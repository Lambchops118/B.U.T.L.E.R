"""Unit tests for the canonical event envelope (C1)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID

try:
    from pydantic import ValidationError
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

from talos.awareness.schemas.events import (
    HARD_MAX_PAYLOAD_BYTES,
    EventEnvelope,
    PayloadTooLargeError,
    Provenance,
    payload_size_bytes,
)


def _valid_kwargs(**overrides):
    kwargs = {
        "event_type": "fan.state.changed",
        "source_id": "fan_pico",
        "received_at": datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc),
        "provenance": Provenance(transport="mqtt", topic_or_endpoint="status/16"),
    }
    kwargs.update(overrides)
    return kwargs


class EnvelopeValidationTest(unittest.TestCase):
    def test_minimal_envelope_defaults(self) -> None:
        envelope = EventEnvelope(**_valid_kwargs())
        self.assertIsInstance(envelope.event_id, UUID)
        self.assertEqual(envelope.schema_version, 1)
        self.assertEqual(envelope.severity, "info")
        self.assertEqual(envelope.confidence, 1.0)
        self.assertIsNone(envelope.observed_at)
        self.assertEqual(envelope.payload, {})
        self.assertFalse(envelope.provenance.retained)

    def test_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            EventEnvelope(**_valid_kwargs(received_at=datetime(2026, 7, 15, 12, 0, 0)))
        self.assertIn("timezone-aware", str(ctx.exception))

    def test_timestamps_normalized_to_utc(self) -> None:
        eastern = timezone(timedelta(hours=-4))
        envelope = EventEnvelope(
            **_valid_kwargs(
                received_at=datetime(2026, 7, 15, 8, 0, 0, tzinfo=eastern),
                observed_at=datetime(2026, 7, 15, 7, 59, 0, tzinfo=eastern),
            )
        )
        self.assertEqual(envelope.received_at.utcoffset(), timedelta(0))
        self.assertEqual(envelope.received_at.hour, 12)
        self.assertEqual(envelope.observed_at.utcoffset(), timedelta(0))

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EventEnvelope(**_valid_kwargs(surprise="nope"))

    def test_invalid_severity_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EventEnvelope(**_valid_kwargs(severity="catastrophic"))

    def test_confidence_bounds_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            EventEnvelope(**_valid_kwargs(confidence=1.5))
        with self.assertRaises(ValidationError):
            EventEnvelope(**_valid_kwargs(confidence=-0.1))

    def test_unsupported_schema_version_rejected(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            EventEnvelope(**_valid_kwargs(schema_version=99))
        self.assertIn("unsupported envelope schema_version", str(ctx.exception))

    def test_blank_event_type_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EventEnvelope(**_valid_kwargs(event_type="   "))

    def test_negative_sequence_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EventEnvelope(**_valid_kwargs(sequence=-1))

    def test_invalid_clock_quality_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Provenance(transport="mqtt", clock_quality="atomic")


class PayloadBoundsTest(unittest.TestCase):
    def test_payload_over_hard_ceiling_rejected(self) -> None:
        oversized = {"blob": "x" * (HARD_MAX_PAYLOAD_BYTES + 10)}
        with self.assertRaises(ValidationError):
            EventEnvelope(**_valid_kwargs(payload=oversized))

    def test_configured_limit_enforced_via_helper(self) -> None:
        envelope = EventEnvelope(**_valid_kwargs(payload={"blob": "x" * 2048}))
        with self.assertRaises(PayloadTooLargeError) as ctx:
            envelope.ensure_payload_within(1024)
        self.assertGreater(ctx.exception.size_bytes, 1024)
        envelope.ensure_payload_within(65536)  # within limit: no exception

    def test_payload_size_measures_compact_json(self) -> None:
        self.assertEqual(payload_size_bytes({}), 2)
        self.assertEqual(payload_size_bytes({"a": 1}), len('{"a":1}'))


if __name__ == "__main__":
    unittest.main()
