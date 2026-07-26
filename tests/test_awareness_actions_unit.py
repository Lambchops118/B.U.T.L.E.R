"""Phase 7 action registry, authentication, and simulator unit tests."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from fastapi import HTTPException
    from pydantic import SecretStr

    from talos.awareness.actions.registry import RegistryError, load_registry
    from talos.awareness.api.routes.actions import require_action_auth
    from talos.awareness.simulator.publisher import SimulatedDevice
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


class ActionRegistryTest(unittest.TestCase):
    def test_deployed_definitions_are_explicit_and_bounded(self) -> None:
        registry = load_registry()
        water = registry.get("water_plants")
        simulator = registry.get("sim_command")
        self.assertIsNotNone(water)
        self.assertIsNotNone(simulator)
        self.assertEqual(water.confirm_value, False)
        self.assertEqual(water.idempotency_behavior, "at_most_once")
        self.assertEqual(water.ack_source_id, "quad_pump_pico")
        self.assertEqual(simulator.idempotency_behavior, "device_key")
        self.assertEqual(simulator.ack_semantics, "execution_result")
        with self.assertRaisesRegex(ValueError, "must be <= 1000"):
            simulator.validate_parameters({"setting": 1001})

    def test_registry_rejects_wildcard_command_topics(self) -> None:
        invalid = """
version = 1
[[actions]]
name = "bad"
target_entity_id = "fan"
permission_level = "standard"
allowed_actors = ["llm"]
confirmation_required = false
safety_checks = []
cooldown_seconds = 0
timeout_seconds = 5
idempotency_behavior = "at_most_once"
command_topic = "fan/#"
payload = "1"
ack_mode = "state_confirmation"
ack_semantics = "state_result"
ack_source_id = "fan_pico"
confirm_property = "pin_16"
confirm_value = true
allowed_prior_values = []
rollback = "none"
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.toml"
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaisesRegex(RegistryError, "MQTT wildcards"):
                load_registry(path)


class CanonicalPumpActionTest(unittest.TestCase):
    """run_pump/stop_pump for the rewritten quad-pump firmware."""

    def setUp(self) -> None:
        self.registry = load_registry()
        self.run_pump = self.registry.get("run_pump")
        self.stop_pump = self.registry.get("stop_pump")
        self.assertIsNotNone(self.run_pump)
        self.assertIsNotNone(self.stop_pump)

    def test_definitions_use_the_canonical_command_ack_contract(self) -> None:
        for definition in (self.run_pump, self.stop_pump):
            self.assertEqual(
                definition.command_topic, "home/irrigation/quad_pump/command"
            )
            self.assertEqual(definition.payload, "envelope")
            self.assertEqual(definition.ack_mode, "command_ack")
            self.assertEqual(definition.ack_semantics, "execution_result")
            self.assertEqual(definition.ack_source_id, "quad_pump_canonical")
            self.assertEqual(definition.target_entity_id, "quad_pump")

    def test_retry_policy_stays_at_most_once_until_device_dedup_is_accepted(self) -> None:
        # Command acknowledgements alone do not make a retry safe; the
        # power-loss bench tests gate the switch to device_key.
        self.assertEqual(self.run_pump.idempotency_behavior, "at_most_once")
        self.assertEqual(self.stop_pump.idempotency_behavior, "at_most_once")

    def test_all_four_logical_channels_are_allowed(self) -> None:
        for channel in (1, 2, 3, 4):
            self.assertEqual(
                self.run_pump.validate_parameters({"channel": channel}),
                {"channel": channel},
            )
            self.assertEqual(
                self.stop_pump.validate_parameters({"channel": channel}),
                {"channel": channel},
            )

    def test_gpio_numbers_and_legacy_pins_are_rejected_as_channels(self) -> None:
        for value in (0, 5, 6, 9, 16, 17, 18, 19):
            with self.assertRaises(ValueError):
                self.run_pump.validate_parameters({"channel": value})

    def test_duration_is_optional_and_strictly_bounded(self) -> None:
        self.assertEqual(
            self.run_pump.validate_parameters({"channel": 1, "duration_seconds": 30}),
            {"channel": 1, "duration_seconds": 30},
        )
        # Omitted duration is legal; the firmware applies its 8 s default.
        self.assertEqual(self.run_pump.validate_parameters({"channel": 1}), {"channel": 1})
        for value in (0, -5, 31, 3600):
            with self.assertRaises(ValueError):
                self.run_pump.validate_parameters({"channel": 1, "duration_seconds": value})

    def test_unregistered_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported parameters"):
            self.run_pump.validate_parameters({"channel": 1, "pot_pin": 17})
        with self.assertRaisesRegex(ValueError, "unsupported parameters"):
            self.stop_pump.validate_parameters({"channel": 1, "duration_seconds": 5})

    def test_command_topic_is_fixed_and_carries_no_parameter_substitution(self) -> None:
        rendered = self.run_pump.render_topic({"channel": 3, "duration_seconds": 5})
        self.assertEqual(rendered, "home/irrigation/quad_pump/command")

    def test_timeout_exceeds_the_firmware_hard_maximum_run(self) -> None:
        # A 30 s run must not be reported as timed out while it is still legal.
        self.assertGreater(self.run_pump.timeout_seconds, 30.0)

    def test_run_cooldown_is_scoped_per_channel_not_globally_disabled(self) -> None:
        self.assertEqual(self.run_pump.cooldown_scope, "parameter")
        self.assertEqual(self.run_pump.cooldown_parameter, "channel")
        self.assertGreater(self.run_pump.cooldown_seconds, 0)

    def test_stopping_is_never_rate_limited(self) -> None:
        self.assertEqual(self.stop_pump.cooldown_seconds, 0.0)
        self.assertEqual(self.stop_pump.cooldown_scope, "action")

    def test_legacy_water_plants_is_preserved_unchanged(self) -> None:
        water = self.registry.get("water_plants")
        self.assertIsNotNone(water)
        self.assertEqual(water.command_topic, "quad_pump/{pot_pin}")
        self.assertEqual(water.ack_mode, "state_confirmation")
        self.assertEqual(water.ack_source_id, "quad_pump_pico")
        self.assertEqual(water.cooldown_scope, "action")
        self.assertEqual(
            water.validate_parameters({"pot_pin": 17}), {"pot_pin": 17}
        )


