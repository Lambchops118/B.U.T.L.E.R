"""Versioned action registry (C14, Phase 7): strict typed TOML definitions.

Every dispatchable action is registered here — MQTT payloads are generated
from these definitions, never from arbitrary model content. Parameters use a
small typed schema (integer enum / boolean) that covers the deployed
devices; anything else is rejected before validation even starts.

``ack_mode`` declares what completion means, per action (silence is never
success):

- ``state_confirmation`` — the device reports the resulting pin/property
  state (the legacy Picos publish ``status/{pin}`` after acting); completion
  requires matching state evidence within the timeout.
- ``command_ack`` — the device publishes an acknowledgement event carrying
  the ``command_id`` (the simulator does); ack means receipt, and completion
  follows the acknowledgement per definition.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RegistryError(Exception):
    """The action registry file is missing, malformed, or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterSpec(_Strict):
    name: str
    type: Literal["integer", "boolean"]
    required: bool = True
    allowed_values: list[int] | None = None
    description: str = ""

    def validate_value(self, value: Any) -> Any:
        if self.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.name} must be an integer")
            if self.allowed_values is not None and value not in self.allowed_values:
                raise ValueError(
                    f"{self.name} must be one of {self.allowed_values}"
                )
            return value
        if not isinstance(value, bool):
            raise ValueError(f"{self.name} must be a boolean")
        return value


class ActionDefinition(_Strict):
    name: str
    description: str = ""
    target_entity_id: str
    parameters: list[ParameterSpec] = Field(default_factory=list)
    allowed_actors: list[str] = Field(default_factory=lambda: ["llm", "operator"])
    confirmation_required: bool = False
    confirmation_ttl_seconds: float = Field(default=120.0, gt=0)
    cooldown_seconds: float = Field(default=0.0, ge=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
    # Topic may reference validated parameters: "quad_pump/{pot_pin}".
    command_topic: str
    # Payload template: "1"/"0" for legacy pins, "envelope" for canonical
    # JSON command envelopes (command_id/idempotency/params included).
    payload: str
    ack_mode: Literal["state_confirmation", "command_ack"]
    # state_confirmation: the current_state property that proves execution.
    confirm_property: str | None = None
    confirm_value: Any | None = None
    # Allowed current states of confirm_property before dispatch (empty = any).
    allowed_prior_values: list[Any] = Field(default_factory=list)
    rollback: Literal["none"] = "none"  # no registered rollbacks exist yet

    def validate_parameters(self, raw: dict[str, Any]) -> dict[str, Any]:
        known = {spec.name for spec in self.parameters}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unsupported parameters: {sorted(unknown)}")
        validated: dict[str, Any] = {}
        for spec in self.parameters:
            if spec.name not in raw:
                if spec.required:
                    raise ValueError(f"missing required parameter {spec.name!r}")
                continue
            validated[spec.name] = spec.validate_value(raw[spec.name])
        return validated

    def render_topic(self, parameters: dict[str, Any]) -> str:
        def _sub(match: re.Match) -> str:
            key = match.group(1)
            if key not in parameters:
                raise ValueError(f"topic references unknown parameter {key!r}")
            return str(parameters[key])

        return re.sub(r"\{([A-Za-z0-9_]+)\}", _sub, self.command_topic)


class ActionRegistry(_Strict):
    version: int = Field(ge=1)
    actions: list[ActionDefinition] = Field(default_factory=list)

    def get(self, name: str) -> ActionDefinition | None:
        for action in self.actions:
            if action.name == name:
                return action
        return None

    def names(self) -> list[str]:
        return [action.name for action in self.actions]


DEFAULT_REGISTRY_PATH = Path(__file__).with_name("actions.toml")


def load_registry(path: Path | None = None) -> ActionRegistry:
    path = path or DEFAULT_REGISTRY_PATH
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"action registry not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"invalid TOML in {path}: {exc}") from exc
    try:
        return ActionRegistry(**raw)
    except Exception as exc:
        raise RegistryError(f"invalid action registry in {path}: {exc}") from exc
