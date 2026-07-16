"""Legacy home-automation tool names use the validated Phase 7 action API."""

from __future__ import annotations

import unittest
from unittest import mock

from talos.services import home_automation


class HomeAutomationActionsTest(unittest.TestCase):
    def test_water_plants_maps_pot_and_reports_unconfirmed_lifecycle(self) -> None:
        with mock.patch.object(
            home_automation.awareness_client,
            "post_json",
            return_value={
                "accepted": True,
                "action_request_id": "request-1",
                "status": "approved",
            },
        ) as post:
            result = home_automation.water_plants(
                2, idempotency_key="water-intent-1"
            )
        post.assert_called_once_with(
            "/actions/request",
            {
                "action": "water_plants",
                "parameters": {"pot_pin": 19},
                "actor": "llm",
                "correlation_id": None,
                "idempotency_key": "water-intent-1",
            },
        )
        self.assertIn("request-1 is approved", result)
        self.assertIn("not confirmed yet", result)

    def test_toggle_fan_rejects_invalid_value_before_api(self) -> None:
        with mock.patch.object(home_automation.awareness_client, "post_json") as post:
            with self.assertRaisesRegex(ValueError, "0 or 1"):
                home_automation.toggle_fan(2)
        post.assert_not_called()

    def test_action_rejection_is_truthful(self) -> None:
        with mock.patch.object(
            home_automation.awareness_client,
            "post_json",
            return_value={"accepted": False, "reason": "cooldown"},
        ):
            with self.assertRaisesRegex(RuntimeError, "cooldown"):
                home_automation.toggle_fan(1, idempotency_key="fan-intent-1")


if __name__ == "__main__":
    unittest.main()
