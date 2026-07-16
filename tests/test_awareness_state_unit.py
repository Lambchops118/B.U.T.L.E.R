"""Unit tests for Phase 3 building blocks (no database)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

try:
    from talos.awareness.config import AwarenessSettings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

from talos.awareness.history.telemetry import QueryBoundsError, validate_range
from talos.awareness.registry.sources import SourceRecord
from talos.awareness.schemas.events import EventEnvelope, Provenance
from talos.awareness.state.classification import classify, value_type_of
from talos.awareness.state.manager import comparison_time

RECEIVED_AT = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
OBSERVED_AT = datetime(2026, 7, 16, 11, 59, 30, tzinfo=timezone.utc)


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


def _envelope(event_type: str, payload: dict, entity_id: str | None = "sim_greenhouse") -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        entity_id=entity_id,
        source_id="sim_device",
        received_at=RECEIVED_AT,
        observed_at=OBSERVED_AT,
        payload=payload,
        provenance=Provenance(
            transport="mqtt",
            topic_or_endpoint="home/sim/greenhouse/state",
            clock_quality="device_synced",
        ),
    )


class ClassificationTest(unittest.TestCase):
    def test_telemetry_yields_measurement_and_state(self) -> None:
        effects = classify(
            _envelope("sim.telemetry.temperature", {"value": 71.5, "unit": "F"})
        )
        self.assertEqual(len(effects.telemetry), 1)
        point = effects.telemetry[0]
        self.assertEqual(point.measurement_name, "temperature")
        self.assertEqual(point.value, 71.5)
        self.assertEqual(point.unit, "F")
        self.assertEqual(len(effects.state_updates), 1)
        self.assertEqual(effects.state_updates[0].property_name, "temperature")
        self.assertFalse(effects.heartbeat)

    def test_non_numeric_telemetry_updates_state_only(self) -> None:
        effects = classify(_envelope("sim.telemetry.mode", {"value": "eco"}))
        self.assertEqual(effects.telemetry, ())
        self.assertEqual(effects.state_updates[0].value, "eco")
        self.assertEqual(effects.state_updates[0].value_type, "string")

    def test_state_reported_yields_one_update_per_key(self) -> None:
        effects = classify(
            _envelope("sim.state.reported", {"pump": "on", "level": 3})
        )
        names = {update.property_name for update in effects.state_updates}
        self.assertEqual(names, {"pump", "level"})
        self.assertEqual(effects.telemetry, ())

    def test_legacy_pin_status_respects_inversion(self) -> None:
        effects = classify(
            _envelope(
                "device.pin_status.reported",
                {"pin": 16, "raw_value": "1", "value_inverted": True},
                entity_id="fan",
            )
        )
        update = effects.state_updates[0]
        self.assertEqual(update.property_name, "pin_16")
        self.assertIs(update.value, False)  # active-low: raw 1 means off

    def test_heartbeat_is_liveness_only(self) -> None:
        effects = classify(_envelope("sim.heartbeat", {}))
        self.assertTrue(effects.heartbeat)
        self.assertEqual(effects.state_updates, ())

    def test_no_entity_means_no_state_effect(self) -> None:
        effects = classify(
            _envelope("sim.telemetry.temperature", {"value": 1.0}, entity_id=None)
        )
        self.assertEqual(effects.state_updates, ())
        self.assertEqual(effects.telemetry, ())

    def test_unrecognized_kind_is_history_only(self) -> None:
        effects = classify(_envelope("plant.overflow.detected", {"zone": 1}))
        self.assertEqual(effects.state_updates, ())
        self.assertEqual(effects.telemetry, ())

    def test_value_types(self) -> None:
        self.assertEqual(value_type_of(True), "boolean")
        self.assertEqual(value_type_of(1), "number")
        self.assertEqual(value_type_of(1.5), "number")
        self.assertEqual(value_type_of("x"), "string")
        self.assertEqual(value_type_of({"a": 1}), "object")


class ComparisonTimeTest(unittest.TestCase):
    def test_trusted_clock_uses_observed_at(self) -> None:
        envelope = _envelope("sim.state.reported", {"pump": "on"})
        self.assertEqual(
            comparison_time(envelope, _source(clock_quality="device_synced")),
            OBSERVED_AT,
        )

    def test_untrusted_clock_uses_received_at(self) -> None:
        envelope = _envelope("sim.state.reported", {"pump": "on"})
        for quality in ("server_received", "unknown", "unsynchronized", "device_local"):
            self.assertEqual(
                comparison_time(envelope, _source(clock_quality=quality)),
                RECEIVED_AT,
                quality,
            )


class QueryBoundsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AwarenessSettings(
            _env_file=None, db_password="unit-test", max_query_range_days=7
        )

    def test_valid_range_passes(self) -> None:
        validate_range(RECEIVED_AT, RECEIVED_AT + timedelta(hours=1), self.settings)

    def test_naive_timestamps_rejected(self) -> None:
        with self.assertRaises(QueryBoundsError):
            validate_range(
                RECEIVED_AT.replace(tzinfo=None), RECEIVED_AT, self.settings
            )

    def test_inverted_range_rejected(self) -> None:
        with self.assertRaises(QueryBoundsError):
            validate_range(RECEIVED_AT, RECEIVED_AT, self.settings)

    def test_oversized_range_rejected(self) -> None:
        with self.assertRaises(QueryBoundsError):
            validate_range(
                RECEIVED_AT, RECEIVED_AT + timedelta(days=8), self.settings
            )


if __name__ == "__main__":
    unittest.main()
