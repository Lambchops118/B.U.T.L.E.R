"""Dead-letter store for rejected messages (C1/C3).

Rejections never crash ingestion and never vanish silently: the raw payload
(bounded), topic, reason, and error detail are persisted for later triage.
Recording failures are logged and swallowed — the dead-letter path must not
take the pipeline down with it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.db.models import DeadLetterEvent
from talos.awareness.logging_utils import get_logger

MAX_RAW_PAYLOAD_CHARS = 8192

logger = get_logger("talos.awareness.ingestion.dead_letter")


class DeadLetterRecorder:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(
        self,
        *,
        received_at: datetime,
        transport: str,
        topic: str | None,
        reason: str,
        raw_payload: bytes | str | None,
        error_detail: str | None = None,
        source_hint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if isinstance(raw_payload, bytes):
            raw_text = raw_payload.decode("utf-8", errors="replace")
        else:
            raw_text = raw_payload
        if raw_text is not None and len(raw_text) > MAX_RAW_PAYLOAD_CHARS:
            raw_text = raw_text[:MAX_RAW_PAYLOAD_CHARS] + "…[truncated]"

        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    DeadLetterEvent.__table__.insert().values(
                        received_at=received_at,
                        transport=transport,
                        topic_or_endpoint=topic,
                        reason=reason,
                        raw_payload=raw_text,
                        error_detail=error_detail,
                        source_hint=source_hint,
                        metadata=metadata or {},
                    )
                )
            logger.warning(
                "dead-lettered message: %s (%s)",
                reason,
                error_detail or "no detail",
                extra={"component": "ingestion", "source_id": source_hint},
            )
        except Exception:
            logger.exception(
                "failed to persist dead-letter record", extra={"component": "ingestion"}
            )
