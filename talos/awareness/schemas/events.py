"""C1 — Canonical event envelope.

Every piece of information entering the awareness backend is represented by
:class:`EventEnvelope`. Timestamp semantics (never conflated):

- ``observed_at``  — when the originating source says it happened (untrusted
  unless ``provenance.clock_quality`` says otherwise)
- ``received_at``  — when this backend received it (always set by us)
- ``processed_at`` — when pipeline processing completed
- ``expires_at``   — when the data becomes stale/irrelevant

All timestamps must be timezone-aware and are normalized to UTC on
validation. Original-timezone details belong in ``provenance.metadata``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CURRENT_ENVELOPE_SCHEMA_VERSION = 1
SUPPORTED_ENVELOPE_SCHEMA_VERSIONS = frozenset({1})

# Absolute ceiling enforced at model level; the (smaller) configured limit is
# enforced by the ingestion pipeline via ensure_payload_within().
HARD_MAX_PAYLOAD_BYTES = 262_144

Severity = Literal["debug", "info", "notice", "warning", "critical"]
ClockQuality = Literal[
    "unknown",
    "unsynchronized",
    "device_local",
    "device_synced",
    "gateway_stamped",
    "server_received",
]


class PayloadTooLargeError(ValueError):
    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(f"payload is {size_bytes} bytes; limit is {max_bytes} bytes")


def payload_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str))


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transport: str = Field(min_length=1, max_length=64)
    topic_or_endpoint: str | None = Field(default=None, max_length=512)
    gateway_id: str | None = Field(default=None, max_length=200)
    firmware_version: str | None = Field(default=None, max_length=100)
    software_version: str | None = Field(default=None, max_length=100)
    clock_quality: ClockQuality = "unknown"
    clock_offset_ms: float | None = None
    authenticated_identity: str | None = Field(default=None, max_length=200)
    # True when the value arrived as an MQTT retained/replayed message (C2):
    # such values bootstrap last-known state but are never assumed current.
    retained: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    schema_version: int = CURRENT_ENVELOPE_SCHEMA_VERSION
    event_type: str = Field(min_length=1, max_length=200)
    entity_id: str | None = Field(default=None, max_length=200)
    source_id: str = Field(min_length=1, max_length=200)
    location_id: str | None = Field(default=None, max_length=200)

    observed_at: datetime | None = None
    received_at: datetime
    processed_at: datetime | None = None

    sequence: int | None = Field(default=None, ge=0)
    source_boot_id: str | None = Field(default=None, max_length=200)

    correlation_id: str | None = Field(default=None, max_length=200)
    causation_id: str | None = Field(default=None, max_length=200)

    severity: Severity = "info"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    retention_class: str | None = Field(default=None, max_length=100)
    expires_at: datetime | None = None

    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance

    @field_validator("schema_version")
    @classmethod
    def _supported_schema_version(cls, value: int) -> int:
        if value not in SUPPORTED_ENVELOPE_SCHEMA_VERSIONS:
            supported = sorted(SUPPORTED_ENVELOPE_SCHEMA_VERSIONS)
            raise ValueError(f"unsupported envelope schema_version {value}; supported: {supported}")
        return value

    @field_validator("observed_at", "received_at", "processed_at", "expires_at")
    @classmethod
    def _timezone_aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware (UTC)")
        return value.astimezone(timezone.utc)

    @field_validator("event_type", "source_id")
    @classmethod
    def _stripped_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def _payload_within_hard_ceiling(self) -> "EventEnvelope":
        size = payload_size_bytes(self.payload)
        if size > HARD_MAX_PAYLOAD_BYTES:
            raise PayloadTooLargeError(size, HARD_MAX_PAYLOAD_BYTES)
        return self

    def ensure_payload_within(self, max_bytes: int) -> None:
        """Enforce the deployment-configured payload limit (pipeline stage)."""
        size = payload_size_bytes(self.payload)
        if size > max_bytes:
            raise PayloadTooLargeError(size, max_bytes)
