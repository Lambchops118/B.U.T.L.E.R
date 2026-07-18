from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import thinking


class ResolveThinkModeTests(unittest.TestCase):
    def test_defaults_to_auto_when_unset(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("TALOS_LLM_THINK_MODE", None)
            self.assertEqual(thinking.resolve_think_mode(), "auto")

    def test_unknown_value_falls_back_to_auto(self):
        with mock.patch.dict("os.environ", {"TALOS_LLM_THINK_MODE": "banana"}):
            self.assertEqual(thinking.resolve_think_mode(), "auto")

    def test_reads_valid_value_case_insensitively(self):
        with mock.patch.dict("os.environ", {"TALOS_LLM_THINK_MODE": "  NEVER "}):
            self.assertEqual(thinking.resolve_think_mode(), "never")


class WantsThinkingTests(unittest.TestCase):
    def test_quick_device_command_does_not_think(self):
        self.assertFalse(thinking.wants_thinking("turn on the kitchen lights"))

    def test_status_and_chitchat_do_not_think(self):
        self.assertFalse(thinking.wants_thinking("what time is it"))
        self.assertFalse(thinking.wants_thinking("how are you"))

    def test_analytical_requests_think(self):
        self.assertTrue(thinking.wants_thinking("why does the furnace keep short-cycling"))
        self.assertTrue(thinking.wants_thinking("explain how the awareness pipeline works"))
        self.assertTrue(thinking.wants_thinking("compare vLLM and Ollama for this box"))

    def test_long_request_thinks(self):
        long_request = "please " + ("adjust the schedule " * 20)
        self.assertGreaterEqual(len(long_request), 200)
        self.assertTrue(thinking.wants_thinking(long_request))

    def test_background_lane_always_thinks(self):
        self.assertTrue(
            thinking.wants_thinking("turn on the lights", runtime_lane="background")
        )

    def test_empty_command_does_not_think(self):
        self.assertFalse(thinking.wants_thinking(""))
        self.assertFalse(thinking.wants_thinking("   "))


class ThinkingSuffixTests(unittest.TestCase):
    def test_auto_suppresses_on_simple_command(self):
        suffix = thinking.thinking_suffix("turn on the lights", mode="auto")
        self.assertEqual(suffix, " /no_think")

    def test_auto_enables_on_complex_command(self):
        suffix = thinking.thinking_suffix("why is the sensor offline", mode="auto")
        self.assertEqual(suffix, " /think")

    def test_always_forces_think(self):
        self.assertEqual(
            thinking.thinking_suffix("turn on the lights", mode="always"), " /think"
        )

    def test_never_forces_no_think(self):
        self.assertEqual(
            thinking.thinking_suffix("why is the sensor offline", mode="never"),
            " /no_think",
        )

    def test_off_injects_nothing(self):
        self.assertEqual(
            thinking.thinking_suffix("why is the sensor offline", mode="off"), ""
        )

    def test_mode_defaults_to_env(self):
        with mock.patch.dict("os.environ", {"TALOS_LLM_THINK_MODE": "off"}):
            self.assertEqual(thinking.thinking_suffix("anything"), "")

    def test_background_lane_thinks_under_auto(self):
        suffix = thinking.thinking_suffix(
            "turn on the lights", runtime_lane="background", mode="auto"
        )
        self.assertEqual(suffix, " /think")


if __name__ == "__main__":
    unittest.main()
