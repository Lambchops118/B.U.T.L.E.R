"""Action request endpoints (Phase 7). Default-loopback and bearer-gated;
every transition is durable and queryable. The model never publishes MQTT
directly — requests go through the registered, validated action service."""

from __future__ import annotations

import secrets
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from talos.awareness.db.models import ActionRequest

router = APIRouter()


class ActionRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="llm", min_length=1, max_length=100)
    correlation_id: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8, max_length=64)
    actor: str = Field(default="llm", min_length=1, max_length=100)


async def require_action_auth(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """Fail closed for physical-action mutations.

    The API remains loopback-bound by default, but loopback is not actor
    authentication. A configured bearer token is therefore mandatory for
    request/confirm/cancel; Phase 8 may extend this shared service identity
    into per-actor credentials without weakening this boundary.
    """
    configured = request.app.state.settings.api_token
    if configured is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "physical action API is disabled until "
                "TALOS_AWARENESS_API_TOKEN is configured"
            ),
        )
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        supplied, configured.get_secret_value()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@router.post("/actions/request", dependencies=[Depends(require_action_auth)])
async def request_action(body: ActionRequestBody, request: Request) -> dict:
    service = request.app.state.action_service
    return await service.request(
        action_name=body.action,
        parameters=body.parameters,
        actor=body.actor,
        correlation_id=body.correlation_id,
        idempotency_key=body.idempotency_key,
    )


@router.post(
    "/actions/{action_request_id}/confirm",
    dependencies=[Depends(require_action_auth)],
)
async def confirm_action(
    action_request_id: UUID, body: ConfirmBody, request: Request
) -> dict:
    service = request.app.state.action_service
    return await service.confirm(action_request_id, token=body.token, actor=body.actor)


@router.post(
    "/actions/{action_request_id}/cancel",
    dependencies=[Depends(require_action_auth)],
)
async def cancel_action(
    action_request_id: UUID,
    request: Request,
    actor: str = Query("operator", min_length=1, max_length=100),
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
