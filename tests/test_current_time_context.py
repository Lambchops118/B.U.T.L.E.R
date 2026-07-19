from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime


class CurrentTimeContextTests(unittest.TestCase):
    def test_returns_authoritative_time_block_when_enabled(self):
        with mock.patch.object(runtime, "INJECT_CURRENT_TIME", True):
            block = runtime._current_time_context()
        self.assertIsNotNone(block)
        # It must carry ground truth and instruct the model not to guess.
        self.assertIn("do not guess", block)
        self.assertIn("Time:", block)
        self.assertIn("Date:", block)

    def test_disabled_returns_none(self):
        with mock.patch.object(runtime, "INJECT_CURRENT_TIME", False):
            self.assertIsNone(runtime._current_time_context())

    def test_time_source_failure_degrades_to_none(self):
        with mock.patch.object(runtime, "INJECT_CURRENT_TIME", True), mock.patch(
            "talos.services.home_automation.get_current_datetime",
            side_effect=RuntimeError("clock unavailable"),
        ):
            self.assertIsNone(runtime._current_time_context())


if __name__ == "__main__":
    unittest.main()
