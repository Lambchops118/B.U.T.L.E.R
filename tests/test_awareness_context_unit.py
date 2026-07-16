"""Unit tests for situation selection and token budgeting (no database)."""

from __future__ import annotations

import unittest

try:
    from talos.awareness.context.broker import (
        PRIORITY_CRITICAL_ALERTS,
        PRIORITY_HEALTH,
        PRIORITY_STATE,
        Candidate,
        estimate_tokens,
        select_items,
    )
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


def _candidate(item_id: str, priority: int, chars: int = 70) -> Candidate:
    return Candidate(item_id=item_id, priority=priority, text="x" * chars, reason="test")


class TokenEstimateTest(unittest.TestCase):
    def test_estimate_is_conservative_and_positive(self) -> None:
        self.assertGreaterEqual(estimate_tokens("word"), 1)
        # 350 chars → at least 100 estimated tokens (overestimates vs ~87 real)
        self.assertGreaterEqual(estimate_tokens("x" * 350), 100)


class SelectionTest(unittest.TestCase):
    def test_lower_priority_dropped_first(self) -> None:
        candidates = [
            _candidate("health:1", PRIORITY_HEALTH),
            _candidate("state:1", PRIORITY_STATE),
            _candidate("state:2", PRIORITY_STATE),
        ]
        budget = estimate_tokens("x" * 70) * 2  # room for exactly two items
        selected, audit = select_items(candidates, budget)
        ids = [candidate.item_id for candidate in selected]
        self.assertEqual(ids, ["state:1", "state:2"])  # health dropped first
        dropped = [entry for entry in audit if not entry["included"]]
        self.assertEqual(dropped[0]["item_id"], "health:1")
        self.assertEqual(dropped[0]["reason"], "budget_exceeded")

    def test_critical_alerts_survive_zero_budget(self) -> None:
        candidates = [
            _candidate("alert:critical", PRIORITY_CRITICAL_ALERTS, chars=200),
            _candidate("state:1", PRIORITY_STATE),
        ]
        selected, audit = select_items(candidates, budget_tokens=1)
        self.assertEqual([c.item_id for c in selected], ["alert:critical"])
        included = {entry["item_id"]: entry["included"] for entry in audit}
        self.assertTrue(included["alert:critical"])
        self.assertFalse(included["state:1"])

    def test_audit_covers_every_candidate(self) -> None:
        candidates = [_candidate(f"state:{i}", PRIORITY_STATE) for i in range(5)]
        _, audit = select_items(candidates, budget_tokens=10_000)
        self.assertEqual(len(audit), 5)
        self.assertTrue(all(entry["included"] for entry in audit))
        for key in ("item_id", "priority", "tokens", "included", "reason"):
            self.assertIn(key, audit[0])


if __name__ == "__main__":
    unittest.main()
