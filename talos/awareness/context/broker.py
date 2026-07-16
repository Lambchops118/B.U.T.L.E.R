"""Compact deterministic situation snapshot under a hard token budget (C12).

The broker reads only the typed Phase 3/4 services' tables (qualified state,
active alerts, pending attention, recent meaningful transitions, unhealthy
sources) and renders bounded single-line items with explicit temporal
qualification — never a raw dump or generated prose. Selection follows fixed
priority: active critical alerts are always included and never truncated;
everything else is admitted in priority order while the budget lasts, and
every include/exclude decision is audited (item id, reason, tokens,
priority).

Token accounting uses a conservative estimate (``ceil(chars / 3.5)``) — no
tokenizer dependency exists in this venv, and overestimating is the safe
direction for a hard budget. Likely-user-location and conversation/task
relevance signals do not exist in the repository yet; the snapshot documents
that limitation rather than inventing them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import (
    Alert,
    AttentionItem,
    CurrentState,
    Source,
    StateTransition,
)

# Fixed priority order (CTX-002); lower number = kept longer.
PRIORITY_CRITICAL_ALERTS = 1
PRIORITY_ALERTS = 2
PRIORITY_ATTENTION = 3
PRIORITY_STATE = 4
PRIORITY_TRANSITIONS = 5
PRIORITY_HEALTH = 6


def estimate_tokens(text: str) -> int:
    """Conservative token estimate; deliberately overestimates."""
    return max(1, math.ceil(len(text) / 3.5))


@dataclass(frozen=True)
class Candidate:
    item_id: str
    priority: int
    text: str
    reason: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def select_items(
    candidates: list[Candidate], budget_tokens: int
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Admit by priority under the budget; critical alerts are never dropped.

    Returns (selected, audit). The audit records every candidate's outcome.
    """
    ordered = sorted(candidates, key=lambda c: (c.priority, c.item_id))
    selected: list[Candidate] = []
    audit: list[dict[str, Any]] = []
    used = 0
    for candidate in ordered:
        mandatory = candidate.priority == PRIORITY_CRITICAL_ALERTS
        fits = used + candidate.tokens <= budget_tokens
        included = mandatory or fits
        if included:
            selected.append(candidate)
            used += candidate.tokens
        audit.append(
            {
                "item_id": candidate.item_id,
                "priority": candidate.priority,
                "tokens": candidate.tokens,
                "included": included,
                "reason": candidate.reason if included else "budget_exceeded",
            }
        )
    return selected, audit


def _age_text(now: datetime, moment: datetime | None) -> str:
    if moment is None:
        return "age unknown"
    seconds = (now - moment).total_seconds()
    if seconds < 90:
        return f"age {seconds:.0f}s"
    if seconds < 5400:
        return f"age {seconds / 60:.0f}m"
    return f"age {seconds / 3600:.1f}h"


def _iso(moment: datetime | None) -> str:
    return moment.isoformat(timespec="seconds") if moment else "unknown"


