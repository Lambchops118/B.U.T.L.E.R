"""Outbox handler that delivers one queued notification (C9/C10, Phase 4).

Renders deterministic fallback wording from the alert row at send time (so
occurrence counts are fresh and no LLM is ever needed), tries the preferred
channel then the remaining channels as fallback, and persists every attempt
in ``notification_deliveries``. Raises when no channel confirms so the outbox
worker retries with backoff — the alert stays open and nothing is marked
delivered without adapter confirmation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.db.models import Alert, AttentionItem, NotificationDelivery
from talos.awareness.logging_utils import get_logger
from talos.awareness.notifications.base import NotificationContent

logger = get_logger("talos.awareness.notifications")


def render_fallback(
    *,
    severity: str,
    title: str,
    description: str | None,
    occurrence_count: int,
    first_seen_at: datetime | None,
    reason: str | None,
) -> NotificationContent:
    """Deterministic wording from validated fields only (NOTIFY-002)."""
    body_parts = []
    if description:
        body_parts.append(description)
    elif reason:
        body_parts.append(reason)
    if occurrence_count > 1:
        body_parts.append(f"Occurred {occurrence_count} times.")
    if first_seen_at is not None:
        body_parts.append(
            f"First seen {first_seen_at.astimezone(timezone.utc).isoformat(timespec='seconds')}."
        )
    return NotificationContent(
        title=f"[{severity.upper()}] {title}",
        body=" ".join(body_parts) or title,
        severity=severity,
    )


class NotificationHandler:
    """Registered with the outbox worker for ``work_type='notification'``."""

    def __init__(self, engine: AsyncEngine, adapters: dict[str, Any]) -> None:
        self._engine = engine
        self._adapters = adapters

    async def __call__(self, payload: dict[str, Any]) -> None:
        attention_item_id = payload.get("attention_item_id")
        alert_id = payload.get("alert_id")
        severity = str(payload.get("severity", "notice"))
        reason = payload.get("reason")

        content = await self._render(alert_id, severity, reason)

        preferred = payload.get("channel")
        order = [name for name in (preferred,) if name and name in self._adapters]
        order += [name for name in self._adapters if name not in order]
        if not order:
            raise RuntimeError("no notification channels configured")

        errors: list[str] = []
        for channel in order:
            adapter = self._adapters[channel]
            result = await adapter.send(content)
            await self._record_attempt(
                attention_item_id=attention_item_id,
                alert_id=alert_id,
                channel=channel,
                confirmed=result.confirmed,
                detail=result.detail,
                provider_message_id=result.provider_message_id,
                fallback=channel != (preferred or channel),
            )
            if result.confirmed:
                if attention_item_id:
                    await self._mark_attention_delivered(attention_item_id)
                return
            errors.append(f"{channel}: {result.detail}")

        raise RuntimeError("all channels failed — " + "; ".join(errors)[:300])

    async def _render(
        self, alert_id: str | None, severity: str, reason: str | None
    ) -> NotificationContent:
        if alert_id:
            async with self._engine.connect() as connection:
                row = (
                    await connection.execute(
                        sa.select(
                            Alert.severity,
                            Alert.title,
                            Alert.description,
                            Alert.occurrence_count,
                            Alert.first_seen_at,
                        ).where(Alert.alert_id == UUID(alert_id))
                    )
                ).one_or_none()
            if row is not None:
                return render_fallback(
                    severity=row.severity,
                    title=row.title,
                    description=row.description,
                    occurrence_count=row.occurrence_count,
                    first_seen_at=row.first_seen_at,
                    reason=reason,
                )
        # Missing alert row must not break the notification (NOTIFY-002).
        return render_fallback(
            severity=severity,
            title=reason or "Attention required",
            description=None,
            occurrence_count=1,
            first_seen_at=None,
            reason=reason,
        )

    async def _record_attempt(
        self,
        *,
        attention_item_id: str | None,
        alert_id: str | None,
        channel: str,
        confirmed: bool,
        detail: str,
        provider_message_id: str | None,
        fallback: bool,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.insert(NotificationDelivery).values(
                    alert_id=UUID(alert_id) if alert_id else None,
                    attention_item_id=(
                        UUID(attention_item_id) if attention_item_id else None
                    ),
                    channel=channel,
                    attempted_at=datetime.now(timezone.utc),
                    status="delivered" if confirmed else "failed",
                    error=None if confirmed else detail[:500],
                    provider_message_id=provider_message_id,
                    metadata_json={"fallback": fallback} if fallback else {},
                )
            )

    async def _mark_attention_delivered(self, attention_item_id: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.update(AttentionItem)
                .where(AttentionItem.attention_item_id == UUID(attention_item_id))
                .values(delivery_status="delivered")
            )
