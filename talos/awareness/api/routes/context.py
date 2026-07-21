"""Situation, provenance, and capability endpoints (Phase 5).

These back the main agent's context injection and MCP read tools. All reads
are bounded; capability reporting distinguishes available, not-yet-implemented
(``search_memory`` arrives in Phase 6), and degraded tools truthfully.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Request

from talos.awareness.context.broker import SituationBroker
from talos.awareness.db.models import Alert, AlertEvent, Event

router = APIRouter()


@router.get("/situation")
async def get_situation(
    request: Request,
    budget_tokens: int | None = Query(None, ge=50),
    entity_id: str | None = Query(None),
) -> dict:
    broker = SituationBroker(request.app.state.engine, request.app.state.settings)
    try:
        return await broker.build(budget_tokens=budget_tokens, entity_id=entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/provenance/{event_id}")
async def get_provenance(event_id: UUID, request: Request) -> dict:
    engine = request.app.state.engine
    async with engine.connect() as connection:
        event = (
            await connection.execute(
                sa.select(
                    Event.event_id,
                    Event.event_type,
                    Event.entity_id,
                    Event.source_id,
                    Event.observed_at,
                    Event.received_at,
                    Event.processed_at,
                    Event.sequence,
                    Event.source_boot_id,
                    Event.correlation_id,
                    Event.causation_id,
                    Event.severity,
                    Event.confidence,
                    Event.provenance,
                ).where(Event.event_id == event_id)
            )
        ).one_or_none()
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        linked_alerts = (
            await connection.execute(
                sa.select(Alert.alert_id, Alert.title, Alert.status, AlertEvent.kind)
                .join(AlertEvent, AlertEvent.alert_id == Alert.alert_id)
                .where(AlertEvent.event_id == event_id)
                .limit(20)
            )
        ).all()
    return {
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "entity_id": event.entity_id,
        "source_id": event.source_id,
        "observed_at": event.observed_at.isoformat() if event.observed_at else None,
        "received_at": event.received_at.isoformat(),
        "processed_at": event.processed_at.isoformat() if event.processed_at else None,
        "sequence": event.sequence,
        "source_boot_id": event.source_boot_id,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "severity": event.severity,
        "confidence": event.confidence,
        "provenance": event.provenance,
        "linked_alerts": [
            {
                "alert_id": str(row.alert_id),
                "title": row.title,
                "status": row.status,
                "kind": row.kind,
            }
            for row in linked_alerts
        ],
    }


@router.get("/capabilities")
async def get_capabilities(request: Request) -> dict:
    """Tool/endpoint inventory with truthful availability (CTX-007)."""
    db_ok = True
    try:
        async with request.app.state.engine.connect() as connection:
            await connection.execute(sa.text("SELECT 1"))
    except Exception:
        db_ok = False
    reads = "available" if db_ok else "degraded: database unreachable"
    if not db_ok:
        action_request = reads
    elif request.app.state.settings.api_token is None:
        action_request = (
            "disabled: TALOS_AWARENESS_API_TOKEN is not configured for "
            "state-changing action routes"
        )
    else:
        action_request = "available"
    return {
        "capabilities": {
            "get_situation": reads,
            "get_current_state": reads,
            "get_recent_events": reads,
            "get_sensor_history": reads,
            "get_active_alerts": reads,
            "get_event_provenance": reads,
            "get_system_health": "available",
            "search_memory": reads + "; semantic (vector) component degrades to full-text while Ollama is unavailable" if db_ok else reads,
            "request_device_action": action_request,
            "get_action_status": reads,
            "set_reminder": reads,
            "list_reminders": reads,
            "cancel_reminder": reads,
        }
    }
