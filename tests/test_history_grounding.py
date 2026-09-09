"""Two defects that let the assistant answer as though it had checked.

Both were found by replaying a captured production request (llm_io
20260909T030229) against the deployed model, and both starve or mislead the
turn rather than being a limit of the model itself:

- stored history keeps only the final text of each turn, so the tool calls that
  produced past answers are invisible and the transcript reads as an unbroken
  run of confident tool-free answers, which the model then imitates;
- the awareness snapshot was cut to 500 characters after the broker had already
  ranked and budgeted it to ~2.4k, discarding every STATE/health line -- and
  potentially a critical alert the broker had guaranteed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime

_HISTORY = [
    {"role": "user", "content": "is the plant watering system online?"},
    {"role": "assistant", "content": "The plant watering system is online."},
]
_TOOLS = [{"type": "function", "function": {"name": "get_current_state"}}]


class HistoryGroundingNoticeTests(unittest.TestCase):
    def test_notice_denies_the_transcript_evidentiary_weight(self) -> None:
        with mock.patch.object(runtime, "INJECT_HISTORY_GROUNDING", True):
            notice = runtime._history_grounding_notice(_HISTORY, _TOOLS)
        self.assertIsNotNone(notice)
        # The three things it has to establish, in the model's own terms.
        self.assertIn("not a record of what was verified", notice)
        self.assertIn("call a tool", notice)
        self.assertIn("do not reuse or extend an earlier answer", notice)

    def test_no_history_means_no_pattern_to_counter(self) -> None:
        with mock.patch.object(runtime, "INJECT_HISTORY_GROUNDING", True):
            self.assertIsNone(runtime._history_grounding_notice([], _TOOLS))

    def test_without_tools_the_advice_would_be_impossible_to_follow(self) -> None:
        with mock.patch.object(runtime, "INJECT_HISTORY_GROUNDING", True):
            self.assertIsNone(runtime._history_grounding_notice(_HISTORY, []))
            self.assertIsNone(runtime._history_grounding_notice(_HISTORY, None))

    def test_can_be_switched_off(self) -> None:
        with mock.patch.object(runtime, "INJECT_HISTORY_GROUNDING", False):
            self.assertIsNone(runtime._history_grounding_notice(_HISTORY, _TOOLS))


class ContextSnapshotLimitTests(unittest.TestCase):
    def test_a_full_broker_snapshot_survives_intact(self) -> None:
        """The broker budgets ~2.4k characters; none of it may be cut here.

        The old 500-character limit kept only whatever sorted first -- verbose
        announcement receipts -- and dropped every line describing the actual
        house, which is exactly the context a state question needs.
        """
        snapshot = " ".join(
            [f"STATE quad_pump.relay_{i} = False (current, age 4m)" for i in range(1, 5)]
            + [f"ANNOUNCEMENT queued for voice {'x' * 300}" for _ in range(3)]
        )
        self.assertGreater(len(snapshot), 500)
        self.assertLess(len(snapshot), runtime.CONTEXT_SNAPSHOT_CHAR_LIMIT)

        rendered = runtime._format_context(snapshot)
        self.assertFalse(rendered.endswith("..."))
        for i in range(1, 5):
            self.assertIn(f"quad_pump.relay_{i}", rendered)

    def test_the_limit_is_above_the_brokers_own_budget(self) -> None:
        """Otherwise this cut, not the broker's ranking, decides what survives.

        The broker defaults to 600 tokens; at roughly four characters per token
        that is ~2.4k, and it guarantees critical alerts survive *its* selection.
        A backstop below that silently revokes the guarantee.
        """
        self.assertGreaterEqual(runtime.CONTEXT_SNAPSHOT_CHAR_LIMIT, 2400)

    def test_an_unbounded_snapshot_is_still_refused(self) -> None:
        rendered = runtime._format_context("y " * 20000)
        self.assertTrue(rendered.endswith("..."))
        self.assertLessEqual(
            len(rendered),
            runtime.CONTEXT_SNAPSHOT_CHAR_LIMIT + len("Context (read-only): ") + 3,
        )

    def test_empty_and_sentinel_snapshots_add_no_message(self) -> None:
        self.assertIsNone(runtime._format_context(""))
        self.assertIsNone(runtime._format_context("no recent status"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
