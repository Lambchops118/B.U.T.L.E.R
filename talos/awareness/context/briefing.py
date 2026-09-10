"""Read-only, deterministic briefing candidates (9A). No delivery or model call."""

from __future__ import annotations

import math
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.context.broker import Candidate, _age_text, _iso
from talos.awareness.history.briefing import BriefingHistory
from talos.awareness.history.telemetry import QueryBoundsError
from talos.awareness.logging_utils import get_logger
from talos.awareness.briefing.speech import event_text, novelty_text, short_text, transition_text

logger = get_logger("talos.awareness.briefing")


class BriefingAssemblyError(RuntimeError):
    """No complete bounded candidate set could be assembled; do not deliver."""


@dataclass(frozen=True, kw_only=True)
class BriefingCandidate(Candidate):
    category: str
    entity_id: str | None
    source_id: str | None
    timestamp: str
    query: str
    evidence: dict[str, Any]
    novelty_score: float | None = None
    spoken_text: str | None = None


@dataclass(frozen=True)
class BriefingWindow:
    start: datetime
    end: datetime
    origin: str
    truncated: bool


def resolve_window(
    settings: AwarenessSettings, end: datetime, last_delivery: datetime | None,
) -> BriefingWindow:
    if end.tzinfo is None or end.utcoffset() is None:
        raise QueryBoundsError("briefing end must be timezone-aware")
    if last_delivery is not None and (
        last_delivery.tzinfo is None or last_delivery.utcoffset() is None or last_delivery >= end
    ):
        raise QueryBoundsError("last delivery must be timezone-aware and before end")
    proposed = last_delivery or end - timedelta(hours=settings.briefing_default_window_hours)
    earliest = end - timedelta(days=settings.max_query_range_days)
    return BriefingWindow(
        max(proposed, earliest), end,
        "recorded_delivery" if last_delivery else "configured_first_run_window",
        proposed < earliest,
    )


