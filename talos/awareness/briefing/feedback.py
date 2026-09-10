"""Explicit owner feedback in existing memories; no transcripts or model inference."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from talos.awareness.db.models import Memory
from talos.awareness.memory.service import EvidenceRef, MemoryService

Category = Literal["alert", "transition", "agent_outcome", "novelty", "interaction", "reminder"]
SCOPE = "briefing_preferences"


class BriefingFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Category | None = None
    item_id: str | None = Field(default=None, min_length=1, max_length=2000)
    value: Literal["dismiss", "interest", "neutral"]

    @model_validator(mode="after")
    def one_target(self):
        if (self.category is None) == (self.item_id is None):
            raise ValueError("provide exactly one of category or item_id")
        return self


def item_key(item_id: str) -> str:
    return "item:" + hashlib.sha256(item_id.encode()).hexdigest()


async def record_feedback(engine, settings, feedback: BriefingFeedback) -> dict:
    key = f"class:{feedback.category}" if feedback.category else item_key(feedback.item_id)
    return await MemoryService(engine, settings).write_deterministic(
        statement=f"Owner briefing preference {key}: {feedback.value}.",
        scope=SCOPE, sensitivity="personal",
        structured_content={"key": key, "value": feedback.value},
        evidence=[EvidenceRef(kind="user_confirmation", reference="briefing_feedback")],
    )


async def preferences(connection, candidates: list[dict], now: datetime) -> dict[str, str]:
    keys = {f"class:{c['category']}" for c in candidates}
    keys.update(item_key(c["item_id"]) for c in candidates)
    if not keys:
        return {}
    key = Memory.structured_content["key"].astext
    # Exact-key reads, never vector search or unrestricted memory statements.
    rows = (await connection.execute(sa.select(
        key.label("key"), Memory.structured_content["value"].astext.label("value"),
    ).where(
        Memory.scope == SCOPE, Memory.status == "active", Memory.sensitivity.in_(("normal", "personal")),
        key.in_(keys),
        sa.or_(Memory.valid_from.is_(None), Memory.valid_from <= now),
        sa.or_(Memory.valid_to.is_(None), Memory.valid_to > now),
        sa.or_(Memory.expires_at.is_(None), Memory.expires_at > now),
    ).distinct(key).order_by(key, Memory.learned_at.desc(), Memory.memory_id).limit(len(keys)))).all()
    return {r.key: r.value for r in rows if r.value in {"dismiss", "interest", "neutral"}}


def apply_preferences(candidates: list[dict], prefs: dict[str, str]) -> tuple[list[dict], list[dict]]:
    kept, audit = [], []
    for candidate in candidates:
        values = [prefs.get(f"class:{candidate['category']}"), prefs.get(item_key(candidate["item_id"]))]
        dismissed = "dismiss" in values and candidate["priority"] != 1
        if not dismissed:
            candidate = dict(candidate)
            candidate["relevance"] = candidate.get("relevance", 0) + (1 if "interest" in values else 0)
            kept.append(candidate)
        if any(values):
            audit.append({"item_id": candidate["item_id"], "included": not dismissed,
                          "reason": "dismissed_class_or_item" if dismissed else "preference_applied_critical_protected"})
    return kept, audit
