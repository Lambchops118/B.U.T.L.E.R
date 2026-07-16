"""Deterministic freshness and source-health worker (C5/C16, Phase 3).

Periodically marks state rows ``stale`` and sources (plus their state rows)
``offline`` when configured deadlines pass, recording state transitions and
source-health history exactly once per status change — restart-safe because
every decision derives from durable rows, and idempotent because a row whose
status already changed no longer matches the update predicate.

Deadlines: per-source ``stale_after_seconds`` / ``offline_after_seconds``
from the registry, falling back to the configured defaults. The anchor is
server receipt time (``received_at`` / ``last_received_at``); untrusted
device clocks never extend freshness. Sources that have never reported stay
``unknown`` rather than becoming ``offline`` — silence since *when* would be
a fabricated fact.

Alert policy belongs to Phase 4: ``alert_hook`` is an interface that receives
each transition and defaults to doing nothing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import CurrentState, Source, SourceHealthHistory, StateTransition
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.state.freshness")

# Receives the tick's open connection so alert effects commit atomically
# with the freshness transition that caused them.
AlertHook = Callable[[AsyncConnection, dict[str, Any]], Awaitable[None]]


async def record_source_health_change(
    connection: AsyncConnection,
    *,
    source_id: str,
    new_status: str,
    previous_status: str | None,
    changed_at: datetime,
    reason: str,
) -> None:
    """Append one source-health history row (used here and by ingestion)."""
    await connection.execute(
        sa.insert(SourceHealthHistory).values(
            source_id=source_id,
            health_status=new_status,
            previous_status=previous_status,
            changed_at=changed_at,
            reason=reason,
        )
    )


class FreshnessWorker:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: AwarenessSettings,
        alert_hook: AlertHook | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._alert_hook = alert_hook
        self._state = "stopped"
        self._last_run_at: str | None = None
        self._last_error: str | None = None
        self._runs = 0
        self._transitions = 0

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "interval_seconds": self._settings.freshness_interval_seconds,
            "last_run_at": self._last_run_at,
            "runs": self._runs,
            "transitions_recorded": self._transitions,
            "last_error": self._last_error,
        }

    async def run(self, stop: asyncio.Event) -> None:
        self._state = "running"
        try:
            while not stop.is_set():
                try:
                    self._transitions += await self.tick()
                    self._last_error = None
                except Exception as exc:  # database outage: truthful, retry next tick
                    self._last_error = str(exc)[:300]
                    logger.exception(
                        "freshness tick failed", extra={"component": "freshness"}
                    )
                self._runs += 1
                self._last_run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._settings.freshness_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._state = "stopped"

    async def tick(self, now: datetime | None = None) -> int:
        """One freshness pass; returns the number of transitions recorded."""
        now = now or datetime.now(timezone.utc)
        transitions = 0
        async with self._engine.begin() as connection:
            transitions += await self._mark_stale_state(connection, now)
            transitions += await self._mark_offline_sources(connection, now)
        return transitions

    async def _mark_stale_state(self, connection: AsyncConnection, now: datetime) -> int:
        default_stale = self._settings.default_stale_after_seconds
        rows = (
            await connection.execute(
                sa.select(
                    CurrentState.entity_id,
                    CurrentState.property_name,
                    CurrentState.value_json,
                    CurrentState.state_status,
                    CurrentState.received_at,
                    Source.stale_after_seconds,
                )
                .join(Source, Source.source_id == CurrentState.source_id, isouter=True)
                .where(CurrentState.state_status.in_(("current", "inferred")))
                .with_for_update(of=CurrentState)
            )
        ).all()

        count = 0
        for row in rows:
            if row.received_at is None:
                continue
            deadline = row.stale_after_seconds or default_stale
            age = (now - row.received_at).total_seconds()
            if age <= deadline:
                continue
            await connection.execute(
                sa.update(CurrentState)
                .where(
                    CurrentState.entity_id == row.entity_id,
                    CurrentState.property_name == row.property_name,
                    CurrentState.state_status == row.state_status,
                )
                .values(state_status="stale")
            )
            await connection.execute(
                sa.insert(StateTransition).values(
                    entity_id=row.entity_id,
                    property_name=row.property_name,
                    occurred_at=now,
                    from_value=row.value_json,
                    to_value=row.value_json,
                    from_status=row.state_status,
                    to_status="stale",
                    reason="stale",
                    metadata_json={"age_seconds": round(age, 3), "deadline_seconds": deadline},
                )
            )
            await self._notify(
                connection,
                {
                    "kind": "state_stale",
                    "entity_id": row.entity_id,
                    "property_name": row.property_name,
                    "age_seconds": age,
                },
            )
            count += 1
        return count

    async def _mark_offline_sources(self, connection: AsyncConnection, now: datetime) -> int:
        default_offline = self._settings.default_offline_after_seconds
        rows = (
            await connection.execute(
                sa.select(
                    Source.source_id,
                    Source.source_type,
                    Source.health_status,
                    Source.last_received_at,
                    Source.offline_after_seconds,
                )
                .where(
                    Source.enabled.is_(True),
                    Source.health_status.notin_(("offline", "misconfigured", "unauthorized", "unknown")),
                    Source.last_received_at.is_not(None),
                )
                .with_for_update()
            )
        ).all()

        count = 0
        for row in rows:
            deadline = row.offline_after_seconds or default_offline
            silence = (now - row.last_received_at).total_seconds()
            if silence <= deadline:
                continue
            await connection.execute(
                sa.update(Source)
                .where(Source.source_id == row.source_id)
                .values(health_status="offline", updated_at=now)
            )
            await record_source_health_change(
                connection,
                source_id=row.source_id,
                new_status="offline",
                previous_status=row.health_status,
                changed_at=now,
                reason=f"silent for {silence:.0f}s (deadline {deadline:.0f}s)",
            )
            count += 1
            count += await self._mark_source_state_offline(connection, row.source_id, now)
            await self._notify(
                connection,
                {
                    "kind": "source_offline",
                    "source_id": row.source_id,
                    "source_type": row.source_type,
                    "silence_seconds": silence,
                },
            )
        return count

    async def _mark_source_state_offline(
        self, connection: AsyncConnection, source_id: str, now: datetime
    ) -> int:
        rows = (
            await connection.execute(
                sa.select(
                    CurrentState.entity_id,
                    CurrentState.property_name,
                    CurrentState.value_json,
                    CurrentState.state_status,
                )
                .where(
                    CurrentState.source_id == source_id,
                    CurrentState.state_status.in_(("current", "inferred", "stale")),
                )
                .with_for_update()
            )
        ).all()
        for row in rows:
            await connection.execute(
                sa.update(CurrentState)
                .where(
                    CurrentState.entity_id == row.entity_id,
                    CurrentState.property_name == row.property_name,
                )
                .values(state_status="offline")
            )
            await connection.execute(
                sa.insert(StateTransition).values(
                    entity_id=row.entity_id,
                    property_name=row.property_name,
                    occurred_at=now,
                    from_value=row.value_json,
                    to_value=row.value_json,
                    from_status=row.state_status,
                    to_status="offline",
                    reason="offline",
                    metadata_json={"source_id": source_id},
                )
            )
        return len(rows)

    async def _notify(
        self, connection: AsyncConnection, transition: dict[str, Any]
    ) -> None:
        if self._alert_hook is None:
            return
        try:
            await self._alert_hook(connection, transition)
        except Exception:
            logger.exception(
                "alert hook failed for %s", transition.get("kind"),
                extra={"component": "freshness"},
            )
