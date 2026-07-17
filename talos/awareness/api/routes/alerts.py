"""Alert lifecycle and outbox endpoints (Phase 4).

The API binds to loopback only (C17); acknowledge/resolve are state-changing
and audited via ``last_updated_at``/``acknowledged_at``/``resolved_at``.
Acknowledgement does not erase an active condition — an acknowledged alert
still deduplicates repeats onto the same incident.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from talos.awareness.api.auth import require_write_auth

from talos.awareness.db.models import Alert, NotificationDelivery
from talos.awareness.outbox.worker import retry_outbox_item

router = APIRouter()


@router.get("/alerts")
async def list_alerts(
    request: Request,
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    engine = request.app.state.engine
    statement = (
        sa.select(
            Alert.alert_id,
            Alert.alert_type,
            Alert.severity,
            Alert.entity_id,
            Alert.title,
            Alert.description,
            Alert.status,
            Alert.opened_at,
            Alert.last_seen_at,
            Alert.occurrence_count,
            Alert.acknowledged_at,
            Alert.resolved_at,
        )
        .order_by(Alert.opened_at.desc())
        .limit(limit)
    )
    if status is not None:
        statement = statement.where(Alert.status == status)
    async with engine.connect() as connection:
        rows = (await connection.execute(statement)).all()
    return {
        "alerts": [
            {
                "alert_id": str(row.alert_id),
                "alert_type": row.alert_type,
                "severity": row.severity,
                "entity_id": row.entity_id,
                "title": row.title,
                "description": row.description,
                "status": row.status,
                "opened_at": row.opened_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
                "occurrence_count": row.occurrence_count,
                "acknowledged_at": (
                    row.acknowledged_at.isoformat() if row.acknowledged_at else None
                ),
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            }
            for row in rows
        ]
    }


@router.get("/alerts/{alert_id}/deliveries")
async def alert_deliveries(alert_id: UUID, request: Request) -> dict:
    engine = request.app.state.engine
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    NotificationDelivery.channel,
                    NotificationDelivery.attempted_at,
                    NotificationDelivery.status,
                    NotificationDelivery.error,
                )
                .where(NotificationDelivery.alert_id == alert_id)
                .order_by(NotificationDelivery.attempted_at)
                .limit(200)
            )
        ).all()
    return {
        "deliveries": [
            {
                "channel": row.channel,
                "attempted_at": row.attempted_at.isoformat(),
                "status": row.status,
                "error": row.error,
            }
            for row in rows
        ]
    }


@router.post("/alerts/{alert_id}/acknowledge", dependencies=[Depends(require_write_auth)])
async def acknowledge_alert(alert_id: UUID, request: Request) -> dict:
    changed = await request.app.state.alert_service.set_status(
        request.app.state.engine, alert_id, "acknowledged"
    )
    if not changed:
        raise HTTPException(status_code=404, detail="alert not found")
    return {"ok": True, "status": "acknowledged"}


@router.post("/alerts/{alert_id}/resolve", dependencies=[Depends(require_write_auth)])
async def resolve_alert(alert_id: UUID, request: Request) -> dict:
    changed = await request.app.state.alert_service.set_status(
        request.app.state.engine, alert_id, "resolved"
    )
    if not changed:
        raise HTTPException(status_code=404, detail="alert not found")
    return {"ok": True, "status": "resolved"}


@router.post("/outbox/{outbox_id}/retry", dependencies=[Depends(require_write_auth)])
async def retry_outbox(outbox_id: int, request: Request) -> dict:
    changed = await retry_outbox_item(request.app.state.engine, outbox_id)
    if not changed:
        raise HTTPException(
            status_code=404, detail="no failed/dead-letter outbox item with that id"
        )
    return {"ok": True}
