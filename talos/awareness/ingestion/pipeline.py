"""C3 — deterministic, idempotent ingestion pipeline.

Stage order per accepted message:

    receive → source authorization (topic ownership) → size check →
    parse/normalize (C1 envelope) → sequence/boot evaluation →
    transactional persistence (event insert + source registry update in ONE
    transaction) → metrics

Idempotency: the ``events`` primary key and the partial unique index on
``(source_id, source_boot_id, sequence)`` make duplicate handling a database
guarantee, not just an application check. Handling the same message twice
updates liveness counters but stores nothing new. No network or LLM calls
happen inside the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import Event, Source
from talos.awareness.ingestion.dead_letter import DeadLetterRecorder
from talos.awareness.ingestion.normalization import NormalizationError, normalize
from talos.awareness.ingestion.sequence import assess_sequence
from talos.awareness.logging_utils import get_logger
from talos.awareness.registry.sources import SourceRepository
from talos.awareness.schemas.events import EventEnvelope, PayloadTooLargeError

logger = get_logger("talos.awareness.ingestion.pipeline")


@dataclass(frozen=True)
class InboundMessage:
    topic: str
    payload: bytes
    retained: bool = False
    transport: str = "mqtt"


@dataclass
class IngestionMetrics:
    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    sequence_gaps: int = 0
    boot_resets: int = 0
    retained_messages: int = 0
    dead_lettered: dict[str, int] = field(default_factory=dict)
    last_message_at: str | None = None

    def record_dead_letter(self, reason: str) -> None:
        self.dead_lettered[reason] = self.dead_lettered.get(reason, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "sequence_gaps": self.sequence_gaps,
            "boot_resets": self.boot_resets,
            "retained_messages": self.retained_messages,
            "dead_lettered": dict(self.dead_lettered),
            "last_message_at": self.last_message_at,
        }


class IngestionPipeline:
    def __init__(
        self,
        engine: AsyncEngine,
        sources: SourceRepository,
        settings: AwarenessSettings,
        metrics: IngestionMetrics | None = None,
    ) -> None:
        self._engine = engine
        self._sources = sources
        self._settings = settings
        self._dead_letters = DeadLetterRecorder(engine)
        self.metrics = metrics or IngestionMetrics()

    async def handle(self, message: InboundMessage) -> str:
        """Process one message; returns a disposition string (for tests/logs).

        Never raises: every failure path either dead-letters or logs.
        """
        received_at = datetime.now(timezone.utc)
        self.metrics.received += 1
        self.metrics.last_message_at = received_at.isoformat(timespec="seconds")

        try:
            return await self._handle_inner(message, received_at)
        except Exception as exc:
            logger.exception(
                "unexpected ingestion failure for topic %s",
                message.topic,
                extra={"component": "ingestion"},
            )
            await self._reject(message, received_at, "internal_error", str(exc), None)
            return "dead_letter:internal_error"

    async def _handle_inner(self, message: InboundMessage, received_at: datetime) -> str:
        await self._sources.refresh()

        source = self._sources.match_topic(message.topic)
        if source is None:
            await self._reject(
                message,
                received_at,
                "unauthorized_topic",
                f"no registered source owns topic {message.topic!r}",
                None,
            )
            return "dead_letter:unauthorized_topic"
        if not source.enabled:
            await self._reject(
                message, received_at, "source_disabled", None, source.source_id
            )
            return "dead_letter:source_disabled"

        if len(message.payload) > self._settings.max_event_payload_bytes:
            await self._reject(
                message,
                received_at,
                "oversized",
                f"{len(message.payload)} bytes > {self._settings.max_event_payload_bytes}",
                source.source_id,
            )
            return "dead_letter:oversized"

        try:
            envelope = normalize(
                topic=message.topic,
                payload=message.payload,
                retained=message.retained,
                transport=message.transport,
                source=source,
                received_at=received_at,
            )
            envelope.ensure_payload_within(self._settings.max_event_payload_bytes)
        except NormalizationError as exc:
            await self._reject(message, received_at, exc.reason, exc.detail, source.source_id)
            return f"dead_letter:{exc.reason}"
        except PayloadTooLargeError as exc:
            await self._reject(message, received_at, "oversized", str(exc), source.source_id)
            return "dead_letter:oversized"

        assessment = assess_sequence(
            source.last_sequence, source.last_boot_id, envelope.sequence, envelope.source_boot_id
        )
        arrival_notes = assessment.arrival_notes()
        if message.retained:
            arrival_notes["retained"] = True
        if arrival_notes:
            envelope.provenance.metadata["arrival"] = arrival_notes

        if assessment.treat_as_duplicate:
            await self._touch_source(source.source_id, received_at)
            self.metrics.duplicates += 1
            return "duplicate"

        disposition = await self._persist(envelope, received_at, advance=assessment.advance)
        if disposition == "duplicate":
            self.metrics.duplicates += 1
            await self._touch_source(source.source_id, received_at)
            return "duplicate"

        self.metrics.accepted += 1
        if assessment.out_of_order:
            self.metrics.out_of_order += 1
        if assessment.gap_before:
            self.metrics.sequence_gaps += 1
        if assessment.boot_reset:
            self.metrics.boot_resets += 1
        if message.retained:
            self.metrics.retained_messages += 1
        logger.info(
            "stored event %s (%s)",
            envelope.event_type,
            "retained" if message.retained else "live",
            extra={
                "component": "ingestion",
                "event_id": envelope.event_id,
                "source_id": envelope.source_id,
                "entity_id": envelope.entity_id,
            },
        )
        return "accepted"

    async def _persist(
        self, envelope: EventEnvelope, received_at: datetime, *, advance: bool
    ) -> str:
        insert_stmt = (
            pg_insert(Event)
            .values(
                event_id=envelope.event_id,
                schema_version=envelope.schema_version,
                event_type=envelope.event_type,
                entity_id=envelope.entity_id,
                source_id=envelope.source_id,
                location_id=envelope.location_id,
                observed_at=envelope.observed_at,
                received_at=envelope.received_at,
                processed_at=datetime.now(timezone.utc),
                sequence=envelope.sequence,
                source_boot_id=envelope.source_boot_id,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                severity=envelope.severity,
                confidence=envelope.confidence,
                retention_class=envelope.retention_class,
                expires_at=envelope.expires_at,
                payload=envelope.payload,
                provenance=envelope.provenance.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
        )

        source_update = {
            "last_received_at": received_at,
            "health_status": "healthy",
            "updated_at": datetime.now(timezone.utc),
        }
        if envelope.observed_at is not None:
            source_update["last_observed_at"] = envelope.observed_at
        if advance and envelope.sequence is not None:
            source_update["last_sequence"] = envelope.sequence
            source_update["last_boot_id"] = envelope.source_boot_id

        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(insert_stmt)
                inserted = bool(result.rowcount)
                if inserted:
                    await connection.execute(
                        sa.update(Source)
                        .where(Source.source_id == envelope.source_id)
                        .values(**source_update)
                    )
        except IntegrityError:
            # Partial unique (source_id, source_boot_id, sequence): redelivery
            # of the same device message under a different event_id.
            return "duplicate"

        return "accepted" if inserted else "duplicate"

    async def _touch_source(self, source_id: str, received_at: datetime) -> None:
        """A duplicate still proves the source is alive."""
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.update(Source)
                .where(Source.source_id == source_id)
                .values(last_received_at=received_at, updated_at=datetime.now(timezone.utc))
            )

    async def _reject(
        self,
        message: InboundMessage,
        received_at: datetime,
        reason: str,
        detail: str | None,
        source_hint: str | None,
    ) -> None:
        self.metrics.record_dead_letter(reason)
        await self._dead_letters.record(
            received_at=received_at,
            transport=message.transport,
            topic=message.topic,
            reason=reason,
            raw_payload=message.payload,
            error_detail=detail,
            source_hint=source_hint,
            metadata={"retained": message.retained} if message.retained else {},
        )
