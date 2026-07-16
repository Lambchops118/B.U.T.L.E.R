"""Alert incident and attention lifecycle (C8, Phase 4).

One persistent condition maps to one active incident: the partial-unique
``deduplication_key`` (while ``open``/``acknowledged``) makes repeats update
first/last-seen, occurrence count, and evidence instead of opening a second
alert. Acknowledgement and resolution are distinct — acknowledging never
erases an active condition, and automatic resolution only happens on
deterministic evidence (a resolve rule).

Attention items are interruption timing, separate from incident state.
``cooldown_key`` + ``cooldown_seconds`` deduplicate interruptions: within the
window the incident still updates, but no new attention item (and therefore
no new notification) is raised — auditable via ``occurrence_count``.

Quiet hours (``TALOS_AWARENESS_QUIET_HOURS``, e.g. ``22:00-07:00`` local
time) defer *noncritical* attention availability to the end of the window;
critical severity is never deferred or silently dropped.
"""

from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import Alert, AlertEvent, AttentionItem, OutboxItem
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.alerts")

ACTIVE_STATUSES = ("open", "acknowledged")


def parse_quiet_hours(spec: str) -> tuple[dt_time, dt_time] | None:
    spec = (spec or "").strip()
    if not spec:
        return None
    try:
        start_text, end_text = spec.split("-", 1)
        start = dt_time.fromisoformat(start_text.strip())
        end = dt_time.fromisoformat(end_text.strip())
        return start, end
    except ValueError as exc:
        raise ValueError(f"invalid quiet hours {spec!r}; expected 'HH:MM-HH:MM'") from exc


def quiet_hours_deferral(
    now: datetime, quiet: tuple[dt_time, dt_time] | None
) -> datetime | None:
    """Local end-of-quiet-window if ``now`` falls inside it, else None."""
    if quiet is None:
        return None
    start, end = quiet
    local = now.astimezone()
    current = local.time()
    if start <= end:
        inside = start <= current < end
        end_day = local
    else:  # window wraps midnight, e.g. 22:00-07:00
        inside = current >= start or current < end
        end_day = local + timedelta(days=1) if current >= start else local
    if not inside:
        return None
    return end_day.replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0
    ).astimezone(timezone.utc)


