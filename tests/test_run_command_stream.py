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
            mock.patch.object(agent_runtime, "emit_pipeline_event"),
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

    def test_emits_prompt_backend_and_stage_telemetry(self):
        backend = _FakeBackend(
            [[LLMTextDelta("Done."), LLMCompletion(text="Done.")]]
        )
        mcp = _FakeMCP()
        telemetry = []
        patches = self._patches(backend, mcp)
        for patcher in patches:
            patcher.start()
        try:
            output = list(
                agent_runtime.run_command_stream(
                    "test telemetry",
                    session_id="voice",
                    interaction_mode="voice",
                    request_id="req-123",
                    telemetry_callback=telemetry.append,
                )
            )
        finally:
            for patcher in patches:
                patcher.stop()

        self.assertEqual(output, ["Done."])
        by_event = {item["event"]: item for item in telemetry}
        self.assertGreater(by_event["prompt_ready"]["prompt_tokens_estimated"], 0)
        self.assertIn("tool_build_ms", by_event["prompt_ready"])
        self.assertEqual(by_event["llm_round_started"]["round"], 1)
        self.assertEqual(by_event["llm_round_completed"]["round"], 1)
        self.assertIn("agent_stream_total_ms", by_event["agent_stream_completed"])

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

    def test_leaked_tool_call_json_is_recovered_not_spoken(self):
        mcp = _FakeMCP()
        backend = _FakeBackend(
            [
                # The model leaks a tool call as plain text content (no structured
                # tool_calls), exactly as a local model sometimes does.
                [
                    LLMTextDelta('{"name": "get_time", "arguments": {}}'),
                    LLMCompletion(text='{"name": "get_time", "arguments": {}}'),
                ],
                # After the recovered tool runs, the real spoken answer arrives.
                [LLMTextDelta("It is noon."), LLMCompletion(text="It is noon.")],
            ]
        )
        deltas = self._run(backend, mcp)

        # The raw JSON must never reach the caller (TTS); only the real answer.
        self.assertEqual(deltas, ["It is noon."])
        self.assertNotIn('{"name"', "".join(deltas))
        # The leaked call was parsed and actually executed.
        self.assertEqual(mcp.calls, [("get_time", "{}")])
        # The second stream call carried the tool result back to the model.
        self.assertIn("tool", [m.get("role") for m in backend.stream_calls[1]])

    def test_explicit_pump_command_routes_through_registered_action_tool(self):
        mcp = _FakeMCP()
        def accepted_action(name, arguments):
            mcp.calls.append((name, arguments))
            return (
                '{"accepted":true,"action_request_id":"req-2",'
                '"status":"approved"}'
            )

        mcp.call_tool = mock.Mock(side_effect=accepted_action)
        backend = _FakeBackend(
            []
        )
        tool_defs = [
            {
                "name": "request_device_action",
                "description": "Request a registered physical action.",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        patches = self._patches(backend, mcp)
        patches[1] = mock.patch.object(
            agent_runtime,
            "_build_tool_definitions",
            return_value=tool_defs,
        )
        for patcher in patches:
            patcher.start()
        try:
            output = list(
                agent_runtime.run_command_stream(
                    "turn on the pump for pot two",
                    session_id="voice-worker",
                    interaction_mode="voice",
                    request_id="pump-2",
                )
            )
        finally:
            for patcher in patches:
                patcher.stop()

        self.assertEqual(
            output,
            [
                "Pump 2 request is approved; physical activation "
                "has not yet been confirmed."
            ],
        )
        self.assertEqual(len(mcp.calls), 1)
        name, raw_arguments = mcp.calls[0]
        self.assertEqual(name, "request_device_action")
        arguments = agent_runtime.parse_tool_arguments(raw_arguments)
        self.assertEqual(arguments["action"], "run_pump")
        self.assertEqual(arguments["parameters"], '{"channel":2}')
        self.assertEqual(arguments["idempotency_key"], "voice-pump-2")
        self.assertEqual(backend.stream_calls, [])

    def test_physical_action_without_tool_is_not_falsely_confirmed(self):
        mcp = _FakeMCP()
        false_reply = [
            LLMTextDelta("I've initiated the watering process."),
            LLMCompletion(text="I've initiated the watering process."),
        ]
        backend = _FakeBackend([list(false_reply), list(false_reply)])
        tool_defs = [
            {
                "name": "request_device_action",
                "description": "Request a registered physical action.",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        patches = self._patches(backend, mcp)
        patches[1] = mock.patch.object(
            agent_runtime,
            "_build_tool_definitions",
            return_value=tool_defs,
        )
        for patcher in patches:
            patcher.start()
        try:
            output = list(
                agent_runtime.run_command_stream(
                    "water the monstera",
                    session_id="voice-worker",
                    interaction_mode="voice",
                )
            )
        finally:
            for patcher in patches:
                patcher.stop()

        self.assertEqual(
            output,
            ["I did not send a device command, so no pump or relay was activated."],
        )
        self.assertEqual(mcp.calls, [])
        self.assertEqual(len(backend.stream_calls), 2)
        self.assertIn(
            "called no action tool",
            backend.stream_calls[1][-1]["content"],
        )

    def test_explicit_pump_action_service_error_is_reported_without_llm_claim(self):
        class _FailingMCP(_FakeMCP):
            def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return '{"error":"awareness API unavailable"}'

        mcp = _FailingMCP()
        backend = _FakeBackend([])
        tool_defs = [
            {
                "name": "request_device_action",
                "description": "Request a registered physical action.",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        patches = self._patches(backend, mcp)
        patches[1] = mock.patch.object(
            agent_runtime,
            "_build_tool_definitions",
            return_value=tool_defs,
        )
        for patcher in patches:
            patcher.start()
        try:
            output = list(
                agent_runtime.run_command_stream(
                    "activate pump three",
                    session_id="voice-worker",
                    interaction_mode="voice",
                    request_id="pump-3",
                )
            )
        finally:
            for patcher in patches:
                patcher.stop()

        self.assertEqual(
            output,
            [
                "Pump 3 command could not be sent because the registered "
                "action service returned an error."
            ],
        )
        self.assertEqual(len(mcp.calls), 1)
        self.assertEqual(backend.stream_calls, [])

    def test_explicit_pump_rejection_reports_reason_without_claiming_activation(self):
        class _RejectedMCP(_FakeMCP):
            def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return (
                    '{"accepted":false,"status":"rejected",'
                    '"reason":"cooldown: channel 4 ran recently"}'
                )

        mcp = _RejectedMCP()
        backend = _FakeBackend([])
        tool_defs = [
            {
                "name": "request_device_action",
                "description": "Request a registered physical action.",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        patches = self._patches(backend, mcp)
        patches[1] = mock.patch.object(
            agent_runtime,
            "_build_tool_definitions",
            return_value=tool_defs,
        )
        for patcher in patches:
            patcher.start()
        try:
            output = list(
                agent_runtime.run_command_stream(
                    "activate pump four",
                    session_id="voice-worker",
                    interaction_mode="voice",
                    request_id="pump-4",
                )
            )
        finally:
            for patcher in patches:
                patcher.stop()

        self.assertEqual(
            output,
            ["Pump 4 command was not sent: cooldown: channel 4 ran recently"],
        )
        self.assertEqual(len(mcp.calls), 1)
        self.assertEqual(backend.stream_calls, [])

    def test_voice_followup_receives_prior_user_and_assistant_turns(self):
        backend = _FakeBackend(
            [
                [
                    LLMTextDelta("Which pot, one or two?"),
                    LLMCompletion(text="Which pot, one or two?"),
                ],
                [LLMTextDelta("Done."), LLMCompletion(text="Done.")],
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
                        "Water the plants.",
                        session_id="voice-worker",
                        interaction_mode="voice",
                    )
                )
                list(
                    agent_runtime.run_command_stream(
                        "Go with both.",
                        session_id="voice-worker",
                        interaction_mode="voice",
                    )
                )
            finally:
                for patcher in patches:
                    patcher.stop()
                store.close()

        second_turn_messages = backend.stream_calls[1]
        self.assertNotIn("Active session summary", second_turn_messages[0]["content"])
        self.assertIn(
            {"role": "user", "content": "Water the plants."},
            second_turn_messages,
        )
        self.assertIn(
            {"role": "assistant", "content": "Which pot, one or two?"},
            second_turn_messages,
        )
        # The live outgoing turn carries the dynamic reasoning soft switch
        # (simple command -> /no_think). The replayed prior turn above stays
        # undecorated, confirming the token never pollutes persisted history.
        self.assertEqual(
            second_turn_messages[-1],
            {"role": "user", "content": "Go with both. /no_think"},
        )


if __name__ == "__main__":
    unittest.main()
