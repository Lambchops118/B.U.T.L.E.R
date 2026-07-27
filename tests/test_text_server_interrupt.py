"""POST /interrupt: the voice worker reporting that the user talked over a reply."""

from __future__ import annotations

import ipaddress
import json
import queue
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime as agent_runtime
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


def _post(url: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class InterruptEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.running = _RunningServer(_make_config(api_token="secret-token"))
        self.addCleanup(self.running.close)
        patcher = mock.patch.object(text_server, "emit_pipeline_event")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cancels_the_in_flight_turn_and_records_what_was_heard(self) -> None:
        token = agent_runtime.register_cancel("req-abc")
        self.addCleanup(agent_runtime.release_cancel, "req-abc")

        with mock.patch.object(agent_runtime, "note_interruption", return_value=True) as note:
            status, body = _post(
                self.running.url("/interrupt"),
                {
                    "session_id": "voice-worker",
                    "request_id": "req-abc",
                    "spoken_text": "Channel one is primed.",
                },
                token="secret-token",
            )

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["cancelled"])
        self.assertTrue(body["recorded"])
        self.assertTrue(token.is_set())
        note.assert_called_once_with("voice-worker", "Channel one is primed.")

    def test_a_reply_that_already_finished_generating_is_still_corrected(self) -> None:
        """The common case: the model finished long before the audio did."""
        with mock.patch.object(agent_runtime, "note_interruption", return_value=True) as note:
            status, body = _post(
                self.running.url("/interrupt"),
                {
                    "session_id": "voice-worker",
                    "request_id": "already-done",
                    "spoken_text": "Channel one is primed.",
                },
                token="secret-token",
            )

        self.assertEqual(status, 200)
        self.assertFalse(body["cancelled"])
        self.assertTrue(body["recorded"])
        note.assert_called_once()

    def test_works_without_a_request_id(self) -> None:
        """Proactive speech has no streaming turn behind it, only a record."""
        with mock.patch.object(agent_runtime, "note_interruption", return_value=True) as note:
            status, body = _post(
                self.running.url("/interrupt"),
                {"session_id": "voice", "spoken_text": "Reminder: the kettle."},
                token="secret-token",
            )

        self.assertEqual(status, 200)
        self.assertFalse(body["cancelled"])
        note.assert_called_once_with("voice", "Reminder: the kettle.")

    def test_requires_a_session_id(self) -> None:
        status, body = _post(
            self.running.url("/interrupt"),
            {"request_id": "req-abc"},
            token="secret-token",
        )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_requires_the_token(self) -> None:
        with mock.patch.object(agent_runtime, "note_interruption") as note:
            status, _ = _post(
                self.running.url("/interrupt"),
                {"session_id": "voice-worker"},
                token="wrong-token",
            )
        self.assertEqual(status, 401)
        note.assert_not_called()


if __name__ == "__main__":
    unittest.main()
