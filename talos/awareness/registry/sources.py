"""Source registry access and MQTT topic-ownership checks (C4/C17).

A source may only publish on topics it owns (``sources.allowed_topics``,
exact topics or MQTT-style patterns with ``+`` and trailing ``#``). Messages
on unowned topics are dead-lettered by the pipeline, and a payload claiming a
``source_id`` other than the topic owner's is rejected as spoofing.

The repository keeps a snapshot in memory and refreshes it on a TTL so the
hot ingestion path never queries PostgreSQL per message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.db.models import Source


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT-style topic matching: ``+`` single level, trailing ``#`` multi."""
    if pattern == topic:
        return True
    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")
    for index, part in enumerate(pattern_parts):
        if part == "#":
            return index == len(pattern_parts) - 1
        if index >= len(topic_parts):
            return False
        if part != "+" and part != topic_parts[index]:
            return False
    return len(pattern_parts) == len(topic_parts)


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    source_type: str
    transport: str
    entity_id: str | None
    location_id: str | None
    schema_version: int
    clock_quality: str
    enabled: bool
    allowed_topics: tuple[str, ...]
    last_sequence: int | None
    last_boot_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceRepository:
    def __init__(self, engine: AsyncEngine, *, refresh_seconds: float = 30.0) -> None:
        self._engine = engine
        self._refresh_seconds = refresh_seconds
        self._snapshot: dict[str, SourceRecord] = {}
        self._loaded_at: float = 0.0

    async def refresh(self, *, force: bool = False) -> None:
        if not force and (time.monotonic() - self._loaded_at) < self._refresh_seconds:
            return
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                sa.select(
                    Source.source_id,
                    Source.source_type,
                    Source.transport,
                    Source.entity_id,
                    Source.location_id,
                    Source.schema_version,
                    Source.clock_quality,
                    Source.enabled,
                    Source.allowed_topics,
                    Source.last_sequence,
                    Source.last_boot_id,
                    Source.metadata_json,
                )
            )
            snapshot: dict[str, SourceRecord] = {}
            for row in rows:
                snapshot[row.source_id] = SourceRecord(
                    source_id=row.source_id,
                    source_type=row.source_type,
                    transport=row.transport,
                    entity_id=row.entity_id,
                    location_id=row.location_id,
                    schema_version=row.schema_version,
                    clock_quality=row.clock_quality,
                    enabled=row.enabled,
                    allowed_topics=tuple(row.allowed_topics or ()),
                    last_sequence=row.last_sequence,
                    last_boot_id=row.last_boot_id,
                    metadata=dict(row.metadata_json or {}),
                )
        self._snapshot = snapshot
        self._loaded_at = time.monotonic()

    def match_topic(self, topic: str) -> SourceRecord | None:
        for record in self._snapshot.values():
            for pattern in record.allowed_topics:
                if topic_matches(pattern, topic):
                    return record
        return None

    def get(self, source_id: str) -> SourceRecord | None:
        return self._snapshot.get(source_id)

    @property
    def size(self) -> int:
        return len(self._snapshot)
