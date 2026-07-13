from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.voice.backends.base import (
    LLMCompletion,
    LLMTextDelta,
    LLMToolCall,
    chat_messages_to_tool_result,
    responses_tools_to_chat_tools,
    tool_calls_to_assistant_message,
)
from talos.voice.backends.llm_openai_compat import OpenAICompatibleChatBackend


def _delta_chunk(content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _tool_delta(index, *, call_id=None, name=None, arguments=None):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=fn)


class FakeChatCompletions:
    def __init__(self, script):
        self._script = script
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._script)


class FakeClient:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=FakeChatCompletions(script))


class ToolDefConversionTests(unittest.TestCase):
    def test_flat_responses_def_becomes_nested(self):
        flat = [
            {
                "type": "function",
                "name": "place_phone_call",
                "description": "Call someone.",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        converted = responses_tools_to_chat_tools(flat)
        self.assertEqual(
            converted,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "place_phone_call",
                        "description": "Call someone.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )

    def test_already_nested_passes_through(self):
        nested = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
        self.assertEqual(responses_tools_to_chat_tools(nested), nested)

    def test_missing_parameters_defaults_to_empty_object(self):
        converted = responses_tools_to_chat_tools([{"type": "function", "name": "ping"}])
        self.assertEqual(
            converted[0]["function"]["parameters"],
            {"type": "object", "properties": {}},
        )

    def test_none_and_garbage_are_skipped(self):
        self.assertEqual(responses_tools_to_chat_tools(None), [])
        self.assertEqual(responses_tools_to_chat_tools(["nope", {"no": "name"}]), [])


class StreamingTests(unittest.TestCase):
    def test_streams_text_then_completion(self):
        script = [
            _delta_chunk(content="Hello "),
            _delta_chunk(content="world."),
            _delta_chunk(finish_reason="stop"),
        ]
        backend = OpenAICompatibleChatBackend(model="test", client=FakeClient(script))
        events = list(backend.stream([{"role": "user", "content": "hi"}]))

        deltas = [e for e in events if isinstance(e, LLMTextDelta)]
        completions = [e for e in events if isinstance(e, LLMCompletion)]
        self.assertEqual([d.text for d in deltas], ["Hello ", "world."])
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].text, "Hello world.")
        self.assertFalse(completions[0].wants_tools)
        self.assertEqual(completions[0].finish_reason, "stop")

    def test_accumulates_streamed_tool_call_arguments(self):
        script = [
            _delta_chunk(tool_calls=[_tool_delta(0, call_id="c1", name="get_weather")]),
            _delta_chunk(tool_calls=[_tool_delta(0, arguments='{"city":')]),
            _delta_chunk(tool_calls=[_tool_delta(0, arguments='"NYC"}')]),
            _delta_chunk(finish_reason="tool_calls"),
        ]
        backend = OpenAICompatibleChatBackend(model="test", client=FakeClient(script))
        completion = backend.complete([{"role": "user", "content": "weather?"}])

        self.assertTrue(completion.wants_tools)
        self.assertEqual(len(completion.tool_calls), 1)
        call = completion.tool_calls[0]
        self.assertEqual(call.call_id, "c1")
        self.assertEqual(call.name, "get_weather")
        self.assertEqual(call.arguments, '{"city":"NYC"}')
        self.assertEqual(completion.finish_reason, "tool_calls")

    def test_multiple_parallel_tool_calls_by_index(self):
        script = [
            _delta_chunk(
                tool_calls=[
                    _tool_delta(0, call_id="a", name="one", arguments="{}"),
                    _tool_delta(1, call_id="b", name="two", arguments="{}"),
                ]
            ),
            _delta_chunk(finish_reason="tool_calls"),
        ]
        backend = OpenAICompatibleChatBackend(model="test", client=FakeClient(script))
        completion = backend.complete([{"role": "user", "content": "go"}])
        names = [c.name for c in completion.tool_calls]
        self.assertEqual(names, ["one", "two"])

    def test_tools_are_passed_in_chat_format(self):
        backend = OpenAICompatibleChatBackend(
            model="test", client=FakeClient([_delta_chunk(finish_reason="stop")])
        )
        tools = [{"type": "function", "name": "ping", "parameters": {}}]
        backend.complete([{"role": "user", "content": "x"}], tools=tools)
        sent = backend._client.chat.completions.calls[0]
        self.assertIn("tools", sent)
        self.assertEqual(sent["tools"][0]["function"]["name"], "ping")
        self.assertTrue(sent["stream"])

    def test_default_uses_max_tokens_param(self):
        backend = OpenAICompatibleChatBackend(
            model="test", max_tokens=123, client=FakeClient([_delta_chunk(finish_reason="stop")])
        )
        backend.complete([{"role": "user", "content": "x"}])
        sent = backend._client.chat.completions.calls[0]
        self.assertEqual(sent.get("max_tokens"), 123)
        self.assertNotIn("max_completion_tokens", sent)

    def test_openai_style_max_completion_tokens_param(self):
        backend = OpenAICompatibleChatBackend(
            model="test",
            max_tokens=123,
            max_tokens_param="max_completion_tokens",
            client=FakeClient([_delta_chunk(finish_reason="stop")]),
        )
        backend.complete([{"role": "user", "content": "x"}])
        sent = backend._client.chat.completions.calls[0]
        self.assertEqual(sent.get("max_completion_tokens"), 123)
        self.assertNotIn("max_tokens", sent)


class MessageHelperTests(unittest.TestCase):
    def test_tool_result_message_shape(self):
        call = LLMToolCall(call_id="c1", name="get_weather", arguments="{}")
        msg = chat_messages_to_tool_result(call, '{"temp": 70}')
        self.assertEqual(
            msg,
            {"role": "tool", "tool_call_id": "c1", "content": '{"temp": 70}'},
        )

    def test_assistant_tool_call_message_shape(self):
        calls = [LLMToolCall(call_id="c1", name="get_weather", arguments='{"city":"NYC"}')]
        msg = tool_calls_to_assistant_message("", calls)
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["tool_calls"][0]["id"], "c1")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "get_weather")


if __name__ == "__main__":
    unittest.main()