class AlertService:
    def __init__(self, settings: AwarenessSettings) -> None:
        self._settings = settings
        self._quiet = parse_quiet_hours(settings.quiet_hours)

    # --- incident lifecycle -------------------------------------------------

    async def open_or_update(
        self,
        connection: AsyncConnection,
        *,
        deduplication_key: str,
        alert_type: str,
        severity: str,
        title: str,
        description: str,
        entity_id: str | None,
        location_id: str | None,
        evidence_event_id: UUID | None,
        recommended_actions: list[str],
        metadata: dict[str, Any],
        now: datetime,
    ) -> tuple[UUID, bool]:
        """Open a new incident or update the active one; returns (id, created)."""
        insert_stmt = (
            pg_insert(Alert)
            .values(
                alert_type=alert_type,
                severity=severity,
                entity_id=entity_id,
                location_id=location_id,
                title=title,
                description=description,
                opened_at=now,
                last_updated_at=now,
                first_seen_at=now,
                last_seen_at=now,
                deduplication_key=deduplication_key,
                recommended_actions=recommended_actions,
                metadata_json=metadata,
            )
            .on_conflict_do_nothing(
                index_elements=["deduplication_key"],
                index_where=sa.text(
                    "status IN ('open', 'acknowledged') AND deduplication_key IS NOT NULL"
                ),
            )
            .returning(Alert.alert_id)
        )
        alert_id = (await connection.execute(insert_stmt)).scalar_one_or_none()
        created = alert_id is not None
        if not created:
            alert_id = (
                await connection.execute(
                    sa.select(Alert.alert_id)
                    .where(
                        Alert.deduplication_key == deduplication_key,
                        Alert.status.in_(ACTIVE_STATUSES),
                    )
                    .with_for_update()
                )
            ).scalar_one()
            await connection.execute(
                sa.update(Alert)
                .where(Alert.alert_id == alert_id)
                .values(
                    occurrence_count=Alert.occurrence_count + 1,
                    last_seen_at=now,
                    last_updated_at=now,
                    severity=severity,
                )
            )
        if evidence_event_id is not None:
            await connection.execute(
                pg_insert(AlertEvent)
                .values(alert_id=alert_id, event_id=evidence_event_id)
                .on_conflict_do_nothing(index_elements=["alert_id", "event_id"])
            )
        return alert_id, created

    async def resolve_by_key(
        self,
        connection: AsyncConnection,
        *,
        deduplication_key: str,
        now: datetime,
        reason: str,
        evidence_event_id: UUID | None = None,
    ) -> int:
        """Deterministic automatic resolution; returns resolved count (0/1)."""
        result = await connection.execute(
            sa.update(Alert)
            .where(
                Alert.deduplication_key == deduplication_key,
                Alert.status.in_(ACTIVE_STATUSES),
            )
            .values(
                status="resolved",
                resolved_at=now,
                last_updated_at=now,
                metadata_json=Alert.metadata_json.op("||")(
                    sa.cast({"resolution": reason}, JSONB)
                ),
            )
            .returning(Alert.alert_id)
        )
        resolved = result.all()
        if resolved and evidence_event_id is not None:
            await connection.execute(
                pg_insert(AlertEvent)
                .values(
                    alert_id=resolved[0].alert_id,
                    event_id=evidence_event_id,
                    kind="resolution",
                )
                .on_conflict_do_nothing(index_elements=["alert_id", "event_id"])
            )
        return len(resolved)

    async def set_status(
        self,
        engine: AsyncEngine,
        alert_id: UUID,
        status: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Operator acknowledge/resolve/suppress via the API."""
        now = now or datetime.now(timezone.utc)
        values: dict[str, Any] = {"status": status, "last_updated_at": now}
        if status == "acknowledged":
            values["acknowledged_at"] = now
        if status == "resolved":
            values["resolved_at"] = now
        async with engine.begin() as connection:
            result = await connection.execute(
                sa.update(Alert).where(Alert.alert_id == alert_id).values(**values)
            )
        return bool(result.rowcount)

    # --- attention and notification intent -----------------------------------

    async def raise_attention(
        self,
        connection: AsyncConnection,
        *,
        alert_id: UUID | None,
        entity_id: str | None,
        severity: str,
        reason: str,
        priority: int,
        interruptibility: str,
        preferred_channel: str | None,
        available_after_seconds: float,
        expires_after_seconds: float | None,
        cooldown_key: str | None,
        cooldown_seconds: float,
        notify: bool,
        notification_payload: dict[str, Any],
        now: datetime,
    ) -> UUID | None:
        """Create an attention item unless its cooldown suppresses it; queue
        the notification outbox work in the same transaction when asked."""
        if cooldown_key and cooldown_seconds > 0:
            recent = (
                await connection.execute(
                    sa.select(AttentionItem.attention_item_id)
                    .where(
                        AttentionItem.cooldown_key == cooldown_key,
                        AttentionItem.created_at
                        > now - timedelta(seconds=cooldown_seconds),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if recent is not None:
                return None

        available_after = now + timedelta(seconds=available_after_seconds)
        if severity != "critical":
            deferred = quiet_hours_deferral(now, self._quiet)
            if deferred is not None and deferred > available_after:
                available_after = deferred

        attention_id = (
            await connection.execute(
                sa.insert(AttentionItem)
                .values(
                    priority=priority,
                    reason=reason,
                    entity_id=entity_id,
                    alert_id=alert_id,
                    created_at=now,
                    available_after=available_after,
                    expires_at=(
                        now + timedelta(seconds=expires_after_seconds)
                        if expires_after_seconds
                        else None
                    ),
                    interruptibility=interruptibility,
                    preferred_channel=preferred_channel,
                    cooldown_key=cooldown_key,
                )
                .returning(AttentionItem.attention_item_id)
            )
        ).scalar_one()

        if notify:
            await connection.execute(
                pg_insert(OutboxItem)
                .values(
                    work_type="notification",
                    aggregate_type="attention_item",
                    aggregate_id=str(attention_id),
                    payload={
                        "attention_item_id": str(attention_id),
                        "alert_id": str(alert_id) if alert_id else None,
                        "channel": preferred_channel,
                        **notification_payload,
                    },
                    idempotency_key=f"notification:{attention_id}",
                    available_at=available_after,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
        return attention_id