class CooldownScopeValidationTest(unittest.TestCase):
    _TEMPLATE = """
version = 1
[[actions]]
name = "scoped"
target_entity_id = "quad_pump"
permission_level = "standard"
allowed_actors = ["llm"]
confirmation_required = false
safety_checks = []
cooldown_seconds = {cooldown}
cooldown_scope = "{scope}"
{parameter_line}
timeout_seconds = 5
idempotency_behavior = "at_most_once"
command_topic = "home/irrigation/quad_pump/command"
payload = "envelope"
ack_mode = "command_ack"
ack_semantics = "execution_result"
ack_source_id = "quad_pump_canonical"
allowed_prior_values = []
rollback = "none"

[[actions.parameters]]
name = "channel"
type = "integer"
allowed_values = [1, 2, 3, 4]
"""

    def _load(self, *, scope: str, cooldown: str, parameter_line: str):
        body = self._TEMPLATE.format(
            scope=scope, cooldown=cooldown, parameter_line=parameter_line
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.toml"
            path.write_text(body, encoding="utf-8")
            return load_registry(path)

    def test_parameter_scope_requires_a_declared_parameter(self) -> None:
        with self.assertRaisesRegex(RegistryError, "cooldown_parameter"):
            self._load(
                scope="parameter",
                cooldown="60",
                parameter_line='cooldown_parameter = "pot_pin"',
            )

    def test_parameter_scope_requires_a_positive_cooldown(self) -> None:
        with self.assertRaisesRegex(RegistryError, "positive cooldown_seconds"):
            self._load(
                scope="parameter",
                cooldown="0",
                parameter_line='cooldown_parameter = "channel"',
            )

    def test_cooldown_parameter_without_parameter_scope_is_rejected(self) -> None:
        with self.assertRaisesRegex(RegistryError, "cooldown_scope"):
            self._load(
                scope="action",
                cooldown="60",
                parameter_line='cooldown_parameter = "channel"',
            )

    def test_action_scope_remains_the_default(self) -> None:
        registry = self._load(scope="action", cooldown="60", parameter_line="")
        self.assertEqual(registry.get("scoped").cooldown_scope, "action")
        self.assertIsNone(registry.get("scoped").cooldown_parameter)


class ActionAuthTest(unittest.TestCase):
    @staticmethod
    def _request(token: str | None):
        settings = SimpleNamespace(api_token=SecretStr(token) if token else None)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))

    def test_action_mutations_fail_closed_without_configured_token(self) -> None:
        with self.assertRaises(HTTPException) as context:
            asyncio.run(require_action_auth(self._request(None), None))
        self.assertEqual(context.exception.status_code, 503)

    def test_action_mutations_require_matching_bearer(self) -> None:
        request = self._request("phase-seven-token")
        with self.assertRaises(HTTPException) as context:
            asyncio.run(require_action_auth(request, "Bearer wrong-token"))
        self.assertEqual(context.exception.status_code, 401)
        asyncio.run(require_action_auth(request, "Bearer phase-seven-token"))


class ActionSimulatorTest(unittest.TestCase):
    def test_command_ack_is_an_explicit_execution_result(self) -> None:
        message = SimulatedDevice().command_ack("cmd-123")[0]
        body = json.loads(message.payload)
        self.assertEqual(body["event_type"], "sim.command_ack")
        self.assertEqual(
            body["payload"],
            {"command_id": "cmd-123", "ok": True, "result": "executed"},
        )


if __name__ == "__main__":
    unittest.main()
