"""Strict, versioned rule-policy definitions (C7, Phase 4).

Policies live in a TOML file (``TALOS_AWARENESS_RULES_PATH``, default
``talos/awareness/rules/rules.toml``) validated into the typed models below —
stdlib ``tomllib``, no extra parser dependency. The policy ``version`` is
recorded with every derived alert so behavior stays reproducible, and the
loaded policy is registered in ``schema_registry`` at startup.

Rule anatomy (all deterministic):

- ``kind``: ``hard`` rules are evaluated first and cannot be overridden by
  anything later; ``classification`` rules handle noncritical salience.
- ``match``: exact ``event_type`` or trailing-``*`` prefix glob, optional
  minimum severity, and typed payload conditions (``eq/ne/gt/ge/lt/le/exists``).
- ``actions``: open/update an alert, resolve one deterministically, and/or
  raise an attention item with optional notification intent.

Templates (``{entity_id}``, ``{payload.zone}`` …) render from validated event
fields only; a missing field renders as ``?`` — missing optional display data
must never break a critical notification. No template can execute anything.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from talos.awareness.db.models import SEVERITIES

_SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}


class PolicyError(Exception):
    """The policy file is missing, malformed, or fails validation."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Condition(_Strict):
    field: str
    op: Literal["eq", "ne", "gt", "ge", "lt", "le", "exists"]
    value: Any = None

    def evaluate(self, payload: dict[str, Any]) -> bool:
        present = self.field in payload
        if self.op == "exists":
            return present
        if not present:
            return False
        actual = payload[self.field]
        if self.op == "eq":
            return actual == self.value
        if self.op == "ne":
            return actual != self.value
        try:
            if self.op == "gt":
                return actual > self.value
            if self.op == "ge":
                return actual >= self.value
            if self.op == "lt":
                return actual < self.value
            if self.op == "le":
                return actual <= self.value
        except TypeError:
            return False
        return False


class Match(_Strict):
    event_type: str
    min_severity: str | None = None
    conditions: list[Condition] = Field(default_factory=list)

    @field_validator("min_severity")
    @classmethod
    def _valid_severity(cls, value: str | None) -> str | None:
        if value is not None and value not in _SEVERITY_RANK:
            raise ValueError(f"unknown severity {value!r}")
        return value

    def matches(self, event_type: str, severity: str, payload: dict[str, Any]) -> bool:
        if self.event_type.endswith("*"):
            if not event_type.startswith(self.event_type[:-1]):
                return False
        elif event_type != self.event_type:
            return False
        if self.min_severity is not None:
            if _SEVERITY_RANK.get(severity, 0) < _SEVERITY_RANK[self.min_severity]:
                return False
        return all(condition.evaluate(payload) for condition in self.conditions)


class AlertAction(_Strict):
    alert_type: str
    severity: str
    title: str
    description: str = ""
    deduplication_key: str
    recommended_actions: list[str] = Field(default_factory=list)

    @field_validator("severity")
    @classmethod
    def _valid_severity(cls, value: str) -> str:
        if value not in _SEVERITY_RANK:
            raise ValueError(f"unknown severity {value!r}")
        return value


class ResolveAction(_Strict):
    deduplication_key: str


class AttentionAction(_Strict):
    priority: int = Field(default=5, ge=1, le=10)
    interruptibility: Literal[
        "immediate", "interrupt_when_safe", "next_interaction", "passive"
    ] = "next_interaction"
    reason: str = ""
    preferred_channel: str | None = None
    available_after_seconds: float = Field(default=0.0, ge=0)
    expires_after_seconds: float | None = Field(default=None, gt=0)
    cooldown_key: str | None = None
    cooldown_seconds: float = Field(default=0.0, ge=0)
    notify: bool = False


class Actions(_Strict):
    alert: AlertAction | None = None
    resolve: ResolveAction | None = None
    attention: AttentionAction | None = None


class Rule(_Strict):
    id: str
    description: str = ""
    kind: Literal["hard", "classification"] = "classification"
    match: Match
    actions: Actions


class FreshnessPolicy(_Strict):
    """Alert policy for worker-detected silence (source offline)."""

    enabled: bool = True
    severity: str = "warning"
    critical_source_types: list[str] = Field(default_factory=list)
    attention: AttentionAction | None = None

    @field_validator("severity")
    @classmethod
    def _valid_severity(cls, value: str) -> str:
        if value not in _SEVERITY_RANK:
            raise ValueError(f"unknown severity {value!r}")
        return value


class RulePolicy(_Strict):
    version: int = Field(ge=1)
    rules: list[Rule] = Field(default_factory=list)
    source_offline: FreshnessPolicy = Field(default_factory=FreshnessPolicy)

    def ordered_rules(self) -> list[Rule]:
        """Hard rules first (RULE-001 precedence), stable within each kind."""
        return sorted(self.rules, key=lambda rule: 0 if rule.kind == "hard" else 1)


DEFAULT_POLICY_PATH = Path(__file__).with_name("rules.toml")


def load_policy(path: Path | None = None) -> RulePolicy:
    path = path or DEFAULT_POLICY_PATH
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(f"rule policy file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"invalid TOML in {path}: {exc}") from exc
    try:
        return RulePolicy(**raw)
    except Exception as exc:
        raise PolicyError(f"invalid rule policy in {path}: {exc}") from exc


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")


def render_template(template: str, envelope_fields: dict[str, Any]) -> str:
    """Render ``{field}`` / ``{payload.key}`` placeholders; missing → ``?``.

    Plain token substitution only — no format-spec, attribute access, or
    anything executable.
    """
    flat: dict[str, Any] = {}
    for key, value in envelope_fields.items():
        if key == "payload" and isinstance(value, dict):
            for inner_key, inner_value in value.items():
                flat[f"payload.{inner_key}"] = inner_value
        elif value is not None:
            flat[key] = value

    def _substitute(match) -> str:
        value = flat.get(match.group(1))
        return "?" if value is None else str(value)

    return _PLACEHOLDER.sub(_substitute, template)
