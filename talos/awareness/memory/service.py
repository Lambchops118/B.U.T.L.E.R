"""Memory manager: deterministic writes, validated candidates, hybrid search.

Write paths (C11):

1. ``write_deterministic`` — unambiguous, policy-permitted facts (explicit
   user statements, recorded incidents). Created ``active`` immediately.
2. ``propose_candidate`` — a model may only propose a strict structured
   candidate. The manager validates the schema and every piece of referenced
   evidence, rejects unsupported claims (the rejected row and reason are
   kept for audit), detects duplicates (idempotent by content hash),
   handles contradictions, and records the decision with model/prompt info.

Changed facts are never overwritten: the old row's validity closes, it is
marked ``superseded`` and linked from the replacement. Inconclusive conflict
keeps both rows active with a ``conflicts_with`` relation. Explicit
user-confirmed evidence outweighs weak inference.

Retrieval is hybrid: full-text (PostgreSQL ``tsvector``) + vector cosine
(when an embedding exists and a query embedding is obtainable) + recency +
importance + confidence, with component scores exposed for debugging.
Embedding work is queued through the outbox and its outage never blocks
acceptance or full-text search. Access counters never touch validity.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import (
    Alert,
    Event,
    Memory,
    MemoryEmbedding,
    MemoryProvenance,
    MemoryRelationship,
    OutboxItem,
)
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.memory")

_SENSITIVITY_ORDER = {"normal": 0, "personal": 1, "sensitive": 2, "restricted": 3}


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "event", "alert", "message", "conversation", "source",
        "user_confirmation", "extraction_job", "model",
    ]
    reference: str = Field(min_length=1, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateProposal(BaseModel):
    """Strict schema for model-proposed memory candidates (MEM-005)."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=3)
    memory_type: Literal["semantic", "episodic"] = "semantic"
    scope: str = Field(default="general", max_length=200)
    structured_content: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    sensitivity: Literal["normal", "personal", "sensitive", "restricted"] = "normal"
    proposing_model: str = Field(default="unknown", max_length=200)
    prompt_version: str = Field(default="unknown", max_length=100)


def content_hash(statement: str, scope: str, memory_type: str) -> str:
    normalized = re.sub(r"\s+", " ", statement.strip().lower())
    return hashlib.sha256(
        f"{memory_type}|{scope}|{normalized}".encode("utf-8")
    ).hexdigest()


