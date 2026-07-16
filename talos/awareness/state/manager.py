"""Durable current-state authority and update logic (C5, Phase 3).

One active row per ``(entity_id, property_name)``. Every accepted event stays
in immutable history regardless of what happens here; this module only
decides whether the *current* view changes.

Update rules, applied inside the ingestion transaction:

- Comparison time is ``observed_at`` when the source clock is trusted,
  otherwise ``received_at`` (C1 timestamp trust).
- A strictly newer comparison time from equal-or-higher authority replaces
  the value. Newer data from a **lower**-authority source also replaces a
  value that is no longer current (stale/offline/unknown) — last known beats
  nothing — but not a live higher-authority value; the disagreement is
  recorded as a conflict instead.
- An older (delayed/out-of-order) message never replaces newer state.
- Equal comparison time with a different value marks the row
  ``conflicting`` (no invented certainty); the contender is kept in metadata.
- Numeric jitter within the configured deadband updates the value and
  timestamps but records no transition; a move beyond the deadband from the
  last *anchor* value records a transition and moves the anchor (hysteresis).

Deadbands come from the owning source's registry metadata:
``metadata.deadbands = {"<property>": <absolute delta>}``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from talos.awareness.db.models import CurrentState, StateTransition
from talos.awareness.logging_utils import get_logger
from talos.awareness.registry.sources import SourceRecord
from talos.awareness.schemas.events import EventEnvelope
from talos.awareness.state.classification import StateUpdate

logger = get_logger("talos.awareness.state.manager")

_TRUSTED_CLOCKS = {"device_synced", "gateway_stamped"}


def comparison_time(envelope: EventEnvelope, source: SourceRecord) -> datetime:
    """The timestamp used for newness comparison (C1 clock trust)."""
    if envelope.observed_at is not None and source.clock_quality in _TRUSTED_CLOCKS:
        return envelope.observed_at
    return envelope.received_at


def _authority(source: SourceRecord) -> int:
    try:
        return int(source.metadata.get("authority_rank", 0))
    except (TypeError, ValueError):
        return 0


def _deadband(source: SourceRecord, property_name: str) -> float | None:
    deadbands = source.metadata.get("deadbands")
    if not isinstance(deadbands, dict):
        return None
    value = deadbands.get(property_name)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


class StateManager:
    """Applies classified state updates within the caller's transaction."""

    async def apply(
        self,
        connection: AsyncConnection,
        envelope: EventEnvelope,
        updates: tuple[StateUpdate, ...],
        source: SourceRecord,
    ) -> int:
        """Apply state updates for one accepted event; returns transitions written."""
        transitions = 0
        incoming_cmp = comparison_time(envelope, source)
        authority = _authority(source)
        for update in updates:
            transitions += await self._apply_one(
                connection, envelope, update, source, incoming_cmp, authority
            )
        return transitions

    async def _apply_one(
        self,
        connection: AsyncConnection,
        envelope: EventEnvelope,
        update: StateUpdate,
        source: SourceRecord,
        incoming_cmp: datetime,
        authority: int,
    ) -> int:
        now = datetime.now(timezone.utc)
        row = (
            await connection.execute(
                sa.select(
                    CurrentState.value_json,
                    CurrentState.state_status,
                    CurrentState.authority_rank,
                    CurrentState.observed_at,
                    CurrentState.received_at,
                    CurrentState.metadata_json.label("metadata_json"),
                )
                .where(
                    CurrentState.entity_id == update.entity_id,
                    CurrentState.property_name == update.property_name,
                )
                .with_for_update()
            )
        ).one_or_none()

        new_value_json = {"value": update.value}

        if row is None:
            await connection.execute(
                sa.insert(CurrentState).values(
                    entity_id=update.entity_id,
                    property_name=update.property_name,
                    value_json=new_value_json,
                    value_type=update.value_type,
                    observed_at=envelope.observed_at,
                    received_at=envelope.received_at,
                    valid_from=incoming_cmp,
                    confidence=envelope.confidence,
                    source_id=envelope.source_id,
                    source_event_id=envelope.event_id,
                    state_status="current",
                    authority_rank=authority,
                    metadata_json={
                        "comparison_time": incoming_cmp.isoformat(),
                        "transition_anchor": update.value,
                    },
                )
            )
            await self._record_transition(
                connection,
                update,
                occurred_at=incoming_cmp,
                from_value=None,
                to_value=new_value_json,
                from_status=None,
                to_status="current",
                reason="initial",
                source_event_id=envelope.event_id,
            )
            return 1

        existing_cmp = self._existing_comparison_time(row)
        old_value_json = row.value_json
        old_status = row.state_status

        if incoming_cmp < existing_cmp:
            # Delayed/out-of-order: history keeps it; current state does not
            # move backwards regardless of authority.
            return 0

        replaceable = old_status in ("stale", "offline", "unknown", "inferred")
        if incoming_cmp == existing_cmp:
            if new_value_json == old_value_json:
                return 0
            if authority > row.authority_rank:
                pass  # higher authority breaks the tie and replaces below
            else:
                await self._mark_conflicting(connection, row, update, envelope, incoming_cmp)
                return 1 if old_status != "conflicting" else 0
        elif authority < row.authority_rank and not replaceable:
            # Newer but weaker than a live higher-authority value: keep the
            # authoritative value, surface the disagreement.
            if new_value_json != old_value_json:
                await self._mark_conflicting(connection, row, update, envelope, incoming_cmp)
                return 1 if old_status != "conflicting" else 0
            return 0

        # Replacement path. Deadband: numeric jitter around the last anchor
        # updates the row without recording a transition.
        metadata = dict(row.metadata_json or {})
        anchor = metadata.get("transition_anchor")
        deadband = _deadband(source, update.property_name)
        suppressed = (
            deadband is not None
            and old_status == "current"
            and isinstance(update.value, (int, float))
            and not isinstance(update.value, bool)
            and isinstance(anchor, (int, float))
            and not isinstance(anchor, bool)
            and abs(float(update.value) - float(anchor)) < deadband
        )
        if not suppressed:
            metadata["transition_anchor"] = update.value
        metadata["comparison_time"] = incoming_cmp.isoformat()
        metadata.pop("conflict", None)

        await connection.execute(
            sa.update(CurrentState)
            .where(
                CurrentState.entity_id == update.entity_id,
                CurrentState.property_name == update.property_name,
            )
            .values(
                value_json=new_value_json,
                value_type=update.value_type,
                observed_at=envelope.observed_at,
                received_at=envelope.received_at,
                valid_from=incoming_cmp,
                confidence=envelope.confidence,
                source_id=envelope.source_id,
                source_event_id=envelope.event_id,
                state_status="current",
                authority_rank=authority,
                metadata_json=metadata,
                updated_at=now,
            )
        )

        value_changed = new_value_json != old_value_json
        status_changed = old_status != "current"
        if suppressed or (not value_changed and not status_changed):
            return 0

        reason = "update"
        if status_changed and old_status in ("stale", "offline"):
            reason = "recovered"
        await self._record_transition(
            connection,
            update,
            occurred_at=incoming_cmp,
            from_value=old_value_json,
            to_value=new_value_json,
            from_status=old_status,
            to_status="current",
            reason=reason,
            source_event_id=envelope.event_id,
        )
        return 1

    def _existing_comparison_time(self, row: Any) -> datetime:
        metadata = row.metadata_json or {}
        stored = metadata.get("comparison_time")
        if stored:
            try:
                parsed = datetime.fromisoformat(stored)
                if parsed.tzinfo is not None:
                    return parsed
            except ValueError:
                pass
        return row.observed_at or row.received_at or datetime.now(timezone.utc)

    async def _mark_conflicting(
        self,
        connection: AsyncConnection,
        row: Any,
        update: StateUpdate,
        envelope: EventEnvelope,
        incoming_cmp: datetime,
    ) -> None:
        metadata = dict(row.metadata_json or {})
        metadata["conflict"] = {
            "source_id": envelope.source_id,
            "value": update.value,
            "event_id": str(envelope.event_id),
            "comparison_time": incoming_cmp.isoformat(),
        }
        await connection.execute(
            sa.update(CurrentState)
            .where(
                CurrentState.entity_id == update.entity_id,
                CurrentState.property_name == update.property_name,
            )
            .values(state_status="conflicting", metadata_json=metadata)
        )
        if row.state_status != "conflicting":
            await self._record_transition(
                connection,
                update,
                occurred_at=incoming_cmp,
                from_value=row.value_json,
                to_value=row.value_json,  # kept value; contender in metadata
                from_status=row.state_status,
                to_status="conflicting",
                reason="conflict",
                source_event_id=envelope.event_id,
            )

    async def _record_transition(
        self,
        connection: AsyncConnection,
        update: StateUpdate,
        *,
        occurred_at: datetime,
        from_value: dict[str, Any] | None,
        to_value: dict[str, Any] | None,
        from_status: str | None,
        to_status: str,
        reason: str,
        source_event_id: Any,
    ) -> None:
        await connection.execute(
            sa.insert(StateTransition).values(
                entity_id=update.entity_id,
                property_name=update.property_name,
                occurred_at=occurred_at,
                from_value=from_value,
                to_value=to_value,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                source_event_id=source_event_id,
            )
        )
