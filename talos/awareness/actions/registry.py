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
  the ``command_id`` (the simulator does); ``ack_semantics`` distinguishes a
  receipt-only acknowledgement from an execution result that may complete.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RegistryError(Exception):
    """The action registry file is missing, malformed, or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterSpec(_Strict):
    name: str
    type: Literal["integer", "boolean"]
    required: bool = True
    allowed_values: list[int] | None = None
    minimum: int | None = None
    maximum: int | None = None
    description: str = ""

    def validate_value(self, value: Any) -> Any:
        if self.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.name} must be an integer")
            if self.allowed_values is not None and value not in self.allowed_values:
                raise ValueError(
                    f"{self.name} must be one of {self.allowed_values}"
                )
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"{self.name} must be >= {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"{self.name} must be <= {self.maximum}")
            return value
        if not isinstance(value, bool):
            raise ValueError(f"{self.name} must be a boolean")
        return value


class ActionDefinition(_Strict):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = ""
    target_entity_id: str = Field(min_length=1, max_length=200)
    parameters: list[ParameterSpec] = Field(default_factory=list)
    permission_level: Literal["standard", "elevated", "critical"]
    allowed_actors: list[str] = Field(min_length=1)
    confirmation_required: bool = False
    confirmation_ttl_seconds: float = Field(default=120.0, gt=0)
    safety_checks: list[Literal["allowed_state"]]
    cooldown_seconds: float = Field(default=0.0, ge=0)
    timeout_seconds: float = Field(default=30.0, gt=0)
    idempotency_behavior: Literal["at_most_once", "device_key"]
    # Topic may reference validated parameters: "quad_pump/{pot_pin}".
    command_topic: str = Field(min_length=1, max_length=500)
    # Payload template: "1"/"0" for legacy pins, "envelope" for canonical
    # JSON command envelopes (command_id/idempotency/params included).
    payload: str
    ack_mode: Literal["state_confirmation", "command_ack"]
    ack_semantics: Literal["state_result", "execution_result", "receipt_only"]
    ack_source_id: str = Field(min_length=1, max_length=200)
    # state_confirmation: the current_state property that proves execution.
    confirm_property: str | None = None
    confirm_value: Any | None = None
    # Allowed current states of confirm_property before dispatch (empty = any).
    allowed_prior_values: list[Any] = Field(default_factory=list)
    rollback: Literal["none"] = "none"  # no registered rollbacks exist yet

    @model_validator(mode="after")
    def validate_definition(self) -> "ActionDefinition":
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameter names must be unique")
        if len(self.allowed_actors) != len(set(self.allowed_actors)):
            raise ValueError("allowed_actors must be unique")
        if any(not actor.strip() or len(actor) > 100 for actor in self.allowed_actors):
            raise ValueError("allowed_actors entries must be 1-100 characters")
        if any(character in self.command_topic for character in ("#", "+")):
            raise ValueError("command_topic cannot contain MQTT wildcards")

        placeholders: set[str] = set()
        for template in (
            self.command_topic,
            self.payload,
            self.confirm_property or "",
            self.confirm_value if isinstance(self.confirm_value, str) else "",
        ):
            placeholders.update(re.findall(r"\{([A-Za-z0-9_]+)\}", template))
        unknown = placeholders - set(parameter_names)
        if unknown:
            raise ValueError(f"templates reference unknown parameters: {sorted(unknown)}")

        if self.ack_mode == "state_confirmation":
            if self.confirm_property is None or self.confirm_value is None:
                raise ValueError(
                    "state_confirmation requires confirm_property and confirm_value"
                )
            if self.ack_semantics != "state_result":
                raise ValueError("state_confirmation requires ack_semantics='state_result'")
        elif self.ack_semantics == "state_result":
            raise ValueError("command_ack cannot use state_result semantics")

        if "allowed_state" in self.safety_checks:
            if not self.allowed_prior_values or not self.confirm_property:
                raise ValueError(
                    "allowed_state safety check requires confirm_property and "
                    "allowed_prior_values"
                )
        elif self.allowed_prior_values:
            raise ValueError(
                "allowed_prior_values requires the allowed_state safety check"
            )

        if self.idempotency_behavior == "device_key" and self.payload != "envelope":
            raise ValueError(
                "device_key idempotency requires an envelope payload carrying the key"
            )
        return self

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

    @model_validator(mode="after")
    def unique_actions(self) -> "ActionRegistry":
        names = [action.name for action in self.actions]
        if len(names) != len(set(names)):
            raise ValueError("action names must be unique")
        return self

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