class MemoryService:
    def __init__(self, engine: AsyncEngine, settings: AwarenessSettings) -> None:
        self._engine = engine
        self._settings = settings

    # --- write paths ---------------------------------------------------------

    async def write_deterministic(
        self,
        *,
        statement: str,
        memory_type: str = "semantic",
        scope: str = "general",
        structured_content: dict[str, Any] | None = None,
        evidence: list[EvidenceRef] | None = None,
        importance: float = 0.6,
        confidence: float = 0.9,
        sensitivity: str = "normal",
        supersede_same_key: bool = True,
    ) -> dict[str, Any]:
        """Unambiguous policy-permitted write; active immediately."""
        statement = statement.strip()
        if not statement or len(statement) > self._settings.memory_statement_max_chars:
            raise ValueError(
                f"statement must be 1..{self._settings.memory_statement_max_chars} chars"
            )
        digest = content_hash(statement, scope, memory_type)
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            existing = await self._live_by_hash(connection, digest)
            if existing is not None:
                return {"memory_id": str(existing), "created": False, "reason": "duplicate"}
            superseded = None
            if supersede_same_key:
                superseded = await self._supersede_same_key(
                    connection, memory_type, scope, structured_content or {}, now,
                    explicit=True,
                )
            memory_id = await self._insert_memory(
                connection,
                statement=statement,
                memory_type=memory_type,
                scope=scope,
                structured_content=structured_content or {},
                importance=importance,
                confidence=confidence,
                sensitivity=sensitivity,
                status="active",
                digest=digest,
                now=now,
                supersedes=superseded,
                decision={"path": "deterministic"},
            )
            await self._insert_provenance(
                connection,
                memory_id,
                evidence or [EvidenceRef(kind="user_confirmation", reference="explicit")],
            )
            await self._queue_embedding(connection, memory_id, digest)
        return {"memory_id": str(memory_id), "created": True, "superseded": superseded and str(superseded)}

    async def propose_candidate(self, proposal: CandidateProposal) -> dict[str, Any]:
        """Validate a model-proposed candidate; accept, merge, or reject."""
        statement = proposal.statement.strip()
        if len(statement) > self._settings.memory_statement_max_chars:
            return await self._reject(proposal, "statement exceeds the configured bound")
        digest = content_hash(statement, proposal.scope, proposal.memory_type)
        now = datetime.now(timezone.utc)

        async with self._engine.begin() as connection:
            unsupported = await self._validate_evidence(connection, proposal.evidence)
            if unsupported:
                return await self._reject_tx(connection, proposal, digest, now, unsupported)

            existing = await self._live_by_hash(connection, digest)
            if existing is not None:
                await connection.execute(
                    pg_insert(MemoryRelationship)
                    .values(
                        from_memory_id=existing,
                        to_memory_id=existing,
                        relation="duplicates",
                    )
                    .on_conflict_do_nothing()
                )
                return {
                    "accepted": False,
                    "memory_id": str(existing),
                    "decision": "merged_duplicate",
                }

            explicit = any(e.kind == "user_confirmation" for e in proposal.evidence)
            confidence = 0.85 if explicit else 0.55
            conflict_or_supersede = await self._resolve_contradiction(
                connection, proposal, now, explicit=explicit
            )
            memory_id = await self._insert_memory(
                connection,
                statement=statement,
                memory_type=proposal.memory_type,
                scope=proposal.scope,
                structured_content=proposal.structured_content,
                importance=proposal.importance,
                confidence=confidence,
                sensitivity=proposal.sensitivity,
                status="active",
                digest=digest,
                now=now,
                supersedes=conflict_or_supersede.get("superseded"),
                decision={
                    "path": "candidate",
                    "decision": "accepted",
                    "model": proposal.proposing_model,
                    "prompt_version": proposal.prompt_version,
                    "explicit_evidence": explicit,
                },
            )
            await self._insert_provenance(connection, memory_id, proposal.evidence)
            conflicting = conflict_or_supersede.get("conflicting")
            if conflicting is not None:
                await connection.execute(
                    sa.insert(MemoryRelationship).values(
                        from_memory_id=memory_id,
                        to_memory_id=conflicting,
                        relation="conflicts_with",
                    )
                )
                await connection.execute(
                    sa.update(Memory)
                    .where(Memory.memory_id.in_((memory_id, conflicting)))
                    .values(metadata_json=Memory.metadata_json.op("||")(
                        sa.cast({"conflict": True}, JSONB)
                    ))
                )
            await self._queue_embedding(connection, memory_id, digest)
        result: dict[str, Any] = {"accepted": True, "memory_id": str(memory_id)}
        if conflict_or_supersede.get("superseded"):
            result["superseded"] = str(conflict_or_supersede["superseded"])
        if conflict_or_supersede.get("conflicting"):
            result["conflicts_with"] = str(conflict_or_supersede["conflicting"])
        return result

    # --- search --------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        memory_type: str | None = None,
        scope: str | None = None,
        max_sensitivity: str = "personal",
        query_embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if limit < 1 or limit > self._settings.memory_search_max_limit:
            raise ValueError(
                f"limit must be 1..{self._settings.memory_search_max_limit}"
            )
        allowed = [
            name for name, rank in _SENSITIVITY_ORDER.items()
            if rank <= _SENSITIVITY_ORDER.get(max_sensitivity, 1)
        ]
        now = datetime.now(timezone.utc)

        conditions = [
            Memory.status == "active",
            Memory.sensitivity.in_(allowed),
            sa.or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            sa.or_(Memory.valid_to.is_(None), Memory.valid_to > now),
        ]
        if memory_type:
            conditions.append(Memory.memory_type == memory_type)
        if scope:
            conditions.append(Memory.scope == scope)

        fts_rank = sa.func.ts_rank(
            sa.func.to_tsvector("english", Memory.statement),
            sa.func.plainto_tsquery("english", query),
        )
        recency = sa.func.exp(
            -sa.func.extract("epoch", sa.func.now() - Memory.learned_at) / 2_592_000.0
        )  # 30-day half-life-ish decay

        columns = [
            Memory.memory_id,
            Memory.memory_type,
            Memory.statement,
            Memory.structured_content,
            Memory.scope,
            Memory.sensitivity,
            Memory.importance,
            Memory.confidence,
            Memory.learned_at,
            Memory.valid_from,
            Memory.valid_to,
            fts_rank.label("fts_score"),
            recency.label("recency_score"),
        ]
        vector_score = None
        if query_embedding is not None:
            vector_score = (
                1 - MemoryEmbedding.embedding.cosine_distance(query_embedding)
            ).label("vector_score")
            columns.append(vector_score)

        statement = sa.select(*columns).where(*conditions)
        if query_embedding is not None:
            statement = statement.join(
                MemoryEmbedding, MemoryEmbedding.memory_id == Memory.memory_id,
                isouter=True,
            )

        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement.limit(200))).all()

        results = []
        for row in rows:
            # ts_rank yields a ~1e-20 epsilon (not exactly 0) for non-matching
            # rows; floor it so no-signal rows are excluded.
            fts = float(row.fts_score or 0.0)
            components = {
                "full_text": fts if fts > 1e-9 else 0.0,
                "recency": float(row.recency_score or 0.0),
                "importance": float(row.importance),
                "confidence": float(row.confidence),
                "vector": float(getattr(row, "vector_score", 0.0) or 0.0),
            }
            total = (
                2.0 * components["full_text"]
                + 1.5 * components["vector"]
                + 0.5 * components["recency"]
                + 0.5 * components["importance"]
                + 0.3 * components["confidence"]
            )
            if components["full_text"] <= 0 and components["vector"] <= 0:
                continue  # no relevance signal at all
            results.append(
                {
                    "memory_id": str(row.memory_id),
                    "memory_type": row.memory_type,
                    "statement": row.statement,
                    "structured_content": row.structured_content,
                    "scope": row.scope,
                    "sensitivity": row.sensitivity,
                    "learned_at": row.learned_at.isoformat(),
                    "valid_from": row.valid_from.isoformat() if row.valid_from else None,
                    "valid_to": row.valid_to.isoformat() if row.valid_to else None,
                    "score": round(total, 6),
                    "component_scores": components,
                }
            )
        results.sort(key=lambda item: item["score"], reverse=True)
        results = results[:limit]

        if results:
            ids = [UUID(item["memory_id"]) for item in results]
            async with self._engine.begin() as connection:
                # Access counters never change semantic validity.
                await connection.execute(
                    sa.update(Memory)
                    .where(Memory.memory_id.in_(ids))
                    .values(
                        access_count=Memory.access_count + 1,
                        last_accessed_at=now,
                    )
                )
        return {
            "query": query,
            "as_of": now.isoformat(timespec="seconds"),
            "vector_used": query_embedding is not None,
            "results": results,
        }

    async def delete(self, memory_id: UUID, *, reason: str) -> bool:
        """Explicit audited deletion (soft: status flips, audit retained)."""
        async with self._engine.begin() as connection:
            result = await connection.execute(
                sa.update(Memory)
                .where(Memory.memory_id == memory_id, Memory.status != "deleted")
                .values(
                    status="deleted",
                    metadata_json=Memory.metadata_json.op("||")(
                        sa.cast(
                            {
                                "deletion": {
                                    "reason": reason,
                                    "at": datetime.now(timezone.utc).isoformat(),
                                }
                            },
                            JSONB,
                        )
                    ),
                )
            )
        return bool(result.rowcount)

    # --- episodes -------------------------------------------------------------

    async def create_episode_from_alert(self, alert_id: UUID) -> dict[str, Any] | None:
        """Deterministic episode for a completed meaningful incident."""
        async with self._engine.connect() as connection:
            alert = (
                await connection.execute(
                    sa.select(
                        Alert.alert_id,
                        Alert.alert_type,
                        Alert.severity,
                        Alert.entity_id,
                        Alert.title,
                        Alert.opened_at,
                        Alert.resolved_at,
                        Alert.occurrence_count,
                    ).where(Alert.alert_id == alert_id)
                )
            ).one_or_none()
            if alert is None:
                return None
            event_ids = (
                await connection.execute(
                    sa.text(
                        "SELECT event_id FROM alert_events WHERE alert_id = :a "
                        "ORDER BY id LIMIT 50"
                    ),
                    {"a": alert_id},
                )
            ).scalars().all()
        resolved = (
            alert.resolved_at.isoformat(timespec="seconds")
            if alert.resolved_at
            else "unresolved"
        )
        statement = (
            f"Incident {alert.alert_type} ({alert.severity}) on "
            f"{alert.entity_id or 'unknown entity'}: {alert.title}. "
            f"Opened {alert.opened_at.isoformat(timespec='seconds')}, "
            f"resolved {resolved}, occurred {alert.occurrence_count} time(s)."
        )
        evidence = [EvidenceRef(kind="alert", reference=str(alert.alert_id))] + [
            EvidenceRef(kind="event", reference=str(event_id)) for event_id in event_ids
        ]
        return await self.write_deterministic(
            statement=statement,
            memory_type="episodic",
            scope=f"incident:{alert.alert_type}",
            structured_content={
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "entity_id": alert.entity_id,
                "occurrences": alert.occurrence_count,
            },
            evidence=evidence,
            importance=0.8 if alert.severity == "critical" else 0.5,
            supersede_same_key=False,  # each incident is its own episode
        )

    # --- internals -------------------------------------------------------------

    async def _validate_evidence(
        self, connection: AsyncConnection, evidence: list[EvidenceRef]
    ) -> str | None:
        """Returns a rejection reason when any evidence reference is dangling."""
        for item in evidence:
            if item.kind == "event":
                try:
                    event_id = UUID(item.reference)
                except ValueError:
                    return f"evidence event id {item.reference!r} is not a UUID"
                found = (
                    await connection.execute(
                        sa.select(Event.event_id).where(Event.event_id == event_id)
                    )
                ).scalar_one_or_none()
                if found is None:
                    return f"evidence event {item.reference} does not exist"
            elif item.kind == "alert":
                try:
                    ref = UUID(item.reference)
                except ValueError:
                    return f"evidence alert id {item.reference!r} is not a UUID"
                found = (
                    await connection.execute(
                        sa.select(Alert.alert_id).where(Alert.alert_id == ref)
                    )
                ).scalar_one_or_none()
                if found is None:
                    return f"evidence alert {item.reference} does not exist"
        return None

    async def _reject(self, proposal: CandidateProposal, reason: str) -> dict[str, Any]:
        digest = content_hash(proposal.statement, proposal.scope, proposal.memory_type)
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as connection:
            return await self._reject_tx(connection, proposal, digest, now, reason)

    async def _reject_tx(
        self,
        connection: AsyncConnection,
        proposal: CandidateProposal,
        digest: str,
        now: datetime,
        reason: str,
    ) -> dict[str, Any]:
        memory_id = await self._insert_memory(
            connection,
            statement=proposal.statement[: self._settings.memory_statement_max_chars],
            memory_type=proposal.memory_type,
            scope=proposal.scope,
            structured_content=proposal.structured_content,
            importance=proposal.importance,
            confidence=0.0,
            sensitivity=proposal.sensitivity,
            status="rejected",
            digest=digest,
            now=now,
            supersedes=None,
            decision={
                "path": "candidate",
                "decision": "rejected",
                "reason": reason,
                "model": proposal.proposing_model,
                "prompt_version": proposal.prompt_version,
            },
        )
        logger.info(
            "memory candidate rejected: %s", reason,
            extra={"component": "memory"},
        )
        return {"accepted": False, "memory_id": str(memory_id), "decision": "rejected", "reason": reason}

    async def _live_by_hash(
        self, connection: AsyncConnection, digest: str
    ) -> UUID | None:
        return (
            await connection.execute(
                sa.select(Memory.memory_id).where(
                    Memory.content_hash == digest,
                    Memory.status.in_(("candidate", "accepted", "active")),
                )
            )
        ).scalar_one_or_none()

    async def _resolve_contradiction(
        self,
        connection: AsyncConnection,
        proposal: CandidateProposal,
        now: datetime,
        *,
        explicit: bool,
    ) -> dict[str, Any]:
        """Same (type, scope, structured key) with a different value: explicit
        evidence supersedes; weak inference keeps both in explicit conflict."""
        key = proposal.structured_content.get("key")
        if not key:
            return {}
        row = (
            await connection.execute(
                sa.select(Memory.memory_id, Memory.structured_content, Memory.metadata_json.label("m"))
                .where(
                    Memory.memory_type == proposal.memory_type,
                    Memory.scope == proposal.scope,
                    Memory.status == "active",
                    Memory.structured_content["key"].astext == str(key),
                )
                .with_for_update()
            )
        ).first()
        if row is None:
            return {}
        if row.structured_content.get("value") == proposal.structured_content.get("value"):
            return {}
        if explicit:
            await connection.execute(
                sa.update(Memory)
                .where(Memory.memory_id == row.memory_id)
                .values(status="superseded", superseded_at=now, valid_to=now)
            )
            return {"superseded": row.memory_id}
        return {"conflicting": row.memory_id}

    async def _supersede_same_key(
        self,
        connection: AsyncConnection,
        memory_type: str,
        scope: str,
        structured_content: dict[str, Any],
        now: datetime,
        *,
        explicit: bool,
    ) -> UUID | None:
        key = structured_content.get("key")
        if not key:
            return None
        row = (
            await connection.execute(
                sa.select(Memory.memory_id, Memory.structured_content)
                .where(
                    Memory.memory_type == memory_type,
                    Memory.scope == scope,
                    Memory.status == "active",
                    Memory.structured_content["key"].astext == str(key),
                )
                .with_for_update()
            )
        ).first()
        if row is None or row.structured_content.get("value") == structured_content.get("value"):
            return None
        await connection.execute(
            sa.update(Memory)
            .where(Memory.memory_id == row.memory_id)
            .values(status="superseded", superseded_at=now, valid_to=now)
        )
        return row.memory_id

    async def _insert_memory(
        self,
        connection: AsyncConnection,
        *,
        statement: str,
        memory_type: str,
        scope: str,
        structured_content: dict[str, Any],
        importance: float,
        confidence: float,
        sensitivity: str,
        status: str,
        digest: str,
        now: datetime,
        supersedes: UUID | None,
        decision: dict[str, Any],
    ) -> UUID:
        memory_id = (
            await connection.execute(
                sa.insert(Memory)
                .values(
                    memory_type=memory_type,
                    statement=statement,
                    structured_content=structured_content,
                    importance=importance,
                    confidence=confidence,
                    scope=scope,
                    sensitivity=sensitivity,
                    valid_from=now,
                    learned_at=now,
                    status=status,
                    content_hash=digest,
                    supersedes_memory_id=supersedes,
                    metadata_json={"decision": decision},
                )
                .returning(Memory.memory_id)
            )
        ).scalar_one()
        if supersedes is not None:
            await connection.execute(
                sa.insert(MemoryRelationship).values(
                    from_memory_id=memory_id,
                    to_memory_id=supersedes,
                    relation="supersedes",
                )
            )
        return memory_id

    async def _insert_provenance(
        self,
        connection: AsyncConnection,
        memory_id: UUID,
        evidence: list[EvidenceRef],
    ) -> None:
        for item in evidence:
            await connection.execute(
                sa.insert(MemoryProvenance).values(
                    memory_id=memory_id,
                    kind=item.kind,
                    reference=item.reference,
                    metadata_json=item.metadata,
                )
            )

    async def _queue_embedding(
        self, connection: AsyncConnection, memory_id: UUID, digest: str
    ) -> None:
        """Embedding is asynchronous outbox work; outage never blocks writes."""
        if not self._settings.embedding_model:
            return
        await connection.execute(
            pg_insert(OutboxItem)
            .values(
                work_type="embedding",
                aggregate_type="memory",
                aggregate_id=str(memory_id),
                payload={"memory_id": str(memory_id), "content_hash": digest},
                idempotency_key=f"embedding:{memory_id}:{digest[:16]}",
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
