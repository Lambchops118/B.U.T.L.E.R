from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime as agent_runtime
from talos.memory import MemoryStore
from talos.voice.backends.base import LLMCompletion, LLMTextDelta, LLMToolCall


class _FakeBackend:
    """Returns a scripted sequence of streamed turns."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.stream_calls = []

    def stream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        self.stream_calls.append([dict(m) for m in messages])
        events = self._turns.pop(0)
        for event in events:
            yield event


class _FakeMCP:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return "noon"


class RunCommandStreamTests(unittest.TestCase):
    def _patches(self, backend, mcp):
        return [
            mock.patch.object(agent_runtime, "get_local_mcp_client", return_value=mcp),
            mock.patch.object(agent_runtime, "_build_tool_definitions", return_value=[]),
            mock.patch.object(agent_runtime, "_get_memory_store", return_value=None),
            mock.patch.object(agent_runtime, "_get_prompt_memory", return_value=""),
            mock.patch.object(agent_runtime, "_record_memory_turn"),
            mock.patch.object(agent_runtime, "_get_stream_backend", return_value=backend),
        ]

    def _run(self, backend, mcp):
        patches = self._patches(backend, mcp)
        for p in patches:
            p.start()
        try:
            return list(
                agent_runtime.run_command_stream(
                    "what time is it",
                    session_id="voice",
                    interaction_mode="voice",
                )
            )
        finally:
            for p in patches:
                p.stop()

    def test_plain_answer_streams_deltas(self):
        backend = _FakeBackend(
            [
                [
                    LLMTextDelta("The light "),
                    LLMTextDelta("is on."),
                    LLMCompletion(text="The light is on."),
                ]
            ]
        )
        deltas = self._run(backend, _FakeMCP())
        self.assertEqual(deltas, ["The light ", "is on."])

    def test_tool_round_then_final_answer(self):
        mcp = _FakeMCP()
        backend = _FakeBackend(
            [
                # First turn: model asks for a tool, emits no spoken text.
                [
                    LLMCompletion(
                        text="",
                        tool_calls=(LLMToolCall(call_id="c1", name="get_time", arguments="{}"),),
                        finish_reason="tool_calls",
                    )
                ],
                # Second turn: the spoken answer.
                [
                    LLMTextDelta("The time "),
                    LLMTextDelta("is noon."),
                    LLMCompletion(text="The time is noon."),
                ],
            ]
        )
        deltas = self._run(backend, mcp)

        # Only the final turn produced spoken text.
        self.assertEqual(deltas, ["The time ", "is noon."])
        # The tool was actually executed via the shared MCP dispatch.
        self.assertEqual(mcp.calls, [("get_time", "{}")])
        # The second stream call included the tool result in history.
        second_call_messages = backend.stream_calls[1]
        roles = [m.get("role") for m in second_call_messages]
        self.assertIn("tool", roles)
        self.assertIn("assistant", roles)

    def test_tool_call_limit_is_enforced(self):
        # Model keeps asking for tools forever; loop must terminate.
        forever_tool_turn = [
            LLMCompletion(
                text="",
                tool_calls=(LLMToolCall(call_id="c", name="get_time", arguments="{}"),),
                finish_reason="tool_calls",
            )
        ]
        turns = [list(forever_tool_turn) for _ in range(agent_runtime.MAX_TOOL_CALL_ROUNDS + 5)]
        backend = _FakeBackend(turns)
        deltas = self._run(backend, _FakeMCP())
        # It should stop and surface the limit note rather than loop endlessly.
        self.assertTrue(any("tool-call limit" in d for d in deltas))

    def test_second_streamed_turn_receives_prior_session_context(self):
        backend = _FakeBackend(
            [
                [LLMTextDelta("Understood."), LLMCompletion(text="Understood.")],
                [LLMTextDelta("Cobalt."), LLMCompletion(text="Cobalt.")],
            ]
        )
        mcp = _FakeMCP()
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir) / "memory.sqlite3")
            patches = [
                mock.patch.object(agent_runtime, "get_local_mcp_client", return_value=mcp),
                mock.patch.object(agent_runtime, "_build_tool_definitions", return_value=[]),
                mock.patch.object(agent_runtime, "_get_memory_store", return_value=store),
                mock.patch.object(agent_runtime, "_get_stream_backend", return_value=backend),
            ]
            for patcher in patches:
                patcher.start()
            try:
                list(
                    agent_runtime.run_command_stream(
                        "My code word is cobalt.",
                        session_id="voice-worker",
                        interaction_mode="voice",
                    )
                )
                list(
                    agent_runtime.run_command_stream(
                        "What was my code word?",
                        session_id="voice-worker",
                        interaction_mode="voice",
                    )
                )
            finally:
                for patcher in patches:
                    patcher.stop()
                store.close()

        second_turn_instructions = backend.stream_calls[1][0]["content"]
        self.assertIn("My code word is cobalt.", second_turn_instructions)
        self.assertIn("Understood.", second_turn_instructions)


if __name__ == "__main__":
    unittest.main()
