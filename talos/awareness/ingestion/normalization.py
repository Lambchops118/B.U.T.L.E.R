"""Normalize raw transport messages into canonical event envelopes (C1/C2).

Canonical topic scheme (new sources):

    home/{domain}/{entity_id}/state
    home/{domain}/{entity_id}/event
    home/{domain}/{entity_id}/telemetry/{measurement}
    home/{domain}/{entity_id}/health
    home/{domain}/{entity_id}/heartbeat

Message bodies are JSON. Recognized system fields (all optional unless noted):
``event_type`` (required for ``event`` topics), ``event_id`` (UUID),
``observed_at`` (ISO-8601 with timezone), ``sequence`` (int ≥ 0), ``boot_id``,
``severity``, ``confidence``, ``correlation_id``, ``causation_id``, and
``payload`` (object with the domain data). If ``payload`` is absent, all
non-system fields form the payload; a bare JSON scalar becomes
``{"value": <scalar>}``.

Legacy adapter: the deployed Pico W firmware publishes bare pin values on
``status/{pin}``. Sources registered with ``metadata.legacy == "pin_status"``
get those messages normalized to ``device.pin_status.reported`` events.

Timestamp trust: ``observed_at`` is kept only if parseable and
timezone-aware; sources whose registry ``clock_quality`` is untrusted keep it
as evidence but ordering always uses ``received_at`` (stamped by the caller).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from talos.awareness.registry.sources import SourceRecord
from talos.awareness.schemas.events import EventEnvelope, Provenance

_SYSTEM_FIELDS = (
    "event_type",
    "event_id",
    "observed_at",
    "sequence",
    "boot_id",
    "severity",
    "confidence",
    "correlation_id",
    "causation_id",
    "source_id",
    "payload",
)

_UNTRUSTED_CLOCKS = {"unknown", "unsynchronized", "server_received"}


class NormalizationError(Exception):
    """Message cannot become a canonical event; ``reason`` is a dead-letter tag."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def normalize(
    *,
    topic: str,
    payload: bytes,
    retained: bool,
    transport: str,
    source: SourceRecord,
    received_at: datetime,
) -> EventEnvelope:
    if source.metadata.get("legacy") == "pin_status":
        return _normalize_legacy_pin_status(
            topic=topic,
            payload=payload,
            retained=retained,
            transport=transport,
            source=source,
            received_at=received_at,
        )
    if topic.split("/")[0] == "home":
        return _normalize_canonical(
            topic=topic,
            payload=payload,
            retained=retained,
            transport=transport,
            source=source,
            received_at=received_at,
        )
    raise NormalizationError(
        "unsupported_topic", f"no normalization rule for topic {topic!r}"
    )


def _provenance(
    *, transport: str, topic: str, retained: bool, source: SourceRecord, notes: dict[str, Any]
) -> Provenance:
    return Provenance(
        transport=transport,
        topic_or_endpoint=topic,
        clock_quality=source.clock_quality,  # registry-declared trust level
        authenticated_identity=source.source_id,
        retained=retained,
        metadata=notes,
    )


def _normalize_legacy_pin_status(
    *,
    topic: str,
    payload: bytes,
    retained: bool,
    transport: str,
    source: SourceRecord,
    received_at: datetime,
) -> EventEnvelope:
    parts = topic.split("/")
    try:
        pin = int(parts[-1])
    except ValueError as exc:
        raise NormalizationError("unsupported_topic", f"legacy topic without pin: {topic!r}") from exc

    raw_value = payload.decode("utf-8", errors="replace").strip()
    if raw_value not in {"0", "1"}:
        raise NormalizationError(
            "malformed_payload", f"legacy pin status expects '0'/'1', got {raw_value!r}"
        )

    return EventEnvelope(
        event_type="device.pin_status.reported",
        entity_id=source.entity_id,
        source_id=source.source_id,
        location_id=source.location_id,
        received_at=received_at,
        severity="debug",
        payload={
            "pin": pin,
            "raw_value": raw_value,
            "value_inverted": bool(source.metadata.get("value_inverted")),
        },
        provenance=_provenance(
            transport=transport, topic=topic, retained=retained, source=source, notes={}
        ),
    )


