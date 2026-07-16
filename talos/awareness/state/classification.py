"""Deterministic event-kind classification for state/telemetry effects (Phase 3).

Derives storage effects from the canonical event type alone — no rules
engine (Phase 4) and no model calls. An event with no recognizable kind still
lands in immutable history; it simply has no state or telemetry effect yet.

    *.telemetry.{measurement}   -> numeric measurement + state property
    *.state.reported            -> one state property per payload key
    device.pin_status.reported  -> boolean "pin_{pin}" property (legacy Picos)
    *.health.reported           -> "health" state property
    *.heartbeat                 -> source liveness only (handled at persist)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from talos.awareness.schemas.events import EventEnvelope


@dataclass(frozen=True)
class TelemetryPoint:
    entity_id: str
    measurement_name: str
    value: float
    unit: str | None
    quality: str | None


@dataclass(frozen=True)
class StateUpdate:
    entity_id: str
    property_name: str
    value: Any
    value_type: str


@dataclass(frozen=True)
class EventEffects:
    state_updates: tuple[StateUpdate, ...] = ()
    telemetry: tuple[TelemetryPoint, ...] = ()
    heartbeat: bool = False


def value_type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def classify(envelope: EventEnvelope) -> EventEffects:
    event_type = envelope.event_type
    entity_id = envelope.entity_id
    payload = envelope.payload or {}

    if event_type.endswith(".heartbeat"):
        return EventEffects(heartbeat=True)

    if entity_id is None:
        # Nothing to attach state or telemetry to; history retains the event.
        return EventEffects()

    if ".telemetry." in event_type:
        measurement = event_type.rsplit(".", 1)[-1]
        value = payload.get("value")
        updates = (
            StateUpdate(entity_id, measurement, value, value_type_of(value)),
        )
        telemetry: tuple[TelemetryPoint, ...] = ()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            telemetry = (
                TelemetryPoint(
                    entity_id=entity_id,
                    measurement_name=measurement,
                    value=float(value),
                    unit=_optional_str(payload.get("unit")),
                    quality=_optional_str(payload.get("quality")),
                ),
            )
        return EventEffects(state_updates=updates, telemetry=telemetry)

    if event_type == "device.pin_status.reported":
        pin = payload.get("pin")
        raw_value = str(payload.get("raw_value", "")).strip()
        if pin is None or raw_value not in {"0", "1"}:
            return EventEffects()
        active = (raw_value == "1") != bool(payload.get("value_inverted"))
        return EventEffects(
            state_updates=(
                StateUpdate(entity_id, f"pin_{pin}", active, "boolean"),
            )
        )

    if event_type.endswith(".state.reported"):
        updates = tuple(
            StateUpdate(entity_id, str(key), value, value_type_of(value))
            for key, value in payload.items()
        )
        return EventEffects(state_updates=updates)

    if event_type.endswith(".health.reported"):
        value = payload if len(payload) > 1 else payload.get("value", payload)
        return EventEffects(
            state_updates=(
                StateUpdate(entity_id, "health", value, value_type_of(value)),
            )
        )

    return EventEffects()


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
