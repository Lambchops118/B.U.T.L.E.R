from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos import router
from talos.request_classifier import RequestClassification


class VoiceFastRoutingTests(unittest.TestCase):
    """The voice lane must not make the extra model-based route LLM call."""

    def test_model_route_skipped_when_disallowed(self) -> None:
        with mock.patch.object(
            router.agent_runtime, "classify_request_route"
        ) as mocked_route:
            decision = router._classify_with_context(
                "turn on the office lights",
                source="voice",
                session_id="voice",
                runtime_context="",
                allow_model_route=False,
            )
        mocked_route.assert_not_called()
        # Falls back to the local heuristic, which keeps this conversational.
        self.assertEqual(decision.mode, "foreground")

    def test_model_route_used_when_allowed(self) -> None:
        with mock.patch.object(
            router.agent_runtime,
            "classify_request_route",
            return_value={"mode": "background", "reason": "model route decision", "response": "ok"},
        ) as mocked_route:
            decision = router._classify_with_context(
                "look through the repo and summarize the architecture",
                source="text",
                session_id="text:1",
                runtime_context="",
                allow_model_route=True,
            )
        mocked_route.assert_called_once()
        self.assertEqual(decision.mode, "background")

    def test_explicit_mode_short_circuits_before_model_route(self) -> None:
        with mock.patch.object(
            router.agent_runtime, "classify_request_route"
        ) as mocked_route:
            decision = router._classify_with_context(
                "anything",
                source="text",
                session_id="text:1",
                runtime_context="",
                requested_mode="background",
                allow_model_route=True,
            )
        mocked_route.assert_not_called()
        self.assertEqual(decision.mode, "background")
        self.assertTrue(decision.reason.startswith("explicit "))

    def test_heuristic_still_routes_clear_background_work_on_voice(self) -> None:
        # Even without the model call, obvious multi-step work still goes async.
        with mock.patch.object(router.agent_runtime, "classify_request_route"):
            decision = router._classify_with_context(
                "research the best microcontrollers and write a report",
                source="voice",
                session_id="voice",
                runtime_context="",
                allow_model_route=False,
            )
        self.assertEqual(decision.mode, "background")


if __name__ == "__main__":
    unittest.main()
