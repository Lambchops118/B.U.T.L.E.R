"""Bounded read endpoints for state, events, and telemetry (Phase 3).

These are repository-native structured reads for later phases' rules,
context, and tools — never model prose. Every response carries ``as_of`` and
qualification data; every history query requires an explicit time range and
respects configured range/point/page bounds (INV-03, INV-17).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from talos.awareness.history.queries import query_events, read_entity_state
from talos.awareness.history.telemetry import QueryBoundsError, query_measurements

router = APIRouter()


def _context(request: Request):
    return request.app.state.engine, request.app.state.settings


@router.get("/state/{entity_id}")
async def get_state(entity_id: str, request: Request) -> dict:
    engine, settings = _context(request)
    return await read_entity_state(engine, settings, entity_id)


@router.get("/events")
async def get_events(
    request: Request,
    start: datetime = Query(...),
    end: datetime = Query(...),
    entity_id: str | None = None,
    source_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = Query(100, ge=1),
) -> dict:
    engine, settings = _context(request)
    try:
        return await query_events(
            engine,
            settings,
            start=start,
            end=end,
            entity_id=entity_id,
            source_id=source_id,
            event_type=event_type,
            severity=severity,
            limit=limit,
        )
    except QueryBoundsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/telemetry/{entity_id}/{measurement}")
async def get_telemetry(
    entity_id: str,
    measurement: str,
    request: Request,
    start: datetime = Query(...),
    end: datetime = Query(...),
    aggregation: str | None = Query(None, description="raw when omitted; else 1m|1h|1d"),
    max_points: int = Query(1000, ge=1),
) -> dict:
    engine, settings = _context(request)
    try:
        return await query_measurements(
            engine,
            settings,
            entity_id=entity_id,
            measurement=measurement,
            start=start,
            end=end,
            aggregation=aggregation,
            max_points=max_points,
        )
    except QueryBoundsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
