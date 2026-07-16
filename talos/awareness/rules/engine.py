"""Deterministic rule evaluation inside the ingestion transaction (C7).

``apply_event`` runs for every accepted event, after state/telemetry effects,
in the same transaction — alert/attention/outbox intent commits atomically
with the event (INGEST-005) and involves no network or model calls. Hard
rules evaluate before classification rules and nothing can override them;
there is no LLM classifier in this phase (RULE-004 allows an optional async
one, deliberately not implemented — documented limitation, not a stub).

``apply_source_offline`` / ``apply_source_recovered`` are the freshness
worker's alert interface: silence severity follows policy and source type,
never an automatic critical (REG-004).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from talos.awareness.alerts.service import AlertService
from talos.awareness.logging_utils import get_logger
from talos.awareness.rules.policy import RulePolicy, render_template
from talos.awareness.schemas.events import EventEnvelope

logger = get_logger("talos.awareness.rules")


class RuleEngine:
    def __init__(self, policy: RulePolicy, alerts: AlertService) -> None:
        self._policy = policy
        self._alerts = alerts

    def status(self) -> dict[str, Any]:
        return {
            "policy_version": self._policy.version,
            "rules": [rule.id for rule in self._policy.ordered_rules()],
            "source_offline_enabled": self._policy.source_offline.enabled,
        }

    @property
    def policy(self) -> RulePolicy:
        return self._policy

    async def apply_event(
        self,
        connection,
        envelope: EventEnvelope,
        resolved_entity_id: str | None,
    ) -> dict[str, int]:
        """Evaluate all rules against one accepted event; returns counters."""
        fields = {
            "entity_id": resolved_entity_id or envelope.entity_id,
            "source_id": envelope.source_id,
            "location_id": envelope.location_id,
            "event_type": envelope.event_type,
            "severity": envelope.severity,
            "payload": envelope.payload or {},
        }
        now = datetime.now(timezone.utc)
        counters = {"alerts_opened": 0, "alerts_updated": 0, "alerts_resolved": 0, "attention": 0}

        for rule in self._policy.ordered_rules():
            if not rule.match.matches(
                envelope.event_type, envelope.severity, envelope.payload or {}
            ):
                continue
            rule_meta = {"rule_id": rule.id, "policy_version": self._policy.version}

            alert_id: UUID | None = None
            alert_severity = envelope.severity
            if rule.actions.alert is not None:
                action = rule.actions.alert
                alert_severity = action.severity
                alert_id, created = await self._alerts.open_or_update(
                    connection,
                    deduplication_key=render_template(action.deduplication_key, fields),
                    alert_type=action.alert_type,
                    severity=action.severity,
                    title=render_template(action.title, fields),
                    description=render_template(action.description, fields),
                    entity_id=resolved_entity_id,
                    location_id=envelope.location_id,
                    evidence_event_id=envelope.event_id,
                    recommended_actions=list(action.recommended_actions),
                    metadata=rule_meta,
                    now=now,
                )
                counters["alerts_opened" if created else "alerts_updated"] += 1

            if rule.actions.resolve is not None:
                counters["alerts_resolved"] += await self._alerts.resolve_by_key(
                    connection,
                    deduplication_key=render_template(
                        rule.actions.resolve.deduplication_key, fields
                    ),
                    now=now,
                    reason=f"rule {rule.id} (policy v{self._policy.version})",
                    evidence_event_id=envelope.event_id,
                )

            if rule.actions.attention is not None:
                created_attention = await self._apply_attention(
                    connection,
                    rule.actions.attention,
                    fields,
                    alert_id=alert_id,
                    entity_id=resolved_entity_id,
                    severity=alert_severity,
                    now=now,
                )
                counters["attention"] += created_attention
        return counters

    async def apply_source_offline(
        self,
        connection,
        *,
        source_id: str,
        source_type: str,
        silence_seconds: float,
    ) -> None:
        policy = self._policy.source_offline
        if not policy.enabled:
            return
        severity = (
            "critical" if source_type in policy.critical_source_types else policy.severity
        )
        now = datetime.now(timezone.utc)
        fields = {"source_id": source_id, "payload": {}}
        alert_id, _ = await self._alerts.open_or_update(
            connection,
            deduplication_key=f"source_offline:{source_id}",
            alert_type="source_offline",
            severity=severity,
            title=f"Source offline: {source_id}",
            description=(
                f"{source_id} has been silent for {silence_seconds:.0f} seconds."
            ),
            entity_id=None,
            location_id=None,
            evidence_event_id=None,
            recommended_actions=["Check device power and network"],
            metadata={"policy_version": self._policy.version, "rule_id": "source_offline"},
            now=now,
        )
        if policy.attention is not None:
            await self._apply_attention(
                connection,
                policy.attention,
                fields,
                alert_id=alert_id,
                entity_id=None,
                severity=severity,
                now=now,
            )

    async def apply_source_recovered(self, connection, *, source_id: str) -> int:
        return await self._alerts.resolve_by_key(
            connection,
            deduplication_key=f"source_offline:{source_id}",
            now=datetime.now(timezone.utc),
            reason="source reported again",
        )

    async def _apply_attention(
        self,
        connection,
        action,
        fields: dict[str, Any],
        *,
        alert_id: UUID | None,
        entity_id: str | None,
        severity: str,
        now: datetime,
    ) -> int:
        reason = render_template(action.reason, fields) or "attention"
        attention_id = await self._alerts.raise_attention(
            connection,
            alert_id=alert_id,
            entity_id=entity_id,
            severity=severity,
            reason=reason,
            priority=action.priority,
            interruptibility=action.interruptibility,
            preferred_channel=action.preferred_channel,
            available_after_seconds=action.available_after_seconds,
            expires_after_seconds=action.expires_after_seconds,
            cooldown_key=(
                render_template(action.cooldown_key, fields)
                if action.cooldown_key
                else None
            ),
            cooldown_seconds=action.cooldown_seconds,
            notify=action.notify,
            notification_payload={"severity": severity, "reason": reason},
            now=now,
        )
        return 1 if attention_id is not None else 0
