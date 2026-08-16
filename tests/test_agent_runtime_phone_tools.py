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
    def test_phone_actions_are_published_as_one_meta_tool(self) -> None:
        # The four phone tools are published to the model as actions on a single
        # "phone" tool; each still dispatches to its original implementation.
        tool_names = {tool["name"] for tool in agent_runtime._resource_tool_definitions()}
        self.assertIn("phone", tool_names)

        actions = agent_runtime._HOST_META_TOOL_ACTIONS["phone"]
        self.assertEqual(
            set(actions.values()),
            {
                "place_phone_call",
                "phone_call_status",
                "recent_phone_calls",
                "summarize_phone_call",
            },
        )

        published = next(
            tool
            for tool in agent_runtime._resource_tool_definitions()
            if tool["name"] == "phone"
        )
        self.assertEqual(
            set(published["parameters"]["properties"]["action"]["enum"]),
            set(actions),
        )

        # Both the meta name and every legacy name stay executable.
        self.assertIn("phone", agent_runtime.HOST_TOOL_NAMES)
        self.assertIn("place_phone_call", agent_runtime.HOST_TOOL_NAMES)

    def test_phone_meta_tool_forwards_to_legacy_implementation(self) -> None:
        with mock.patch(
            "talos.phone.place_phone_call",
            return_value={"success": True, "call": {"call_id": "conv_123"}},
        ) as place_mock:
            payload = agent_runtime._invoke_host_tool(
                mcp_client=None,
                name="phone",
                arguments=json.dumps(
                    {
                        "action": "place_call",
                        "contact_or_number": "mom",
                        "purpose": "pickup",
                    }
                ),
                session_id="main-pc",
                runtime_lane="foreground",
            )

        self.assertTrue(json.loads(payload)["success"])
        place_mock.assert_called_once_with(
            "mom",
            purpose="pickup",
            brief_context="",
            message_to_deliver="",
            session_id="main-pc",
            runtime_lane="foreground",
        )

    def test_unknown_meta_action_is_reported_not_raised(self) -> None:
        payload = agent_runtime._invoke_host_tool(
            mcp_client=None,
            name="phone",
            arguments=json.dumps({"action": "not_a_real_action"}),
            session_id="main-pc",
            runtime_lane="foreground",
        )
        parsed = json.loads(payload)
        self.assertFalse(parsed["success"])
        self.assertIn("place_call", parsed["message"])

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
