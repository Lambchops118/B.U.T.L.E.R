"""Reminder write/read/cancel operations (loopback API surface).

Deterministic storage only: the service validates that ``due_at`` is a
timezone-aware future instant and persists it. Parsing "7pm" into that instant
is the LLM's job at creation time; the backend just stores and later fires it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import Reminder

_LISTABLE_STATUSES = ("scheduled", "fired", "cancelled", "expired")


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "reminder_id": str(row.reminder_id),
        "text": row.text,
        "due_at": row.due_at.astimezone(timezone.utc).isoformat(),
        "status": row.status,
        "created_by": row.created_by,
        "entity_id": row.entity_id,
        "created_at": row.created_at.astimezone(timezone.utc).isoformat(),
        "fired_at": row.fired_at.astimezone(timezone.utc).isoformat() if row.fired_at else None,
        "cancelled_at": (
            row.cancelled_at.astimezone(timezone.utc).isoformat() if row.cancelled_at else None
        ),
        "attention_item_id": str(row.attention_item_id) if row.attention_item_id else None,
    }


class ReminderService:
    def __init__(self, engine: AsyncEngine, settings: AwarenessSettings) -> None:
        self._engine = engine
        self._settings = settings

    async def create(
        self,
        *,
        text: str,
        due_at: datetime,
        created_by: str | None = None,
        entity_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("reminder text must not be empty")
        if due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware (include an offset, e.g. -04:00)")
        due_at = due_at.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if due_at <= now:
            raise ValueError(f"due_at must be in the future (now is {now.isoformat()})")

        insert_stmt = (
            pg_insert(Reminder)
            .values(
                text=text,
                due_at=due_at,
                created_by=created_by,
                entity_id=entity_id,
                idempotency_key=idempotency_key,
                metadata_json=metadata or {},
            )
            .returning(Reminder.reminder_id)
        )
        if idempotency_key:
            insert_stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=["idempotency_key"]
            )

        async with self._engine.begin() as connection:
            reminder_id = (await connection.execute(insert_stmt)).scalar_one_or_none()
            if reminder_id is None and idempotency_key:
                reminder_id = (
                    await connection.execute(
                        sa.select(Reminder.reminder_id).where(
                            Reminder.idempotency_key == idempotency_key
                        )
                    )
                ).scalar_one()
            row = (
                await connection.execute(
                    sa.select(Reminder).where(Reminder.reminder_id == reminder_id)
                )
            ).one()
        return _row_to_dict(row)

    async def list(
        self, *, status: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        query = sa.select(Reminder)
        if status is not None:
            if status not in _LISTABLE_STATUSES:
                raise ValueError(f"status must be one of {_LISTABLE_STATUSES}")
            query = query.where(Reminder.status == status)
        query = query.order_by(Reminder.due_at.asc()).limit(limit)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(query)).all()
        return {"reminders": [_row_to_dict(r) for r in rows], "count": len(rows)}

    async def cancel(self, reminder_id: UUID) -> bool:
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                sa.update(Reminder)
                .where(Reminder.reminder_id == reminder_id, Reminder.status == "scheduled")
                .values(status="cancelled", cancelled_at=now)
            )
        return bool(result.rowcount)
