"""Numeric telemetry storage and bounded queries (C6, Phase 3).

Raw points land in the ``measurements`` hypertable inside the ingestion
transaction (one row per accepted telemetry event — trivially bounded at
current volumes, so no separate batching stage yet; revisit against measured
lag if the fleet grows). Aggregate queries read the minute/hour/day
continuous aggregates. Every query requires an explicit time range and a
point limit; unbounded requests are rejected, never silently served.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import Measurement
from talos.awareness.schemas.events import EventEnvelope
from talos.awareness.state.classification import TelemetryPoint

AGGREGATE_VIEWS = {"1m": "measurements_1m", "1h": "measurements_1h", "1d": "measurements_1d"}


class QueryBoundsError(ValueError):
    """The request exceeds configured range/point bounds or is malformed."""


async def insert_measurements(
    connection: AsyncConnection,
    envelope: EventEnvelope,
    points: tuple[TelemetryPoint, ...],
) -> None:
    for point in points:
        await connection.execute(
            pg_insert(Measurement)
            .values(
                time=envelope.observed_at or envelope.received_at,
                entity_id=point.entity_id,
                measurement_name=point.measurement_name,
                source_id=envelope.source_id,
                received_at=envelope.received_at,
                value_double=point.value,
                unit=point.unit,
                quality=point.quality,
                confidence=envelope.confidence,
                source_event_id=envelope.event_id,
            )
            .on_conflict_do_nothing()
        )


def validate_range(
    start: datetime, end: datetime, settings: AwarenessSettings
) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise QueryBoundsError("start and end must be timezone-aware")
    if end <= start:
        raise QueryBoundsError("end must be after start")
    max_seconds = settings.max_query_range_days * 86400
    if (end - start).total_seconds() > max_seconds:
        raise QueryBoundsError(
            f"range exceeds the {settings.max_query_range_days}-day maximum"
        )


async def query_measurements(
    engine: AsyncEngine,
    settings: AwarenessSettings,
    *,
    entity_id: str,
    measurement: str,
    start: datetime,
    end: datetime,
    aggregation: str | None = None,
    max_points: int = 1000,
) -> dict[str, Any]:
    """Bounded raw or aggregate telemetry query with explicit ``as_of``."""
    validate_range(start, end, settings)
    if max_points < 1 or max_points > settings.max_query_points:
        raise QueryBoundsError(
            f"max_points must be between 1 and {settings.max_query_points}"
        )
    if aggregation is not None and aggregation not in AGGREGATE_VIEWS:
        raise QueryBoundsError(
            f"aggregation must be one of {sorted(AGGREGATE_VIEWS)} or omitted"
        )

    as_of = datetime.now(timezone.utc).isoformat(timespec="seconds")
    params: dict[str, Any] = {
        "entity_id": entity_id,
        "measurement": measurement,
        "start": start,
        "end": end,
        "limit": max_points + 1,  # one extra row detects truncation
    }

    if aggregation is None:
        statement = sa.text(
            "SELECT time, value_double, unit, quality, confidence, source_id "
            "FROM measurements "
            "WHERE entity_id = :entity_id AND measurement_name = :measurement "
            "AND time >= :start AND time < :end "
            "ORDER BY time ASC LIMIT :limit"
        )
        keys = ("time", "value", "unit", "quality", "confidence", "source_id")
    else:
        view = AGGREGATE_VIEWS[aggregation]
        statement = sa.text(
            f"SELECT bucket, value_min, value_max, value_avg, sample_count, "
            f"value_stddev, unit FROM {view} "
            "WHERE entity_id = :entity_id AND measurement_name = :measurement "
            "AND bucket >= :start AND bucket < :end "
            "ORDER BY bucket ASC LIMIT :limit"
        )
        keys = ("time", "min", "max", "avg", "count", "stddev", "unit")

    async with engine.connect() as connection:
        rows = (await connection.execute(statement, params)).all()

    truncated = len(rows) > max_points
    points = [
        {key: _jsonable(value) for key, value in zip(keys, row)}
        for row in rows[:max_points]
    ]
    return {
        "entity_id": entity_id,
        "measurement": measurement,
        "aggregation": aggregation or "raw",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "as_of": as_of,
        "truncated": truncated,
        "points": points,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
