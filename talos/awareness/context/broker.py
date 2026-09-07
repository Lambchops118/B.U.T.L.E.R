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
direction for a hard budget.

Human context: presence state and recent interaction, reported by the main
agent over the internal transport, are selected as their own high-priority
section and also steer attention selection. Two deterministic effects, no
inference:

- ``interruptibility`` is honored — ``passive`` items are withheld while
  nobody is present, because "mention it if we are already talking" cannot be
  satisfied when we are not.
- ``conversation_relevance`` on an attention item is scored against the
  entities named by recent interaction/agent events, and a match sorts the
  item earlier *within its priority band*. Priority bands themselves never
  change, so a critical alert can never be reordered behind small talk.

The focus set is only as good as what the agent supplies: an interaction the
agent could not attribute to an entity contributes nothing, and relevance is
then zero and selection is exactly the pre-existing priority order. That
limitation is reported in the snapshot rather than papered over.
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
    Entity,
    Event,
    Source,
    StateTransition,
)

# Fixed priority order (CTX-002); lower number = kept longer.
# Presence sits directly below alerts: who is here, and whether we are mid
# conversation, is what makes every item under it interpretable.
PRIORITY_CRITICAL_ALERTS = 1
PRIORITY_ALERTS = 2
PRIORITY_PRESENCE = 3
PRIORITY_ATTENTION = 4
PRIORITY_STATE = 5
PRIORITY_TRANSITIONS = 6
PRIORITY_HEALTH = 7

# Deterministic relevance weights (no model involved).
RELEVANCE_ENTITY_MATCH = 2.0
RELEVANCE_IMMEDIATE = 1.0

# Event types carrying human/agent context, matched by prefix.
INTERACTION_EVENT_PREFIX = "person.interaction."
AGENT_EVENT_PREFIX = "agent."


def estimate_tokens(text: str) -> int:
    """Conservative token estimate; deliberately overestimates."""
    return max(1, math.ceil(len(text) / 3.5))


