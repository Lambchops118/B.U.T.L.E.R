from __future__ import annotations

import asyncio
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.agent import tool_access
from butler.services import discord_messages

_ENV = {
    "BUTLER_PRIVILEGED_CALLER_TOKEN": "secret",
    "DISCORD_SERVICE_URL": "http://127.0.0.1:8000",
}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok(payload: dict) -> _Response:
    return _Response(json.dumps(payload).encode("utf-8"))


class SendMessageTests(unittest.TestCase):
    def test_posts_authenticated_request_and_returns_outcome(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            return_value=_ok({"status": "sent", "message_ids": ["1"]}),
        ) as urlopen:
            result = discord_messages.send_message("dinner is ready", "bob")

        self.assertEqual(result["status"], "sent")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/send-message")
        self.assertEqual(request.get_header("X-butler-privileged-token"), "secret")
        body = json.loads(request.data)
        self.assertEqual(body["recipient"], "bob")
        self.assertEqual(body["delivery"], "dm")
        self.assertTrue(body["idempotency_key"])

    def test_named_channel_forces_channel_delivery(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            return_value=_ok({"status": "sent"}),
        ) as urlopen:
            discord_messages.send_message("hi", "bob", "dm", "#general", "Gaming")
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["delivery"], "channel")
        self.assertEqual(body["channel"], "#general")
        self.assertEqual(body["server"], "Gaming")

    def test_channel_post_without_recipient_is_allowed(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            return_value=_ok({"status": "sent"}),
        ) as urlopen:
            discord_messages.send_message("hi", "", "dm", "general")
        body = json.loads(urlopen.call_args.args[0].data)
        self.assertIsNone(body["recipient"])
        self.assertNotIn("server", body)

    def test_invalid_inputs_never_hit_the_network(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen"
        ) as urlopen:
            self.assertEqual(discord_messages.send_message("", "bob")["status"], "invalid")
            self.assertEqual(discord_messages.send_message("hi", "")["status"], "invalid")
            self.assertEqual(discord_messages.send_message("hi", "bob", "sms")["status"], "invalid")
        urlopen.assert_not_called()

    def test_disabled_without_token(self) -> None:
        with mock.patch.dict("os.environ", {**_ENV, "BUTLER_PRIVILEGED_CALLER_TOKEN": ""}), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen"
        ) as urlopen:
            self.assertEqual(discord_messages.send_message("hi", "bob")["status"], "disabled")
        urlopen.assert_not_called()

    def test_unreachable_service_is_unavailable(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError("refused")),
        ):
            self.assertEqual(discord_messages.send_message("hi", "bob")["status"], "unavailable")

    def test_read_timeout_is_unknown_not_failed(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            self.assertEqual(discord_messages.send_message("hi", "bob")["status"], "unknown")

    def test_rejected_token_is_disabled(self) -> None:
        error = urllib.error.HTTPError(
            "http://x", 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"invalid privilege token"}')
        )
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen", side_effect=error
        ):
            result = discord_messages.send_message("hi", "bob")
        self.assertEqual(result["status"], "disabled")
        self.assertIn("invalid privilege token", result["error"])


class VoiceTests(unittest.TestCase):
    def test_join_without_channel_follows_owner(self) -> None:
        with mock.patch.dict(
            "os.environ", {**_ENV, "DISCORD_VOICE_CONTROL_URL": "http://127.0.0.1:8001"}
        ), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            return_value=_ok({"status": "joined"}),
        ) as urlopen:
            result = discord_messages.voice("join")
        self.assertEqual(result["status"], "joined")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8001/voice/join")
        self.assertEqual(json.loads(request.data), {})
        self.assertEqual(request.get_header("X-butler-privileged-token"), "secret")

    def test_join_named_channel_and_leave_server(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            return_value=_ok({"status": "left"}),
        ) as urlopen:
            discord_messages.voice("join", "raid night", "Gaming")
            discord_messages.voice("leave", "", "Gaming")
        join_req, leave_req = (c.args[0] for c in urlopen.call_args_list)
        self.assertEqual(json.loads(join_req.data), {"channel": "raid night", "server": "Gaming"})
        self.assertTrue(leave_req.full_url.endswith("/voice/leave"))
        self.assertEqual(json.loads(leave_req.data), {"server": "Gaming"})

    def test_status_is_a_get(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            return_value=_ok({"status": "ok", "connected": []}),
        ) as urlopen:
            discord_messages.voice("status")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.endswith("/voice/status"))

    def test_unknown_action_and_bot_not_running(self) -> None:
        with mock.patch.dict("os.environ", _ENV), mock.patch(
            "butler.services.discord_messages.urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError("refused")),
        ) as urlopen:
            self.assertEqual(discord_messages.voice("dance")["status"], "invalid")
            self.assertEqual(urlopen.call_count, 0)
            self.assertEqual(discord_messages.voice("join")["status"], "unavailable")


class ProviderTests(unittest.TestCase):
    def test_tools_registered_and_return_json(self) -> None:
        from mcp.server.fastmcp import FastMCP

        from butler.mcp_servers.providers.discord_messages import register

        server = FastMCP("test-discord")
        register(server)
        tools = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertEqual(
            tools,
            {
                "discord_send_message",
                "discord_list_contacts",
                "discord_list_channels",
                "discord_voice",
            },
        )

        with mock.patch.object(
            discord_messages, "send_message", return_value={"status": "ambiguous", "candidates": []}
        ) as send:
            result = asyncio.run(
                server.call_tool("discord_send_message", {"message": "hi", "recipient": "ob"})
            )
        send.assert_called_once_with("hi", "ob", "dm", "", "")
        text = json.dumps(result, default=str)
        self.assertIn("ambiguous", text)

    def test_restricted_discord_callers_cannot_use_it_by_default(self) -> None:
        with mock.patch.dict("os.environ", {"BUTLER_RESTRICTED_TOOL_ALLOWLIST": ""}):
            self.assertFalse(
                tool_access.is_tool_permitted("discord_send_message", tool_access.RESTRICTED)
            )
            self.assertTrue(tool_access.is_tool_permitted("discord_send_message", tool_access.FULL))


if __name__ == "__main__":
    unittest.main()