def _normalize_canonical(
    *,
    topic: str,
    payload: bytes,
    retained: bool,
    transport: str,
    source: SourceRecord,
    received_at: datetime,
) -> EventEnvelope:
    parts = topic.split("/")
    if len(parts) < 4:
        raise NormalizationError(
            "unsupported_topic", f"expected home/{{domain}}/{{entity}}/{{kind}}, got {topic!r}"
        )
    domain, entity_id, kind = parts[1], parts[2], parts[3]

    body = _parse_body(payload, kind)
    notes: dict[str, Any] = {}

    claimed_source = body.get("source_id")
    if claimed_source is not None and str(claimed_source) != source.source_id:
        raise NormalizationError(
            "source_mismatch",
            f"payload claims source_id {claimed_source!r} but topic {topic!r} "
            f"belongs to {source.source_id!r}",
        )

    event_type = body.get("event_type")
    severity = body.get("severity", "info")
    retention_class = None
    if event_type is None:
        if kind == "state":
            event_type = f"{domain}.state.reported"
        elif kind == "telemetry":
            measurement = parts[4] if len(parts) > 4 else "value"
            event_type = f"{domain}.telemetry.{measurement}"
        elif kind == "health":
            event_type = f"{domain}.health.reported"
        elif kind == "heartbeat":
            event_type = f"{domain}.heartbeat"
            severity = body.get("severity", "debug")
            retention_class = "heartbeat"
        elif kind == "event":
            raise NormalizationError(
                "malformed_payload", "event topics require an explicit event_type"
            )
        else:
            raise NormalizationError(
                "unsupported_topic", f"unknown topic kind {kind!r} in {topic!r}"
            )

    observed_at = _parse_observed_at(body.get("observed_at"), notes)
    if observed_at is not None and source.clock_quality in _UNTRUSTED_CLOCKS:
        # Preserved as evidence only; ordering uses received_at (C1).
        notes["observed_at_untrusted"] = True

    event_id = _parse_event_id(body.get("event_id"))
    sequence = _parse_sequence(body.get("sequence"))

    domain_payload = body.get("payload")
    if not isinstance(domain_payload, dict):
        domain_payload = {
            key: value for key, value in body.items() if key not in _SYSTEM_FIELDS
        }

    envelope_kwargs: dict[str, Any] = {
        "event_type": str(event_type),
        "entity_id": entity_id or source.entity_id,
        "source_id": source.source_id,
        "location_id": source.location_id,
        "received_at": received_at,
        "observed_at": observed_at,
        "sequence": sequence,
        "source_boot_id": _optional_str(body.get("boot_id")),
        "correlation_id": _optional_str(body.get("correlation_id")),
        "causation_id": _optional_str(body.get("causation_id")),
        "severity": severity,
        "retention_class": retention_class,
        "payload": domain_payload,
        "provenance": _provenance(
            transport=transport, topic=topic, retained=retained, source=source, notes=notes
        ),
    }
    if event_id is not None:
        envelope_kwargs["event_id"] = event_id
    if "confidence" in body:
        envelope_kwargs["confidence"] = body["confidence"]

    try:
        return EventEnvelope(**envelope_kwargs)
    except ValidationError as exc:
        raise NormalizationError("invalid_event", _first_error(exc)) from exc


def _parse_body(payload: bytes, kind: str) -> dict[str, Any]:
    if not payload:
        if kind == "heartbeat":
            return {}
        raise NormalizationError("malformed_payload", "empty payload")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NormalizationError("malformed_payload", f"invalid JSON: {exc}") from exc
    if isinstance(parsed, dict):
        return parsed
    return {"payload": {"value": parsed}}


def _parse_observed_at(value: Any, notes: dict[str, Any]) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        notes["invalid_observed_at"] = str(value)[:100]
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        notes["naive_observed_at"] = str(value)[:100]
        return None
    return parsed


def _parse_event_id(value: Any) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise NormalizationError("malformed_payload", f"invalid event_id: {value!r}") from exc


def _parse_sequence(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise NormalizationError("malformed_payload", f"invalid sequence: {value!r}") from exc


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "validation failed"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalid')}"
