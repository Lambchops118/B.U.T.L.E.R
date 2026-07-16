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