@dataclass(frozen=True)
class Candidate:
    item_id: str
    priority: int
    text: str
    reason: str
    # Ordering nudge *inside* a priority band; never across bands.
    relevance: float = 0.0

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def select_items(
    candidates: list[Candidate], budget_tokens: int
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Admit by priority under the budget; critical alerts are never dropped.

    Returns (selected, audit). The audit records every candidate's outcome.
    """
    # Priority first (unchanged), then higher relevance, then a stable id so
    # equal candidates keep a deterministic, reproducible order.
    ordered = sorted(candidates, key=lambda c: (c.priority, -c.relevance, c.item_id))
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
                "relevance": candidate.relevance,
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


def _relevance_for(row: Any, focus: dict[str, Any]) -> tuple[float, str]:
    """Score one attention item against the current human focus.

    Deterministic and additive; the reason string records why, so the audit
    explains every ordering decision without anyone having to guess.
    """
    relevance = 0.0
    reasons = ["pending_attention"]

    related = {row.entity_id} if row.entity_id else set()
    declared = row.conversation_relevance or {}
    if isinstance(declared.get("entity_id"), str):
        related.add(declared["entity_id"])
    named = declared.get("entity_ids")
    if isinstance(named, list):
        related.update(str(item) for item in named if isinstance(item, str))

    if related & focus["entity_ids"]:
        relevance += RELEVANCE_ENTITY_MATCH
        reasons.append("matches_conversation_entity")
    if row.interruptibility == "immediate":
        relevance += RELEVANCE_IMMEDIATE
        reasons.append("immediate")
    return relevance, "+".join(reasons)


def _limitations(focus: dict[str, Any]) -> str:
    """Report exactly which human signals are and are not available."""
    parts = []
    if focus["status"] == "unknown":
        parts.append("no presence signal has ever been recorded for a person entity")
    elif not focus["present"]:
        parts.append(f"presence is {focus['status']}, so passive items are withheld")
    if not focus["entity_ids"]:
        parts.append(
            "no recent interaction named an entity, so conversation relevance "
            "contributed nothing and ordering is by priority alone"
        )
    parts.append("user location within the home is still not modeled")
    return "; ".join(parts) + "."


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
            focus = await self._human_focus(connection, now)
            candidates += await self._alert_candidates(connection, now)
            candidates += await self._presence_candidates(connection, now, focus)
            candidates += await self._attention_candidates(connection, now, focus)
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
            "presence": focus["summary"],
            "limitations": _limitations(focus),
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

    async def _human_focus(self, connection, now: datetime) -> dict[str, Any]:
        """Deterministic human context: who is present and what was just discussed.

        Presence comes from ``current_state`` rows on person entities, judged
        by the same freshness rule reads use everywhere else — an overdue row
        is ``stale``, never silently "current". The focus entity set is
        collected from the ``entity_ids`` recent interaction/agent events
        carry; events that name none contribute nothing rather than being
        guessed at.
        """
        present = False
        presence_status = "unknown"
        last_presence: datetime | None = None
        modality: str | None = None

        rows = (
            await connection.execute(
                sa.select(
                    CurrentState.entity_id,
                    CurrentState.property_name,
                    CurrentState.value_json,
                    CurrentState.state_status,
                    CurrentState.received_at,
                    Source.stale_after_seconds,
                )
                .join(Entity, Entity.entity_id == CurrentState.entity_id)
                .join(Source, Source.source_id == CurrentState.source_id, isouter=True)
                .where(Entity.entity_type == "person")
                .order_by(CurrentState.updated_at.desc())
                .limit(self._settings.situation_max_items_per_section)
            )
        ).all()

        for row in rows:
            status = self._qualified_status(
                row.state_status, row.received_at, row.stale_after_seconds, now
            )
            value = (row.value_json or {}).get("value")
            if row.property_name == "present":
                presence_status = status
                last_presence = row.received_at
                present = bool(value) and status in ("current", "inferred")
            elif row.property_name == "modality" and isinstance(value, str):
                modality = value

        focus_entities: set[str] = set()
        last_interaction: datetime | None = None
        window = now - timedelta(
            minutes=self._settings.situation_transition_window_minutes
        )
        event_rows = (
            await connection.execute(
                sa.select(Event.event_type, Event.payload, Event.received_at)
                .where(
                    Event.received_at >= window,
                    sa.or_(
                        Event.event_type.startswith(INTERACTION_EVENT_PREFIX),
                        Event.event_type.startswith(AGENT_EVENT_PREFIX),
                    ),
                )
                .order_by(Event.received_at.desc())
                .limit(self._settings.situation_max_items_per_section)
            )
        ).all()
        for row in event_rows:
            if last_interaction is None:
                last_interaction = row.received_at
            named = (row.payload or {}).get("entity_ids")
            if isinstance(named, list):
                focus_entities.update(
                    str(item) for item in named if isinstance(item, str) and item
                )

        if present:
            summary = f"present via {modality or 'unknown modality'}"
        elif presence_status == "unknown":
            summary = "no presence signal recorded"
        else:
            summary = f"not detected as present ({presence_status})"

        return {
            "present": present,
            "status": presence_status,
            "modality": modality,
            "last_presence_at": last_presence,
            "last_interaction_at": last_interaction,
            "entity_ids": focus_entities,
            "interaction_events": len(event_rows),
            "summary": summary,
        }

    def _qualified_status(
        self,
        state_status: str,
        received_at: datetime | None,
        stale_after: float | None,
        now: datetime,
    ) -> str:
        """Reads never present overdue data as current (shared with state rows)."""
        if state_status in ("current", "inferred") and received_at is not None:
            deadline = stale_after or self._settings.default_stale_after_seconds
            if (now - received_at).total_seconds() > deadline:
                return "stale"
        return state_status

    async def _presence_candidates(
        self, connection, now: datetime, focus: dict[str, Any]
    ) -> list[Candidate]:
        """One bounded line for the human context, plus one for recent interaction."""
        candidates: list[Candidate] = []
        if focus["status"] == "unknown" and focus["last_interaction_at"] is None:
            # Nothing observed. Say nothing rather than asserting absence:
            # "no signal" and "nobody home" are different claims.
            return candidates

        presence_text = (
            f"PRESENCE owner: {focus['summary']} "
            f"({focus['status']}, last signal {_iso(focus['last_presence_at'])}, "
            f"{_age_text(now, focus['last_presence_at'])})"
        )
        candidates.append(
            Candidate(
                item_id="presence:owner",
                priority=PRIORITY_PRESENCE,
                text=presence_text,
                reason="human_presence",
            )
        )

        if focus["last_interaction_at"] is not None:
            named = sorted(focus["entity_ids"])
            about = f", about {', '.join(named)}" if named else ""
            candidates.append(
                Candidate(
                    item_id="presence:interaction",
                    priority=PRIORITY_PRESENCE,
                    text=(
                        f"INTERACTION last {_iso(focus['last_interaction_at'])} "
                        f"({_age_text(now, focus['last_interaction_at'])}, "
                        f"{focus['interaction_events']} recent event(s){about})"
                    ),
                    reason="recent_interaction",
                )
            )
        return candidates

    async def _attention_candidates(
        self, connection, now: datetime, focus: dict[str, Any]
    ) -> list[Candidate]:
        rows = (
            await connection.execute(
                sa.select(
                    AttentionItem.attention_item_id,
                    AttentionItem.reason,
                    AttentionItem.priority,
                    AttentionItem.interruptibility,
                    AttentionItem.entity_id,
                    AttentionItem.conversation_relevance,
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
        candidates = []
        for row in rows:
            if row.interruptibility == "passive" and not focus["present"]:
                # "Raise it if we happen to be talking" is unsatisfiable when
                # nobody is here; the item stays pending for a later read.
                continue
            relevance, why = _relevance_for(row, focus)
            candidates.append(
                Candidate(
                    item_id=f"attention:{row.attention_item_id}",
                    priority=PRIORITY_ATTENTION,
                    text=(
                        f"ATTENTION(p{row.priority}, {row.interruptibility}) {row.reason} "
                        f"(raised {_iso(row.created_at)}, {_age_text(now, row.created_at)})"
                    ),
                    reason=why,
                    relevance=relevance,
                )
            )
        return candidates


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
