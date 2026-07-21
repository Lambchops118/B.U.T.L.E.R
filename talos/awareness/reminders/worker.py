"""Deterministic due-time reminder worker.

Polls durable reminders and, when ``due_at`` has passed, raises an attention
item through :class:`AlertService.raise_attention` in the same transaction that
marks the reminder ``fired`` — so a crash mid-fire either commits both or
neither, and a fired reminder can never fire twice (the status flip removes it
from the ``scheduled`` predicate). No LLM is involved in the firing decision.

The raised attention flows through the normal notification egress: with the
default ``voice`` channel the agent phrases and speaks the reminder aloud at
the right moment. Quiet hours defer the spoken delivery of noncritical
reminders exactly as they do for other noncritical attention.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from talos.awareness.alerts.service import AlertService
from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import Reminder
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.reminders")


class ReminderWorker:
    def __init__(
        self,
        engine: AsyncEngine,
        settings: AwarenessSettings,
        alert_service: AlertService,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._alerts = alert_service
        self._state = "stopped"
        self._last_run_at: str | None = None
        self._last_error: str | None = None
        self._runs = 0
        self._fired = 0

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "interval_seconds": self._settings.reminder_interval_seconds,
            "last_run_at": self._last_run_at,
            "runs": self._runs,
            "reminders_fired": self._fired,
            "last_error": self._last_error,
        }

    async def run(self, stop: asyncio.Event) -> None:
        self._state = "running"
        try:
            while not stop.is_set():
                try:
                    self._fired += await self.tick()
                    self._last_error = None
                except Exception as exc:  # database outage: truthful, retry next tick
                    self._last_error = str(exc)[:300]
                    logger.exception(
                        "reminder tick failed", extra={"component": "reminders"}
                    )
                self._runs += 1
                self._last_run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._settings.reminder_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._state = "stopped"

    async def tick(self, now: datetime | None = None) -> int:
        """Fire every reminder whose due time has passed; returns the count."""
        now = now or datetime.now(timezone.utc)
        fired = 0
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        Reminder.reminder_id,
                        Reminder.text,
                        Reminder.entity_id,
                    )
                    .where(Reminder.status == "scheduled", Reminder.due_at <= now)
                    .order_by(Reminder.due_at.asc())
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                await self._fire(connection, row, now)
                fired += 1
        return fired

    async def _fire(self, connection: AsyncConnection, row: Any, now: datetime) -> None:
        attention_id = await self._alerts.raise_attention(
            connection,
            alert_id=None,
            entity_id=row.entity_id,
            severity="notice",
            reason=row.text,
            priority=3,
            interruptibility=self._settings.reminder_interruptibility,
            preferred_channel=self._settings.reminder_channel,
            available_after_seconds=0.0,
            expires_after_seconds=None,
            cooldown_key=None,
            cooldown_seconds=0.0,
            notify=True,
            notification_payload={"severity": "notice", "reason": row.text},
            now=now,
        )
        await connection.execute(
            sa.update(Reminder)
            .where(Reminder.reminder_id == row.reminder_id)
            .values(status="fired", fired_at=now, attention_item_id=attention_id)
        )
