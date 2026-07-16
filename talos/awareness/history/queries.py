"""Bounded event-history and qualified current-state reads (C5/C6, Phase 3).

Every result carries ``as_of`` and enough age/source information for a
caller to distinguish "current" from "last known". State reads also compute
an ``effective_status``: a row the freshness worker has not visited yet, but
whose deadline has passed, is reported stale — reads never present overdue
data as unqualified current.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import CurrentState, Event, Source
from talos.awareness.history.telemetry import QueryBoundsError, validate_range


async def query_events(
    engine: AsyncEngine,
    settings: AwarenessSettings,
    *,
    start: datetime,
    end: datetime,
    entity_id: str | None = None,
    source_id: str | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    validate_range(start, end, settings)
    if limit < 1 or limit > settings.max_event_page_size:
        raise QueryBoundsError(
            f"limit must be between 1 and {settings.max_event_page_size}"
        )

    statement = (
        sa.select(
            Event.event_id,
            Event.event_type,
            Event.entity_id,
            Event.source_id,
            Event.observed_at,
            Event.received_at,
            Event.severity,
            Event.confidence,
            Event.payload,
            Event.provenance,
        )
        .where(Event.received_at >= start, Event.received_at < end)
        .order_by(Event.received_at.desc(), Event.event_id)
        .limit(limit + 1)
    )
    if entity_id is not None:
        statement = statement.where(Event.entity_id == entity_id)
    if source_id is not None:
        statement = statement.where(Event.source_id == source_id)
    if event_type is not None:
        statement = statement.where(Event.event_type == event_type)
    if severity is not None:
        statement = statement.where(Event.severity == severity)

    async with engine.connect() as connection:
        rows = (await connection.execute(statement)).all()

    truncated = len(rows) > limit
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "truncated": truncated,
        "events": [
            {
                "event_id": str(row.event_id),
                "event_type": row.event_type,
                "entity_id": row.entity_id,
                "source_id": row.source_id,
                "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                "received_at": row.received_at.isoformat(),
                "severity": row.severity,
                "confidence": row.confidence,
                "payload": row.payload,
                "provenance": row.provenance,
            }
            for row in rows[:limit]
        ],
    }


async def read_entity_state(
    engine: AsyncEngine,
    settings: AwarenessSettings,
    entity_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    statement = (
        sa.select(
            CurrentState.property_name,
            CurrentState.value_json,
            CurrentState.value_type,
            CurrentState.observed_at,
            CurrentState.received_at,
            CurrentState.updated_at,
            CurrentState.confidence,
            CurrentState.source_id,
            CurrentState.source_event_id,
            CurrentState.state_status,
            CurrentState.authority_rank,
            CurrentState.metadata_json.label("metadata_json"),
            Source.stale_after_seconds,
        )
        .join(Source, Source.source_id == CurrentState.source_id, isouter=True)
        .where(CurrentState.entity_id == entity_id)
        .order_by(CurrentState.property_name)
    )
    async with engine.connect() as connection:
        rows = (await connection.execute(statement)).all()

    properties = []
    for row in rows:
        age = (
            (now - row.received_at).total_seconds() if row.received_at is not None else None
        )
        effective_status = row.state_status
        if row.state_status in ("current", "inferred") and age is not None:
            deadline = row.stale_after_seconds or settings.default_stale_after_seconds
            if age > deadline:
                effective_status = "stale"
        properties.append(
            {
                "property_name": row.property_name,
                "value": (row.value_json or {}).get("value"),
                "value_type": row.value_type,
                "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                "received_at": row.received_at.isoformat() if row.received_at else None,
                "age_seconds": round(age, 3) if age is not None else None,
                "confidence": row.confidence,
                "source_id": row.source_id,
                "source_event_id": (
                    str(row.source_event_id) if row.source_event_id else None
                ),
                "status": effective_status,
                "stored_status": row.state_status,
                "authority_rank": row.authority_rank,
                "conflict": (row.metadata_json or {}).get("conflict"),
            }
        )
    return {
        "entity_id": entity_id,
        "as_of": now.isoformat(timespec="seconds"),
        "properties": properties,
    }
