"""Memory endpoints (Phase 6): deterministic writes, candidate proposals,
hybrid search, audited deletion. Loopback-only like the rest of the API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from talos.awareness.memory.embeddings import EmbeddingUnavailableError, OllamaEmbeddingClient
from talos.awareness.memory.service import CandidateProposal, EvidenceRef, MemoryService

router = APIRouter()


def _service(request: Request) -> MemoryService:
    return MemoryService(request.app.state.engine, request.app.state.settings)


class DeterministicWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=3)
    memory_type: str = "semantic"
    scope: str = "general"
    structured_content: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    importance: float = Field(default=0.6, ge=0, le=1)
    sensitivity: str = "normal"


@router.post("/memory/deterministic")
async def write_deterministic(body: DeterministicWrite, request: Request) -> dict:
    try:
        return await _service(request).write_deterministic(
            statement=body.statement,
            memory_type=body.memory_type,
            scope=body.scope,
            structured_content=body.structured_content,
            evidence=body.evidence or None,
            importance=body.importance,
            sensitivity=body.sensitivity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/memory/candidates")
async def propose_candidate(body: dict, request: Request) -> dict:
    try:
        proposal = CandidateProposal(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:500]) from exc
    return await _service(request).propose_candidate(proposal)


@router.get("/memory/search")
async def search_memory(
    request: Request,
    query: str = Query(min_length=1),
    limit: int = Query(10, ge=1),
    memory_type: str | None = Query(None),
    scope: str | None = Query(None),
    max_sensitivity: str = Query("personal"),
) -> dict:
    settings = request.app.state.settings
    query_embedding = None
    if settings.embedding_model:
        try:
            query_embedding = await OllamaEmbeddingClient(settings).embed(query)
        except EmbeddingUnavailableError:
            query_embedding = None  # truthful degradation: full-text continues
    try:
        return await _service(request).search(
            query,
            limit=limit,
            memory_type=memory_type,
            scope=scope,
            max_sensitivity=max_sensitivity,
            query_embedding=query_embedding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/memory/{memory_id}/delete")
async def delete_memory(memory_id: UUID, request: Request, reason: str = Query(...)) -> dict:
    deleted = await _service(request).delete(memory_id, reason=reason)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found or already deleted")
    return {"ok": True}