def _one_line(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + " [text truncated]"


class BriefingAssembler:
    def __init__(self, engine: AsyncEngine, settings: AwarenessSettings) -> None:
        self._engine = engine
        self._settings = settings

    async def build(self, kind: str, *, now: datetime | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", kind):
            raise QueryBoundsError("briefing kind must be a lowercase identifier (1-50 chars)")
        now = now or datetime.now(timezone.utc)
        resolve_window(self._settings, now, None)  # validate before any database I/O
        try:
            async with self._engine.connect() as connection:
                connection = await connection.execution_options(isolation_level="REPEATABLE READ")
                async with connection.begin():
                    return await self.assemble(BriefingHistory(connection), kind, now)
        except QueryBoundsError:
            raise
        except Exception as exc:
            # Never return partial results or echo potentially sensitive SQL parameters.
            logger.error("briefing assembly failed: kind=%s error_type=%s", kind, type(exc).__name__)
            raise BriefingAssemblyError(f"briefing assembly failed: {type(exc).__name__}") from exc

    async def assemble(self, history: BriefingHistory, kind: str, now: datetime) -> dict[str, Any]:
        settings = self._settings
        last = await history.last_delivery(kind, now)
        window = resolve_window(settings, now, last)
        limit = min(settings.briefing_max_candidates, settings.max_query_points,
                    settings.max_event_page_size)
        baseline_start = max(
            window.start - timedelta(days=settings.briefing_novelty_baseline_days),
            now - timedelta(days=settings.max_query_range_days),
        )
        # Sequential reads intentionally share one consistent database snapshot.
        batches = [
            await history.alerts(window.start, now, limit),
            await history.attention(window.start, now, limit),
            await history.transitions(window.start, now, limit),
            await history.events(window.start, now, limit),
            await history.novelty(window.start, now, baseline_start, limit, settings.max_query_points),
        ]
        candidates: list[BriefingCandidate] = []
        query_audit = [{"query": b.query, "start": window.start.isoformat(),
                        "end": now.isoformat(), "limit": limit,
                        "returned": len(b.rows), "truncated": b.truncated} for b in batches]
        query_audit[-1].update(baseline_start=baseline_start.isoformat(),
                               baseline_end=window.start.isoformat(),
                               baseline_scope="entity/measurement/unit; complete hourly buckets",
                               baseline_point_limit=settings.max_query_points)

        def add(batch, row, *, item_id, category, priority, moment, text, source=None,
                evidence=None, novelty=None, spoken=None):
            candidates.append(BriefingCandidate(
                item_id=item_id, category=category, priority=priority,
                text=f"{text} (recorded {_iso(moment)}, {_age_text(now, moment)}; source={source or 'unknown'})",
                reason=f"stored_{category}", entity_id=row.entity_id, source_id=source,
                timestamp=moment.isoformat(), query=batch.query,
                evidence=evidence or {}, novelty_score=novelty,
                spoken_text=spoken,
            ))

        alerts, attention, transitions, events, novelty = batches
        if alerts.truncated and alerts.rows[-1].severity == "critical":
            raise BriefingAssemblyError("critical candidates exceed query bound; no partial briefing")
        if ((attention.truncated and attention.rows[-1].priority <= 1) or
                (events.truncated and events.rows[-1].severity == "critical")):
            raise BriefingAssemblyError("critical candidates exceed query bound; no partial briefing")
        for row in alerts.rows:
            add(alerts, row, item_id=f"alert:{row.alert_id}", category="alert",
                priority=1 if row.severity == "critical" else 2, moment=row.last_updated_at,
                text=f"ALERT {row.entity_id or 'system'} [{row.severity}/{row.status}] {_one_line(row.title)}",
                spoken=("Important: " if row.severity == "critical" else "") + short_text(row.title, "An alert needs your attention."),
                evidence={"alert_id": str(row.alert_id), "source_status": "rule-derived"})
        for row in attention.rows:
            category = "reminder" if row.reminder_id else (
                "agent_outcome" if (row.conversation_relevance or {}).get("kind", "").startswith("agent")
                else "alert")
            add(attention, row, item_id=f"attention:{row.attention_item_id}", category=category,
                priority=max(1, row.priority), moment=row.created_at,
                text=f"PENDING {category} for {row.entity_id or 'owner'}: {_one_line(row.reason)}",
                spoken=short_text(row.reason, "You have a pending reminder." if category == "reminder" else "An item needs your attention."),
                evidence={"attention_item_id": str(row.attention_item_id),
                          "due_at": _iso(row.due_at) if row.due_at else None})
        for row in transitions.rows:
            add(transitions, row, item_id=f"transition:{row.id}", category="transition",
                priority=6, moment=row.occurred_at, source=row.source_id,
                text=f"CHANGE {row.entity_id}.{row.property_name} -> {_one_line(repr((row.to_value or {}).get('value')))} [{row.to_status}]",
                spoken=transition_text(row.entity_id, row.property_name, (row.to_value or {}).get("value"),
                                       row.to_status, (row.from_value or {}).get("value"), row.from_status),
                evidence={"transition_id": row.id, "source_event_id": str(row.source_event_id) if row.source_event_id else None,
                          "observed_at": _iso(row.observed_at), "confidence": row.confidence})
        for row in events.rows:
            category = "agent_outcome" if row.event_type.startswith("agent.") else "interaction"
            # Deliberately render typed event identity, not arbitrary payload text:
            # no utterances, tool arguments, stack traces, or embedded secrets.
            add(events, row, item_id=f"event:{row.event_id}", category=category,
                priority=1 if row.severity == "critical" else (4 if category == "agent_outcome" else 7),
                moment=row.received_at, source=row.source_id,
                text=f"EVENT {row.entity_id or 'unknown entity'}: {row.event_type} [{row.severity}]",
                spoken=event_text(row.event_type),
                evidence={"event_id": str(row.event_id), "observed_at": _iso(row.observed_at),
                          "confidence": row.confidence, "clock_quality": (row.provenance or {}).get("clock_quality", "unknown")})
        unavailable = 0
        for row in novelty.rows:
            score = row.novelty_score
            if score is None or not math.isfinite(score):
                unavailable += 1
                continue
            if score < settings.briefing_novelty_z_threshold:
                continue
            # Full composite measurement key, with JSON encoding to avoid delimiter collisions.
            item_id = "measurement:" + json.dumps([row.time.isoformat(), row.entity_id,
                                                    row.measurement_name, row.source_id], separators=(",", ":"))
            add(novelty, row, item_id=item_id, category="novelty", priority=5,
                moment=row.time, source=row.source_id, novelty=float(score),
                text=f"UNUSUAL {row.entity_id}.{row.measurement_name}={row.value_double} {row.unit or ''}; z={score:.2f} against prior hourly aggregates",
                spoken=novelty_text(row.entity_id, row.measurement_name, row.value_double, row.unit, row.baseline_mean),
                evidence={"source_event_id": str(row.source_event_id) if row.source_event_id else None,
                          "baseline_samples": int(row.baseline_samples),
                          "baseline_mean": row.baseline_mean, "baseline_stddev": row.baseline_stddev,
                          "received_at": _iso(row.received_at), "confidence": row.confidence})
        query_audit[-1]["unscored_missing_constant_or_bounded_baseline"] = unavailable
        from talos.awareness.briefing.feedback import apply_preferences
        unavailable_ids = await history.unavailable([c.item_id for c in candidates])
        eligible = [asdict(c) for c in candidates if c.item_id not in unavailable_ids]
        eligible, preference_audit = apply_preferences(eligible, await history.preferences(eligible, now))
        candidates = [BriefingCandidate(**c) for c in eligible]
        candidates.sort(key=lambda c: (c.priority, -c.relevance, c.item_id))
        kept = candidates[:limit]
        if any(c.priority == 1 for c in candidates[limit:]):
            raise BriefingAssemblyError("critical candidates exceed total bound; no partial briefing")
        return {
            "kind": kind, "as_of": now.isoformat(),
            "window": {"start": window.start.isoformat(), "end": now.isoformat(),
                       "origin": window.origin, "truncated": window.truncated},
            "candidates": [asdict(c) for c in kept],
            "truncated": window.truncated or any(b.truncated for b in batches) or len(candidates) > limit,
            "audit": [{"item_id": c.item_id, "priority": c.priority, "tokens": c.tokens,
                       "included": i < limit, "reason": c.reason if i < limit else "candidate_bound_exceeded"}
                      for i, c in enumerate(candidates)],
            "queries": query_audit,
            "feedback_audit": preference_audit,
            "unavailable_ids": sorted(unavailable_ids),
            "limitations": ["Candidate assembly only; delivery and optional selection run in the briefing outbox.",
                            "Missing or constant baselines are unscored; bounded history cannot prove a first-ever observation.",
                            "Rule-derived attention and freshness transitions may have unknown source attribution.",
                            "Notification confirmation means adapter acceptance, not proof of speech or human receipt."],
        }
