"""Unit tests for Phase 4 building blocks (no database, no network)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from talos.awareness.rules.policy import (
        PolicyError,
        RulePolicy,
        load_policy,
        render_template,
    )
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

from talos.awareness.alerts.service import parse_quiet_hours, quiet_hours_deferral
from talos.awareness.notifications.handler import render_fallback


class PolicyLoadTest(unittest.TestCase):
    def test_default_policy_loads_and_orders_hard_first(self) -> None:
        policy = load_policy()
        self.assertGreaterEqual(policy.version, 1)
        kinds = [rule.kind for rule in policy.ordered_rules()]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: 0 if k == "hard" else 1))

    def test_missing_file_is_a_policy_error(self) -> None:
        with self.assertRaises(PolicyError):
            load_policy(Path("/nonexistent/rules.toml"))

    def test_unknown_fields_rejected(self) -> None:
        with self.assertRaises(Exception):
            RulePolicy(version=1, rules=[], bogus="x")  # type: ignore[call-arg]


class RuleMatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy()
        self.overflow = next(
            rule for rule in self.policy.rules if rule.id == "overflow-critical"
        )
        self.resolved = next(
            rule for rule in self.policy.rules if rule.id == "overflow-resolved"
        )

    def test_overflow_condition_matches(self) -> None:
        self.assertTrue(
            self.overflow.match.matches(
                "plant.overflow.detected", "critical", {"overflow": True}
            )
        )

    def test_overflow_false_matches_resolve_rule_only(self) -> None:
        payload = {"overflow": False}
        self.assertFalse(
            self.overflow.match.matches("plant.overflow.detected", "critical", payload)
        )
        self.assertTrue(
            self.resolved.match.matches("plant.overflow.detected", "info", payload)
        )

    def test_wrong_event_type_does_not_match(self) -> None:
        self.assertFalse(
            self.overflow.match.matches("sim.heartbeat", "critical", {"overflow": True})
        )

    def test_glob_and_severity_matching(self) -> None:
        from talos.awareness.rules.policy import Match

        match = Match(event_type="sim.telemetry.*", min_severity="warning")
        self.assertTrue(match.matches("sim.telemetry.temperature", "critical", {}))
        self.assertFalse(match.matches("sim.telemetry.temperature", "info", {}))
        self.assertFalse(match.matches("sim.state.reported", "critical", {}))

    def test_numeric_conditions(self) -> None:
        from talos.awareness.rules.policy import Condition

        self.assertTrue(Condition(field="v", op="gt", value=5).evaluate({"v": 6}))
        self.assertFalse(Condition(field="v", op="gt", value=5).evaluate({"v": 5}))
        self.assertFalse(Condition(field="v", op="gt", value=5).evaluate({}))
        self.assertFalse(
            Condition(field="v", op="gt", value=5).evaluate({"v": "not-a-number"})
        )
        self.assertTrue(Condition(field="v", op="exists").evaluate({"v": None}))


class TemplateTest(unittest.TestCase):
    def test_renders_fields_and_payload(self) -> None:
        text = render_template(
            "Overflow at {entity_id} zone {payload.zone}",
            {"entity_id": "pot_1", "payload": {"zone": 2}},
        )
        self.assertEqual(text, "Overflow at pot_1 zone 2")

    def test_missing_fields_render_placeholder_not_crash(self) -> None:
        text = render_template(
            "Overflow at {entity_id} zone {payload.zone}", {"payload": {}}
        )
        self.assertEqual(text, "Overflow at ? zone ?")


class QuietHoursTest(unittest.TestCase):
    def test_parse_and_reject(self) -> None:
        self.assertIsNone(parse_quiet_hours(""))
        self.assertIsNotNone(parse_quiet_hours("22:00-07:00"))
        with self.assertRaises(ValueError):
            parse_quiet_hours("bogus")

    def test_wrapping_window_defers_inside_only(self) -> None:
        quiet = parse_quiet_hours("22:00-07:00")
        inside = datetime.now(timezone.utc).astimezone().replace(
            hour=23, minute=0, second=0, microsecond=0
        )
        outside = inside.replace(hour=12)
        deferred = quiet_hours_deferral(inside.astimezone(timezone.utc), quiet)
        self.assertIsNotNone(deferred)
        self.assertGreater(deferred, inside.astimezone(timezone.utc))
        self.assertIsNone(quiet_hours_deferral(outside.astimezone(timezone.utc), quiet))


class FallbackWordingTest(unittest.TestCase):
    def test_deterministic_wording_with_full_fields(self) -> None:
        content = render_fallback(
            severity="critical",
            title="Overflow detected: pot_1",
            description="Overflow reported by sim_device (zone 1).",
            occurrence_count=3,
            first_seen_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            reason=None,
        )
        self.assertEqual(content.title, "[CRITICAL] Overflow detected: pot_1")
        self.assertIn("Occurred 3 times.", content.body)
        self.assertIn("First seen 2026-07-16T12:00:00+00:00.", content.body)

    def test_missing_optional_data_does_not_break(self) -> None:
        content = render_fallback(
            severity="warning",
            title="Source offline: fan_pico",
            description=None,
            occurrence_count=1,
            first_seen_at=None,
            reason=None,
        )
        self.assertEqual(content.body, "Source offline: fan_pico")


if __name__ == "__main__":
    unittest.main()
