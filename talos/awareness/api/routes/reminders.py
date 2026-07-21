"""Reminder endpoints: create (deterministic write), list, cancel.

Loopback-only like the rest of the API; mutations honor the shared write
token when one is configured. Firing happens in the due-time worker, not here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from talos.awareness.api.auth import require_write_auth
from talos.awareness.reminders.service import ReminderService

router = APIRouter()


def _service(request: Request) -> ReminderService:
    return ReminderService(request.app.state.engine, request.app.state.settings)


class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    due_at: datetime
    entity_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/reminders", dependencies=[Depends(require_write_auth)])
async def create_reminder(body: ReminderCreate, request: Request) -> dict:
    try:
        return await _service(request).create(
            text=body.text,
            due_at=body.due_at,
            created_by="llm",
            entity_id=body.entity_id,
            idempotency_key=body.idempotency_key,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/reminders")
async def list_reminders(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1),
) -> dict:
    try:
        return await _service(request).list(status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reminders/{reminder_id}/cancel", dependencies=[Depends(require_write_auth)])
async def cancel_reminder(reminder_id: UUID, request: Request) -> dict:
    cancelled = await _service(request).cancel(reminder_id)
    if not cancelled:
        raise HTTPException(
            status_code=404, detail="reminder not found or not in a cancellable state"
        )
    return {"ok": True}
