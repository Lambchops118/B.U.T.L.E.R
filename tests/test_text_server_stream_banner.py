"""POST /chat/stream must also publish the finished turn to the GUI.

The streaming lane runs the agent directly instead of routing through
``router_loop``, so unlike ``/chat`` it has to enqueue the ui message itself.
Without that, the whole voice lane -- every wake-word command and every spoken
reply -- completes without ever reaching the pygame panel.
"""

from __future__ import annotations

import ipaddress
import json
import queue
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.text import server as text_server
from talos.text.server import TextAgentHTTPServer, TextServerConfig


def _make_config(*, api_token: str) -> TextServerConfig:
    return TextServerConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,
        api_token=api_token,
        request_timeout=5,
        terminal_request_timeout=0,
        allowed_networks=(ipaddress.ip_network("127.0.0.1/32"),),
        phone_push_token="",
    )


class _RunningServer:
    def __init__(self, config: TextServerConfig) -> None:
        self.central_queue: queue.Queue = queue.Queue()
        self.server = TextAgentHTTPServer(("127.0.0.1", 0), self.central_queue, config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _post_stream(url: str, body: dict, token: str) -> list[dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    request.add_header("Authorization", f"Bearer {token}")
    events: list[dict] = []
    with urllib.request.urlopen(request, timeout=10) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


class StreamBannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_stream = text_server.agent_runtime.run_command_stream
        self._original_snapshot = text_server._stream_state_snapshot
        text_server._stream_state_snapshot = lambda body: "no recent status"

    def tearDown(self) -> None:
        text_server.agent_runtime.run_command_stream = self._original_stream
        text_server._stream_state_snapshot = self._original_snapshot

    def _ui_message(self, running: _RunningServer):
        """The first ui message on the queue, ignoring unrelated traffic."""
        deadline_messages = []
        while True:
            message = running.central_queue.get(timeout=5)
            deadline_messages.append(message)
            if message.type == "ui":
                return message

    def test_completed_stream_publishes_command_and_response(self) -> None:
        def fake_stream(command, snapshot, **kwargs):
            yield "The monstera "
            yield "has been watered."

        text_server.agent_runtime.run_command_stream = fake_stream
        running = _RunningServer(_make_config(api_token="secret-token"))
        try:
            events = _post_stream(
                running.url("/chat/stream"),
                {"message": "water the monstera", "session_id": "voice", "source": "voice"},
                token="secret-token",
            )
            self.assertEqual(events[-1]["type"], "done")

            message = self._ui_message(running)
            kind, command, response = message.payload
            self.assertEqual(kind, "VOICE_CMD")
            self.assertEqual(command, "water the monstera")
            self.assertEqual(response, "The monstera has been watered.")
        finally:
            running.close()

    def test_failed_stream_still_publishes_what_was_said(self) -> None:
        """A barge-in or mid-turn error must not erase the turn from the panel."""

        def fake_stream(command, snapshot, **kwargs):
            yield "Of course, sir."
            raise RuntimeError("model went away")

        text_server.agent_runtime.run_command_stream = fake_stream
        running = _RunningServer(_make_config(api_token="secret-token"))
        try:
            events = _post_stream(
                running.url("/chat/stream"),
                {"message": "water the monstera", "session_id": "voice", "source": "voice"},
                token="secret-token",
            )
            self.assertEqual(events[-1]["type"], "error")

            message = self._ui_message(running)
            kind, command, response = message.payload
            self.assertEqual(kind, "VOICE_CMD")
            self.assertEqual(command, "water the monstera")
            self.assertEqual(response, "Of course, sir.")
        finally:
            running.close()


if __name__ == "__main__":
    unittest.main()
