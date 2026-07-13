from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime as agent_runtime


class AgentRuntimePhoneToolTests(unittest.TestCase):
    def test_place_phone_call_is_registered_as_host_tool(self) -> None:
        tool_names = {tool["name"] for tool in agent_runtime._resource_tool_definitions()}
        self.assertIn("place_phone_call", tool_names)
        self.assertIn("phone_call_status", tool_names)
        self.assertIn("recent_phone_calls", tool_names)
        self.assertIn("summarize_phone_call", tool_names)
        self.assertIn("place_phone_call", agent_runtime.HOST_TOOL_NAMES)

    def test_host_tool_forwards_session_and_runtime_lane(self) -> None:
        with mock.patch(
            "talos.phone.place_phone_call",
            return_value={"success": True, "call": {"call_id": "conv_123"}},
        ) as place_mock:
            payload = agent_runtime._invoke_host_tool(
                mcp_client=None,
                name="place_phone_call",
                arguments=json.dumps({"contact_or_number": "mom", "purpose": "pickup"}),
                session_id="main-pc",
                runtime_lane="foreground",
            )

        parsed = json.loads(payload)
        self.assertTrue(parsed["success"])
        place_mock.assert_called_once_with(
            "mom",
            purpose="pickup",
            brief_context="",
            message_to_deliver="",
            session_id="main-pc",
            runtime_lane="foreground",
        )


if __name__ == "__main__":
    unittest.main()
