from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.agent import runtime as agent_runtime
from butler.agent import tool_access
from butler.voice.backends.base import LLMToolCall

_ENV = {
    "BUTLER_EXTERNAL_SOURCES": "discord",
    "BUTLER_PRIVILEGED_CALLER_TOKEN": "s3cret",
    "BUTLER_RESTRICTED_TOOL_ALLOWLIST": "get_weather",
}


class _FakeMcp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def openai_tool_definitions(self):
        return [
            {"type": "function", "name": "toggle_lights", "parameters": {}},
            {"type": "function", "name": "get_weather", "parameters": {}},
        ]

    def call_tool(self, name, arguments):
        self.calls.append(name)
        return json.dumps({"success": True})


@mock.patch.dict(os.environ, _ENV)
class ResolveRequestAccessTests(unittest.TestCase):
    def test_internal_sources_keep_full_access(self) -> None:
        for source in ("voice", "text", "terminal", "http", ""):
            self.assertEqual(
                tool_access.resolve_request_access(source, None, None), tool_access.FULL
            )

    def test_external_source_is_restricted_by_default(self) -> None:
        self.assertEqual(
            tool_access.resolve_request_access("discord", None, None),
            tool_access.RESTRICTED,
        )

    def test_full_requires_matching_secret(self) -> None:
        resolve = tool_access.resolve_request_access
        self.assertEqual(resolve("Discord", "full", "s3cret"), tool_access.FULL)
        self.assertEqual(resolve("discord", "full", "wrong"), tool_access.RESTRICTED)
        self.assertEqual(resolve("discord", "full", None), tool_access.RESTRICTED)
        # The secret alone does not escalate a request that did not ask for it.
        self.assertEqual(resolve("discord", None, "s3cret"), tool_access.RESTRICTED)

    def test_unset_secret_never_grants_full(self) -> None:
        with mock.patch.dict(os.environ, {"BUTLER_PRIVILEGED_CALLER_TOKEN": ""}):
            self.assertEqual(
                tool_access.resolve_request_access("discord", "full", ""),
                tool_access.RESTRICTED,
            )

    def test_unknown_access_value_is_restricted(self) -> None:
        self.assertFalse(tool_access.is_tool_permitted("toggle_lights", "admin"))


@mock.patch.dict(os.environ, _ENV)
class RuntimeEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        # Keep the test to the tools under test: no host tools, no reordering.
        patches = [
            mock.patch.object(agent_runtime, "_resource_tool_definitions", return_value=[]),
            mock.patch.object(agent_runtime, "TOOLS_DISABLED", False),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_restricted_caller_is_only_offered_allowlisted_tools(self) -> None:
        mcp = _FakeMcp()
        full = agent_runtime._build_tool_definitions(mcp)
        restricted = agent_runtime._build_tool_definitions(mcp, tool_access.RESTRICTED)
        self.assertEqual({t["name"] for t in full}, {"toggle_lights", "get_weather"})
        self.assertEqual([t["name"] for t in restricted], ["get_weather"])

    def test_stream_executor_refuses_unpermitted_call(self) -> None:
        mcp = _FakeMcp()
        calls = (
            LLMToolCall(call_id="1", name="toggle_lights", arguments="{}"),
            LLMToolCall(call_id="2", name="get_weather", arguments="{}"),
        )
        _messages, events = agent_runtime._execute_chat_tool_calls(
            calls,
            mcp,
            session_id="discord-guild-1",
            runtime_lane="foreground",
            tool_access=tool_access.RESTRICTED,
        )
        self.assertEqual(mcp.calls, ["get_weather"])
        self.assertIn("not_authorized", events[0]["raw_result"])

    def test_responses_executor_refuses_unpermitted_call(self) -> None:
        mcp = _FakeMcp()
        response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call", name="toggle_lights", arguments="{}", call_id="1"
                )
            ]
        )
        _outputs, events = agent_runtime._collect_tool_outputs(
            response, mcp, tool_access=tool_access.RESTRICTED
        )
        self.assertEqual(mcp.calls, [])
        self.assertIn("not_authorized", events[0]["raw_result"])

    def test_full_access_runs_any_tool(self) -> None:
        mcp = _FakeMcp()
        agent_runtime._execute_chat_tool_calls(
            (LLMToolCall(call_id="1", name="toggle_lights", arguments="{}"),),
            mcp,
            session_id="voice",
            runtime_lane="foreground",
        )
        self.assertEqual(mcp.calls, ["toggle_lights"])


if __name__ == "__main__":
    unittest.main()
