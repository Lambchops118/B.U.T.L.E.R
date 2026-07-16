"""Unit tests for ingestion building blocks (no broker, no database)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

try:
    from talos.awareness.ingestion.mqtt_client import backoff_delay
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

from talos.awareness.ingestion.normalization import NormalizationError, normalize
from talos.awareness.ingestion.pipeline import IngestionMetrics
from talos.awareness.ingestion.sequence import assess_sequence
from talos.awareness.registry.sources import SourceRecord, topic_matches

RECEIVED_AT = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _source(**overrides) -> SourceRecord:
    values = {
        "source_id": "sim_device",
        "source_type": "simulator",
        "transport": "mqtt",
        "entity_id": "sim_greenhouse",
        "location_id": "home",
        "schema_version": 1,
        "clock_quality": "device_synced",
        "enabled": True,
        "allowed_topics": ("home/sim/#",),
        "last_sequence": None,
        "last_boot_id": None,
        "metadata": {},
    }
    values.update(overrides)
    return SourceRecord(**values)


def _normalize(topic: str, payload: bytes, source: SourceRecord, retained: bool = False):
    return normalize(
        topic=topic,
        payload=payload,
        retained=retained,
        transport="mqtt",
        source=source,
        received_at=RECEIVED_AT,
    )


class TopicMatchingTest(unittest.TestCase):
    def test_exact_match(self) -> None:
        self.assertTrue(topic_matches("status/16", "status/16"))
        self.assertFalse(topic_matches("status/16", "status/160"))

    def test_multi_level_wildcard(self) -> None:
        self.assertTrue(topic_matches("home/sim/#", "home/sim/greenhouse/heartbeat"))
        self.assertTrue(topic_matches("home/sim/#", "home/sim/x"))
        self.assertFalse(topic_matches("home/sim/#", "home/other/x"))
        self.assertTrue(topic_matches("#", "anything/at/all"))

    def test_single_level_wildcard(self) -> None:
        self.assertTrue(topic_matches("home/+/greenhouse/state", "home/sim/greenhouse/state"))
        self.assertFalse(topic_matches("home/+/greenhouse/state", "home/sim/other/state"))
        self.assertFalse(topic_matches("home/+", "home/sim/greenhouse"))


class SequenceAssessmentTest(unittest.TestCase):
    def test_no_sequence_is_neutral(self) -> None:
        assessment = assess_sequence(5, "boot-a", None, None)
        self.assertFalse(assessment.advance)
        self.assertFalse(assessment.treat_as_duplicate)

    def test_first_message_advances(self) -> None:
        assessment = assess_sequence(None, None, 1, "boot-a")
        self.assertTrue(assessment.advance)
        self.assertEqual(assessment.arrival_notes(), {})

    def test_boot_change_is_reset_not_out_of_order(self) -> None:
        assessment = assess_sequence(900, "boot-a", 1, "boot-b")
        self.assertTrue(assessment.boot_reset)
        self.assertFalse(assessment.out_of_order)
        self.assertTrue(assessment.advance)

    def test_same_sequence_same_boot_is_duplicate(self) -> None:
        assessment = assess_sequence(7, "boot-a", 7, "boot-a")
        self.assertTrue(assessment.treat_as_duplicate)
        self.assertFalse(assessment.advance)

    def test_lower_sequence_is_out_of_order_and_does_not_advance(self) -> None:
        assessment = assess_sequence(10, "boot-a", 8, "boot-a")
        self.assertTrue(assessment.out_of_order)
        self.assertFalse(assessment.advance)

    def test_jump_reports_gap(self) -> None:
        assessment = assess_sequence(10, "boot-a", 16, "boot-a")
        self.assertEqual(assessment.gap_before, 5)
        self.assertTrue(assessment.advance)

    def test_consecutive_has_no_gap(self) -> None:
        assessment = assess_sequence(10, "boot-a", 11, "boot-a")
        self.assertIsNone(assessment.gap_before)


class NormalizationTest(unittest.TestCase):
    def test_scalar_telemetry_wrapped_as_value(self) -> None:
        envelope = _normalize(
            "home/sim/greenhouse/telemetry/temperature", b"23.5", _source()
        )
        self.assertEqual(envelope.event_type, "sim.telemetry.temperature")
        self.assertEqual(envelope.payload, {"value": 23.5})
        self.assertEqual(envelope.entity_id, "greenhouse")
        self.assertEqual(envelope.source_id, "sim_device")

    def test_state_topic_gets_default_event_type(self) -> None:
        envelope = _normalize(
            "home/sim/greenhouse/state", b'{"payload": {"pump": "on"}}', _source()
        )
        self.assertEqual(envelope.event_type, "sim.state.reported")
        self.assertEqual(envelope.payload, {"pump": "on"})

    def test_event_topic_requires_event_type(self) -> None:
        with self.assertRaises(NormalizationError) as ctx:
            _normalize("home/sim/greenhouse/event", b'{"payload": {}}', _source())
        self.assertEqual(ctx.exception.reason, "malformed_payload")

    def test_heartbeat_allows_empty_payload(self) -> None:
        envelope = _normalize("home/sim/greenhouse/heartbeat", b"", _source())
        self.assertEqual(envelope.event_type, "sim.heartbeat")
        self.assertEqual(envelope.severity, "debug")
        self.assertEqual(envelope.retention_class, "heartbeat")

    def test_source_spoofing_rejected(self) -> None:
        body = b'{"event_type": "x.y", "source_id": "fan_pico", "payload": {}}'
        with self.assertRaises(NormalizationError) as ctx:
            _normalize("home/sim/greenhouse/event", body, _source())
        self.assertEqual(ctx.exception.reason, "source_mismatch")

    def test_invalid_json_rejected(self) -> None:
        with self.assertRaises(NormalizationError) as ctx:
            _normalize("home/sim/greenhouse/event", b"{nope", _source())
        self.assertEqual(ctx.exception.reason, "malformed_payload")

    def test_naive_observed_at_dropped_with_note(self) -> None:
        body = b'{"value": 1, "observed_at": "2026-07-15T08:00:00"}'
        envelope = _normalize("home/sim/greenhouse/telemetry/temperature", body, _source())
        self.assertIsNone(envelope.observed_at)
        self.assertIn("naive_observed_at", envelope.provenance.metadata)

    def test_untrusted_clock_keeps_observed_at_as_evidence(self) -> None:
        body = b'{"value": 1, "observed_at": "2026-07-15T08:00:00+00:00"}'
        envelope = _normalize(
            "home/sim/greenhouse/telemetry/temperature",
            body,
            _source(clock_quality="unknown"),
        )
        self.assertIsNotNone(envelope.observed_at)
        self.assertTrue(envelope.provenance.metadata.get("observed_at_untrusted"))

    def test_legacy_pin_status(self) -> None:
        source = _source(
            source_id="fan_pico",
            entity_id="fan",
            clock_quality="server_received",
            allowed_topics=("status/16",),
            metadata={"legacy": "pin_status", "pin": 16, "value_inverted": True},
        )
        envelope = _normalize("status/16", b"0", source)
        self.assertEqual(envelope.event_type, "device.pin_status.reported")
        self.assertEqual(
            envelope.payload, {"pin": 16, "raw_value": "0", "value_inverted": True}
        )
        self.assertEqual(envelope.entity_id, "fan")
        self.assertEqual(envelope.severity, "debug")

    def test_legacy_pin_status_rejects_garbage(self) -> None:
        source = _source(metadata={"legacy": "pin_status"}, allowed_topics=("status/16",))
        with self.assertRaises(NormalizationError) as ctx:
            _normalize("status/16", b"banana", source)
        self.assertEqual(ctx.exception.reason, "malformed_payload")

    def test_unknown_topic_shape_rejected(self) -> None:
        with self.assertRaises(NormalizationError) as ctx:
            _normalize("weird/thing", b"{}", _source(allowed_topics=("weird/#",)))
        self.assertEqual(ctx.exception.reason, "unsupported_topic")

    def test_retained_flag_lands_in_provenance(self) -> None:
        envelope = _normalize(
            "home/sim/greenhouse/heartbeat", b"", _source(), retained=True
        )
        self.assertTrue(envelope.provenance.retained)


class BackoffTest(unittest.TestCase):
    def test_backoff_is_bounded_with_jitter(self) -> None:
        for attempt in range(0, 12):
            for _ in range(50):
                delay = backoff_delay(attempt)
                self.assertGreater(delay, 0.0)
                self.assertLessEqual(delay, 90.0)  # cap 60 * 1.5 jitter


class _BrokenEngine:
    """Stands in for an AsyncEngine whose database is unreachable."""

    def connect(self):
        raise RuntimeError("database down")

    def begin(self):
        raise RuntimeError("database down")


class PipelineNeverRaisesTest(unittest.TestCase):
    def test_database_outage_does_not_kill_intake(self) -> None:
        """A dead-letter failure is logged and swallowed (INV-14): the MQTT
        intake loop must survive a database outage."""
        import asyncio
        import logging

        from talos.awareness.config import AwarenessSettings
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.registry.sources import SourceRepository

        settings = AwarenessSettings(_env_file=None, db_password="unit-test")
        engine = _BrokenEngine()
        pipeline = IngestionPipeline(engine, SourceRepository(engine), settings)
        message = InboundMessage(topic="home/sim/greenhouse/heartbeat", payload=b"{}")

        logging.disable(logging.CRITICAL)
        try:
            disposition = asyncio.run(pipeline.handle(message))
        finally:
            logging.disable(logging.NOTSET)

        # The dead-letter recorder swallows its own persistence failure, so
        # the message is classified internal_error and nothing propagates.
        self.assertEqual(disposition, "dead_letter:internal_error")
        self.assertEqual(pipeline.metrics.received, 1)
        self.assertEqual(pipeline.metrics.dead_lettered.get("internal_error"), 1)


class MetricsTest(unittest.TestCase):
    def test_dead_letter_counting_and_snapshot(self) -> None:
        metrics = IngestionMetrics()
        metrics.record_dead_letter("malformed_payload")
        metrics.record_dead_letter("malformed_payload")
        metrics.record_dead_letter("unauthorized_topic")
        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["dead_lettered"]["malformed_payload"], 2)
        self.assertEqual(snapshot["dead_lettered"]["unauthorized_topic"], 1)


if __name__ == "__main__":
    unittest.main()