class SituationBroker:
    def __init__(self, engine: AsyncEngine, settings: AwarenessSettings) -> None:
        self._engine = engine
        self._settings = settings

    async def build(
        self,
        *,
        budget_tokens: int | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        budget = budget_tokens or self._settings.situation_budget_tokens
        if budget < 1 or budget > self._settings.situation_budget_tokens * 10:
            raise ValueError("budget_tokens out of range")
        now = datetime.now(timezone.utc)
        candidates: list[Candidate] = []
        async with self._engine.connect() as connection:
            candidates += await self._alert_candidates(connection, now)
            candidates += await self._attention_candidates(connection, now)
            candidates += await self._state_candidates(connection, now, entity_id)
            candidates += await self._transition_candidates(connection, now)
            candidates += await self._health_candidates(connection, now)

        selected, audit = select_items(candidates, budget)
        lines = [candidate.text for candidate in selected]
        used = sum(candidate.tokens for candidate in selected)
        return {
            "as_of": _iso(now),
            "budget_tokens": budget,
            "used_tokens": used,
            "truncated": len(selected) < len(candidates),
            "item_count": len(selected),
            "text": "\n".join(lines) if lines else "No noteworthy situation items.",
            "audit": audit,
            "limitations": (
                "No user-location or conversation-relevance signal exists yet; "
                "selection is by alert/attention/freshness priority only."
            ),
        }

    async def _alert_candidates(self, connection, now: datetime) -> list[Candidate]:
        rows = (
            await connection.execute(
                sa.select(
                    Alert.alert_id,
                    Alert.severity,
                    Alert.title,
                    Alert.status,
                    Alert.occurrence_count,
                    Alert.last_seen_at,
                )
                .where(Alert.status.in_(("open", "acknowledged")))
                .order_by(Alert.opened_at.desc())
                .limit(self._settings.situation_max_items_per_section)
            )
        ).all()
        candidates = []
        for row in rows:
            critical = row.severity == "critical"
            text = (
                f"ALERT[{row.severity}] {row.title} ({row.status}, "
                f"x{row.occurrence_count}, last {_iso(row.last_seen_at)}, "
                f"{_age_text(now, row.last_seen_at)})"
            )
            candidates.append(
                Candidate(
                    item_id=f"alert:{row.alert_id}",
                    priority=PRIORITY_CRITICAL_ALERTS if critical else PRIORITY_ALERTS,
                    text=text,
                    reason="active_critical_alert" if critical else "active_alert",
                )
            )
        return candidates

    async def _attention_candidates(self, connection, now: datetime) -> list[Candidate]:
        rows = (
            await connection.execute(
                sa.select(
                    AttentionItem.attention_item_id,
                    AttentionItem.reason,
                    AttentionItem.priority,
                    AttentionItem.interruptibility,
                    AttentionItem.created_at,
                )
                .where(
                    AttentionItem.delivery_status == "pending",
                    sa.or_(
                        AttentionItem.available_after.is_(None),
                        AttentionItem.available_after <= now,
                    ),
                    sa.or_(
                        AttentionItem.expires_at.is_(None),
                        AttentionItem.expires_at > now,
                    ),
                )
                .order_by(AttentionItem.priority)
                .limit(self._settings.situation_max_items_per_section)
            )
        ).all()
        return [
            Candidate(
                item_id=f"attention:{row.attention_item_id}",
                priority=PRIORITY_ATTENTION,
                text=(
                    f"ATTENTION(p{row.priority}, {row.interruptibility}) {row.reason} "
                    f"(raised {_iso(row.created_at)}, {_age_text(now, row.created_at)})"
                ),
                reason="pending_attention",
            )
            for row in rows
        ]

    async def _state_candidates(
        self, connection, now: datetime, entity_id: str | None
    ) -> list[Candidate]:
        statement = (
            sa.select(
                CurrentState.entity_id,
                CurrentState.property_name,
                CurrentState.value_json,
                CurrentState.state_status,
                CurrentState.observed_at,
                CurrentState.received_at,
                CurrentState.confidence,
                CurrentState.source_id,
                Source.stale_after_seconds,
            )
            .join(Source, Source.source_id == CurrentState.source_id, isouter=True)
            .order_by(CurrentState.updated_at.desc())
            .limit(self._settings.situation_max_items_per_section)
        )
        if entity_id is not None:
            statement = statement.where(CurrentState.entity_id == entity_id)
        rows = (await connection.execute(statement)).all()
        candidates = []
        for row in rows:
            status = row.state_status
            if status in ("current", "inferred") and row.received_at is not None:
                deadline = row.stale_after_seconds or self._settings.default_stale_after_seconds
                if (now - row.received_at).total_seconds() > deadline:
                    status = "stale"  # reads never present overdue data as current
            value = (row.value_json or {}).get("value")
            text = (
                f"STATE {row.entity_id}.{row.property_name} = {value!r} "
                f"({status}, observed {_iso(row.observed_at)}, "
                f"received {_iso(row.received_at)}, {_age_text(now, row.received_at)}, "
                f"conf {row.confidence:.2f}, src {row.source_id})"
            )
            candidates.append(
                Candidate(
                    item_id=f"state:{row.entity_id}.{row.property_name}",
                    priority=PRIORITY_STATE,
                    text=text,
                    reason="entity_filter" if entity_id else "recent_state",
                )
            )
        return candidates

    async def _transition_candidates(self, connection, now: datetime) -> list[Candidate]:
        window = now - timedelta(
            minutes=self._settings.situation_transition_window_minutes
        )
        rows = (
            await connection.execute(
                sa.select(
                    StateTransition.id,
                    StateTransition.entity_id,
                    StateTransition.property_name,
                    StateTransition.to_value,
                    StateTransition.to_status,
                    StateTransition.reason,
                    StateTransition.occurred_at,
                )
                .where(StateTransition.occurred_at >= window)
                .order_by(StateTransition.occurred_at.desc())
                .limit(self._settings.situation_max_items_per_section)
            )
        ).all()
        return [
            Candidate(
                item_id=f"transition:{row.id}",
                priority=PRIORITY_TRANSITIONS,
                text=(
                    f"CHANGE {row.entity_id}.{row.property_name} -> "
                    f"{(row.to_value or {}).get('value')!r} [{row.to_status}] "
                    f"({row.reason}, {_iso(row.occurred_at)}, "
                    f"{_age_text(now, row.occurred_at)})"
                ),
                reason="recent_transition",
            )
            for row in rows
        ]

    async def _health_candidates(self, connection, now: datetime) -> list[Candidate]:
        rows = (
            await connection.execute(
                sa.select(
                    Source.source_id,
                    Source.health_status,
                    Source.last_received_at,
                )
                .where(
                    Source.enabled.is_(True),
                    Source.health_status.notin_(("healthy", "unknown")),
                )
                .limit(self._settings.situation_max_items_per_section)
            )
        ).all()
        return [
            Candidate(
                item_id=f"health:{row.source_id}",
                priority=PRIORITY_HEALTH,
                text=(
                    f"HEALTH source {row.source_id} is {row.health_status} "
                    f"(last message {_iso(row.last_received_at)}, "
                    f"{_age_text(now, row.last_received_at)})"
                ),
                reason="unhealthy_source",
            )
            for row in rows
        ]
