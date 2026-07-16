"""Local Ollama embeddings via the outbox (C11, Phase 6).

The handler runs in the outbox worker for ``work_type='embedding'``: it loads
the accepted memory, calls the configured local Ollama model, and stores the
vector plus model/dimension/version/hash metadata. Any failure (Ollama not
installed, model missing, dimension mismatch) raises so the worker retries
with backoff — acceptance and full-text retrieval never depend on this path.
Only selected semantic/episodic memories are embedded; raw telemetry,
heartbeats, and transcripts never enter this corpus (they never become
memories in the first place).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import Memory, MemoryEmbedding
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.memory.embeddings")


class EmbeddingUnavailableError(RuntimeError):
    """Ollama or the embedding model is unreachable; the outbox will retry."""


class OllamaEmbeddingClient:
    def __init__(self, settings: AwarenessSettings) -> None:
        self._url = settings.ollama_host.rstrip("/") + "/api/embeddings"
        self._model = settings.embedding_model
        self._dimension = settings.embedding_dimension

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, text: str) -> list[float]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self._url, json={"model": self._model, "prompt": text}
                )
        except httpx.HTTPError as exc:
            raise EmbeddingUnavailableError(f"ollama unreachable: {exc}") from exc
        if response.status_code != 200:
            raise EmbeddingUnavailableError(
                f"ollama returned {response.status_code}: {response.text[:200]}"
            )
        vector = response.json().get("embedding")
        if not isinstance(vector, list) or not vector:
            raise EmbeddingUnavailableError("ollama returned no embedding")
        if len(vector) != self._dimension:
            raise EmbeddingUnavailableError(
                f"dimension mismatch: model returned {len(vector)}, "
                f"schema expects {self._dimension} — changing model families "
                "requires a migration and re-embedding"
            )
        return [float(value) for value in vector]


class EmbeddingHandler:
    """Outbox handler: idempotent by (memory_id, content_hash)."""

    def __init__(
        self,
        engine: AsyncEngine,
        settings: AwarenessSettings,
        client: OllamaEmbeddingClient | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._client = client or OllamaEmbeddingClient(settings)

    async def __call__(self, payload: dict[str, Any]) -> None:
        memory_id = UUID(payload["memory_id"])
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        Memory.statement, Memory.content_hash, Memory.status
                    ).where(Memory.memory_id == memory_id)
                )
            ).one_or_none()
        if row is None or row.status not in ("active", "accepted"):
            return  # superseded/deleted before embedding: nothing to do
        if row.content_hash != payload.get("content_hash"):
            return  # stale work for re-extracted content

        vector = await self._client.embed(row.statement)
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            await connection.execute(
                pg_insert(MemoryEmbedding)
                .values(
                    memory_id=memory_id,
                    embedding=vector,
                    model=self._client.model,
                    dimension=len(vector),
                    version="1",
                    content_hash=row.content_hash,
                    embedded_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["memory_id"],
                    set_={
                        "embedding": vector,
                        "model": self._client.model,
                        "dimension": len(vector),
                        "content_hash": row.content_hash,
                        "embedded_at": now,
                    },
                )
            )
            await connection.execute(
                sa.update(Memory)
                .where(Memory.memory_id == memory_id)
                .values(
                    embedding_model=self._client.model,
                    embedding_dimension=len(vector),
                    embedding_version="1",
                    embedded_at=now,
                )
            )
