from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.request_classifier import RequestClassification
from talos.router import _enforce_foreground_for_sensitive_actions, _must_run_in_foreground


class RouterPhoneRoutingTests(unittest.TestCase):
    def test_detects_explicit_phone_call_requests(self) -> None:
        self.assertTrue(_must_run_in_foreground("call mom now"))
        self.assertTrue(_must_run_in_foreground("place the call to Thomas"))
        self.assertTrue(_must_run_in_foreground("try the call now"))
        self.assertTrue(_must_run_in_foreground("give dad a call"))

    def test_non_phone_work_does_not_match(self) -> None:
        self.assertFalse(_must_run_in_foreground("call this function from the setup script"))
        self.assertFalse(_must_run_in_foreground("phone numbers are configured now"))
        self.assertFalse(_must_run_in_foreground("write a call summary"))

    def test_background_phone_requests_are_forced_foreground(self) -> None:
        decision = RequestClassification(mode="background", reason="model route decision", response="Working on it.")
        updated = _enforce_foreground_for_sensitive_actions("try the call now", decision)
        self.assertEqual(updated.mode, "foreground")
        self.assertIn("foreground session", updated.reason)
        self.assertEqual(updated.response, "")

    def test_non_background_decisions_are_preserved(self) -> None:
        decision = RequestClassification(mode="status", reason="status question")
        updated = _enforce_foreground_for_sensitive_actions("call mom now", decision)
        self.assertEqual(updated, decision)


if __name__ == "__main__":
    unittest.main()
