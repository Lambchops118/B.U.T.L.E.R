"""Barge-in across the agent side: cancelling a turn and correcting the record.

The voice worker's own audio handling is covered in ``test_barge_in.py``; this
covers what has to happen in the main agent once the user has cut in.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime as agent_runtime
from talos.memory import MemoryStore
from talos.voice.backends.base import LLMCompletion, LLMTextDelta, LLMToolCall


class _CancellingBackend:
    """Streams deltas, setting the cancel token partway through the first turn."""

    def __init__(self, cancel_after: int, turns=None):
        self.cancel_after = cancel_after
        self.cancel_event: threading.Event | None = None
        self.closed = False
        self.turns = turns
        self.stream_calls = 0

    def stream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        self.stream_calls += 1
        try:
            if self.turns is not None:
                for event in self.turns.pop(0):
                    yield event
                return
            for index in range(10):
                if index == self.cancel_after and self.cancel_event is not None:
                    self.cancel_event.set()
                yield LLMTextDelta(f"part{index} ")
            yield LLMCompletion(text="".join(f"part{i} " for i in range(10)))
        finally:
            self.closed = True


class _FakeMCP:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return "noon"


def _patches(backend, mcp, memory_store=None, record_mock=None):
    return [
        mock.patch.object(agent_runtime, "get_local_mcp_client", return_value=mcp),
        mock.patch.object(agent_runtime, "_build_tool_definitions", return_value=[]),
        mock.patch.object(agent_runtime, "_get_memory_store", return_value=memory_store),
        mock.patch.object(agent_runtime, "_get_prompt_memory", return_value=""),
        mock.patch.object(agent_runtime, "_get_stream_backend", return_value=backend),
        mock.patch.object(agent_runtime, "emit_pipeline_event"),
    ] + ([mock.patch.object(agent_runtime, "_record_memory_turn", record_mock)] if record_mock else [])


class CancelStreamingTurnTests(unittest.TestCase):
    def _run(self, backend, cancel, **kwargs):
        mcp = _FakeMCP()
        record = mock.Mock()
        patches = _patches(backend, mcp, record_mock=record)
        for patcher in patches:
            patcher.start()
        try:
            deltas = list(
                agent_runtime.run_command_stream(
                    "tell me about the pumps",
                    session_id="voice",
                    interaction_mode="voice",
                    cancel=cancel,
                    **kwargs,
                )
            )
        finally:
            for patcher in patches:
                patcher.stop()
        return deltas, record

    def test_cancelling_stops_generation_partway_through(self):
        cancel = threading.Event()
        backend = _CancellingBackend(cancel_after=3)
        backend.cancel_event = cancel

        deltas, _ = self._run(backend, cancel)

        # Everything up to the cancel is spoken; nothing after it is produced.
        self.assertEqual(deltas, ["part0 ", "part1 ", "part2 "])

    def test_cancelling_closes_the_backend_stream(self):
        """Closing it is what ends the HTTP request to the model server."""
        cancel = threading.Event()
        backend = _CancellingBackend(cancel_after=2)
        backend.cancel_event = cancel

        self._run(backend, cancel)

        self.assertTrue(backend.closed)

    def test_interrupted_turn_is_still_recorded(self):
        cancel = threading.Event()
        backend = _CancellingBackend(cancel_after=3)
        backend.cancel_event = cancel

        _, record = self._run(backend, cancel)

        # An interrupted turn that vanished from history would leave the next
        # turn with no idea what the user was reacting to.
        record.assert_called_once()
        self.assertEqual(record.call_args.args[2], "tell me about the pumps")
        self.assertEqual(record.call_args.args[3], "part0 part1 part2")

    def test_a_token_already_set_stops_before_any_tool_round(self):
        cancel = threading.Event()
        cancel.set()
        backend = _CancellingBackend(cancel_after=99)

        deltas, _ = self._run(backend, cancel)

        self.assertEqual(deltas, [])
        self.assertEqual(backend.stream_calls, 0)

    def test_cancelling_between_tool_rounds_stops_the_loop(self):
        cancel = threading.Event()
        turns = [
            [
                LLMCompletion(
                    text="",
                    tool_calls=(
                        LLMToolCall(call_id="c1", name="get_time", arguments="{}"),
                    ),
                    finish_reason="tool_calls",
                )
            ],
            [LLMTextDelta("It is noon."), LLMCompletion(text="It is noon.")],
        ]
        backend = _CancellingBackend(cancel_after=0, turns=turns)
        mcp = _FakeMCP()

        def cancel_during_tool(name, arguments):
            cancel.set()
            return "noon"

        mcp.call_tool = cancel_during_tool
        record = mock.Mock()
        patches = _patches(backend, mcp, record_mock=record)
        for patcher in patches:
            patcher.start()
        try:
            deltas = list(
                agent_runtime.run_command_stream(
                    "what time is it",
                    session_id="voice",
                    interaction_mode="voice",
                    cancel=cancel,
                )
            )
        finally:
            for patcher in patches:
                patcher.stop()

        self.assertEqual(deltas, [])
        # The second turn was never requested.
        self.assertEqual(backend.stream_calls, 1)

    def test_telemetry_marks_the_turn_as_interrupted(self):
        cancel = threading.Event()
        backend = _CancellingBackend(cancel_after=1)
        backend.cancel_event = cancel
        telemetry = []

        self._run(backend, cancel, telemetry_callback=telemetry.append)

        events = {item["event"]: item for item in telemetry}
        self.assertIn("agent_stream_interrupted", events)
        self.assertTrue(events["agent_stream_interrupted"]["interrupted"])
        self.assertNotIn("agent_stream_completed", events)

    def test_an_uncancelled_turn_is_unaffected(self):
        backend = _CancellingBackend(cancel_after=99)
        telemetry = []

        deltas, _ = self._run(backend, threading.Event(), telemetry_callback=telemetry.append)

        self.assertEqual(len(deltas), 10)
        events = {item["event"] for item in telemetry}
        self.assertIn("agent_stream_completed", events)
        self.assertNotIn("agent_stream_interrupted", events)


class CancelRegistryTests(unittest.TestCase):
    def tearDown(self) -> None:
        agent_runtime.release_cancel("req-1")

    def test_cancel_reaches_the_registered_token(self):
        token = agent_runtime.register_cancel("req-1")
        self.assertTrue(agent_runtime.request_cancel("req-1"))
        self.assertTrue(token.is_set())

    def test_cancelling_a_finished_turn_reports_false(self):
        agent_runtime.register_cancel("req-1")
        agent_runtime.release_cancel("req-1")
        self.assertFalse(agent_runtime.request_cancel("req-1"))

    def test_unknown_and_blank_request_ids_are_safe(self):
        self.assertFalse(agent_runtime.request_cancel("never-registered"))
        self.assertFalse(agent_runtime.request_cancel(""))


class InterruptionRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore(":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def test_reply_is_cut_back_to_what_was_heard(self):
        self.store.record_turn(
            "voice",
            "tell me about the pumps",
            "Channel one is primed. Channel two is idle. Channel three is offline.",
        )

        amended = self.store.amend_last_assistant_message(
            "voice", "Channel one is primed. [cut off]"
        )

        self.assertTrue(amended)
        messages = self.store.get_recent_messages("voice")
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertEqual(messages[-1]["content"], "Channel one is primed. [cut off]")
        self.assertNotIn("Channel three", messages[-1]["content"])
        # The user's side of the exchange is untouched.
        self.assertEqual(messages[-2]["content"], "tell me about the pumps")

    def test_assistant_turn_is_added_when_nothing_was_recorded(self):
        self.store.record_message("voice", "user", "tell me about the pumps")

        amended = self.store.amend_last_assistant_message("voice", "[interrupted]")

        self.assertTrue(amended)
        messages = self.store.get_recent_messages("voice")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertEqual(messages[-1]["content"], "[interrupted]")

    def test_empty_session_is_left_alone(self):
        self.assertFalse(self.store.amend_last_assistant_message("voice", "[x]"))

    def test_runtime_marks_the_cut_and_keeps_the_audible_prefix(self):
        self.store.record_turn("voice", "tell me about the pumps", "One. Two. Three.")

        with mock.patch.object(
            agent_runtime, "_get_memory_store", return_value=self.store
        ):
            recorded = agent_runtime.note_interruption("voice", "One.")

        self.assertTrue(recorded)
        content = self.store.get_recent_messages("voice")[-1]["content"]
        self.assertTrue(content.startswith("One."))
        self.assertIn("interrupted", content)
        self.assertNotIn("Three", content)

    def test_runtime_records_that_nothing_was_heard(self):
        self.store.record_turn("voice", "tell me about the pumps", "One. Two.")

        with mock.patch.object(
            agent_runtime, "_get_memory_store", return_value=self.store
        ):
            agent_runtime.note_interruption("voice", "")

        content = self.store.get_recent_messages("voice")[-1]["content"]
        self.assertIn("before anything was heard", content)

    def test_no_memory_store_is_not_an_error(self):
        with mock.patch.object(agent_runtime, "_get_memory_store", return_value=None):
            self.assertFalse(agent_runtime.note_interruption("voice", "One."))


if __name__ == "__main__":
    unittest.main()
