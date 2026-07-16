"""Action request endpoints (Phase 7). Loopback-only; every transition is
durable and queryable. The model never publishes MQTT directly — requests go
through the registered, validated action service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from talos.awareness.db.models import ActionRequest

router = APIRouter()


class ActionRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="llm", max_length=100)
    correlation_id: str | None = Field(default=None, max_length=200)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8, max_length=64)
    actor: str = Field(default="llm", max_length=100)


@router.post("/actions/request")
async def request_action(body: ActionRequestBody, request: Request) -> dict:
    service = request.app.state.action_service
    return await service.request(
        action_name=body.action,
        parameters=body.parameters,
        actor=body.actor,
        correlation_id=body.correlation_id,
    )


@router.post("/actions/{action_request_id}/confirm")
async def confirm_action(
    action_request_id: UUID, body: ConfirmBody, request: Request
) -> dict:
    service = request.app.state.action_service
    return await service.confirm(action_request_id, token=body.token, actor=body.actor)


@router.post("/actions/{action_request_id}/cancel")
async def cancel_action(
    action_request_id: UUID, request: Request, actor: str = Query("operator")
) -> dict:
    service = request.app.state.action_service
    return await service.cancel(action_request_id, actor=actor)


@router.get("/actions/{action_request_id}")
async def get_action(action_request_id: UUID, request: Request) -> dict:
    result = await request.app.state.action_service.get(action_request_id)
    if result is None:
        raise HTTPException(status_code=404, detail="action request not found")
    return result


@router.get("/actions")
async def list_actions(
    request: Request,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    engine = request.app.state.engine
    statement = (
        sa.select(
            ActionRequest.action_request_id,
            ActionRequest.action_name,
            ActionRequest.status,
            ActionRequest.actor,
            ActionRequest.created_at,
            ActionRequest.error,
        )
        .order_by(ActionRequest.created_at.desc())
        .limit(limit)
    )
    if status is not None:
        statement = statement.where(ActionRequest.status == status)
    async with engine.connect() as connection:
        rows = (await connection.execute(statement)).all()
    registry = request.app.state.action_service.registry
    return {
        "supported_actions": registry.names(),
        "requests": [
            {
                "action_request_id": str(row.action_request_id),
                "action_name": row.action_name,
                "status": row.status,
                "actor": row.actor,
                "created_at": row.created_at.isoformat(),
                "error": row.error,
            }
            for row in rows
        ],
    }
