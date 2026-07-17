"""Retention service (C15, Phase 8): dry-run plans, bounded resumable
deletion, aggregate-before-delete, and hard evidence protection.

Policies (each a configurable age; 0 disables):

- ``raw_measurements`` — raw telemetry older than the cutoff, ONLY after the
  minute/hour/day continuous aggregates are refreshed through the cutoff
  (the required aggregates are produced as part of execution, not assumed).
- ``heartbeat_events`` — events with ``retention_class='heartbeat'``; any
  event referenced by ``alert_events`` is excluded (and the database's
  RESTRICT constraint would refuse it anyway — unresolved-alert evidence is
  protected twice).
- ``dead_letters``, ``completed_outbox``, ``state_transitions``,
  ``source_health_history`` — operational history past its window.
- ``resolved_alerts`` — resolved/expired incidents; open/acknowledged ones
  are never eligible.
- ``discarded_memories`` — only ``rejected``/``deleted`` memory rows.
  Active and superseded memories (and therefore their provenance chains)
  are never deleted by retention: supersession history is the point.

Execution deletes in bounded batches, each in its own transaction — a crash
mid-run loses nothing and a re-run resumes idempotently (eligibility is
recomputed from durable rows). Every batch is logged; the run report returns
exact per-policy counts. Retention never mutates rows outside these policies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.retention")

AGGREGATE_VIEWS = ("measurements_1m", "measurements_1h", "measurements_1d")


@dataclass(frozen=True)
class Policy:
    name: str
    table: str
    days: int
    where_sql: str  # eligibility predicate; :cutoff bound
    id_column: str
    protection_note: str = ""


def _policies(settings: AwarenessSettings) -> list[Policy]:
    return [
        Policy(
            name="raw_measurements",
            table="measurements",
            days=settings.retention_raw_measurements_days,
            where_sql="time < :cutoff",
            id_column="ctid",
            protection_note="deleted only after 1m/1h/1d aggregates are refreshed through the cutoff",
        ),
        Policy(
            name="heartbeat_events",
            table="events",
            days=settings.retention_heartbeat_events_days,
            where_sql=(
                "retention_class = 'heartbeat' AND received_at < :cutoff "
                "AND NOT EXISTS (SELECT 1 FROM alert_events ae WHERE ae.event_id = events.event_id)"
            ),
            id_column="event_id",
            protection_note="alert evidence excluded (also enforced by ON DELETE RESTRICT)",
        ),
        Policy(
            name="dead_letters",
            table="dead_letter_events",
            days=settings.retention_dead_letter_days,
            where_sql="received_at < :cutoff",
            id_column="id",
        ),
        Policy(
            name="completed_outbox",
            table="outbox",
            days=settings.retention_completed_outbox_days,
            where_sql="status = 'completed' AND completed_at < :cutoff",
            id_column="outbox_id",
            protection_note="pending/failed/dead_letter work is never deleted",
        ),
        Policy(
            name="resolved_alerts",
            table="alerts",
            days=settings.retention_resolved_alerts_days,
            where_sql="status IN ('resolved', 'expired') AND last_updated_at < :cutoff",
            id_column="alert_id",
            protection_note="open/acknowledged incidents are never eligible",
        ),
        Policy(
            name="state_transitions",
            table="state_transitions",
            days=settings.retention_state_transitions_days,
            where_sql="occurred_at < :cutoff",
            id_column="id",
        ),
        Policy(
            name="source_health_history",
            table="source_health_history",
            days=settings.retention_source_health_history_days,
            where_sql="changed_at < :cutoff",
            id_column="id",
        ),
        Policy(
            name="discarded_memories",
            table="memories",
            days=settings.retention_discarded_memories_days,
            where_sql="status IN ('rejected', 'deleted') AND updated_at < :cutoff",
            id_column="memory_id",
            protection_note="active/superseded memories and their provenance are never deleted",
        ),
    ]


class RetentionService:
    def __init__(self, engine: AsyncEngine, settings: AwarenessSettings) -> None:
        self._engine = engine
        self._settings = settings

    async def plan(self, now: datetime | None = None) -> dict[str, Any]:
        """Dry run: exact eligible counts, cutoffs, and protections. No writes."""
        now = now or datetime.now(timezone.utc)
        entries = []
        async with self._engine.connect() as connection:
            for policy in _policies(self._settings):
                if policy.days <= 0:
                    entries.append(
                        {"policy": policy.name, "enabled": False, "reason": "disabled (0 days)"}
                    )
                    continue
                cutoff = now - timedelta(days=policy.days)
                count = (
                    await connection.execute(
                        sa.text(
                            f"SELECT count(*) FROM {policy.table} WHERE {policy.where_sql}"
                        ),
                        {"cutoff": cutoff},
                    )
                ).scalar_one()
                entries.append(
                    {
                        "policy": policy.name,
                        "enabled": True,
                        "table": policy.table,
                        "cutoff": cutoff.isoformat(timespec="seconds"),
                        "eligible": count,
                        "protection": policy.protection_note or None,
                    }
                )
        return {
            "as_of": now.isoformat(timespec="seconds"),
            "dry_run": True,
            "batch_size": self._settings.retention_batch_size,
            "policies": entries,
        }

    async def execute(self, now: datetime | None = None) -> dict[str, Any]:
        """Bounded, resumable, idempotent deletion per enabled policy."""
        now = now or datetime.now(timezone.utc)
        report = await self.plan(now)
        report["dry_run"] = False
        for entry in report["policies"]:
            if not entry.get("enabled") or not entry.get("eligible"):
                entry["deleted"] = 0
                continue
            policy = next(p for p in _policies(self._settings) if p.name == entry["policy"])
            cutoff = now - timedelta(days=policy.days)
            if policy.name == "raw_measurements":
                await self._refresh_aggregates(cutoff)
                entry["aggregates_refreshed_through"] = cutoff.isoformat(timespec="seconds")
            entry["deleted"] = await self._delete_batched(policy, cutoff)
        return report

    async def _refresh_aggregates(self, cutoff: datetime) -> None:
        """Produce the required aggregates BEFORE any raw deletion."""
        async with self._engine.connect() as connection:
            autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
            for view in AGGREGATE_VIEWS:
                await autocommit.execute(
                    sa.text(
                        f"CALL refresh_continuous_aggregate('{view}', NULL, "
                        f"CAST(:cutoff AS timestamptz))"
                    ),
                    {"cutoff": cutoff},
                )

    async def _delete_batched(self, policy: Policy, cutoff: datetime) -> int:
        deleted = 0
        batch = self._settings.retention_batch_size
        while True:
            # One bounded batch per transaction: crash-safe and resumable.
            async with self._engine.begin() as connection:
                result = await connection.execute(
                    sa.text(
                        f"DELETE FROM {policy.table} WHERE {policy.id_column} IN ("
                        f"SELECT {policy.id_column} FROM {policy.table} "
                        f"WHERE {policy.where_sql} LIMIT :batch)"
                    ),
                    {"cutoff": cutoff, "batch": batch},
                )
            deleted += result.rowcount
            logger.info(
                "retention %s: deleted batch of %d (total %d)",
                policy.name,
                result.rowcount,
                deleted,
                extra={"component": "retention"},
            )
            if result.rowcount < batch:
                return deleted
