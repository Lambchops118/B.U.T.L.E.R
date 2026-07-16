"""POST /notify: deterministic GUI banner ingress for the awareness backend."""

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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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


class NotifyEndpointTest(unittest.TestCase):
    def test_notify_enqueues_deterministic_ui_message(self) -> None:
        running = _RunningServer(_make_config(api_token="secret-token"))
        try:
            status, body = _post(
                running.url("/notify"),
                {"title": "Overflow detected: pot_1", "body": "Zone 1", "severity": "critical"},
                token="secret-token",
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            message = running.central_queue.get(timeout=2)
            self.assertEqual(message.type, "ui")
            self.assertFalse(message.needs_llm)  # deterministic: no LLM involved
            kind, title, text = message.payload
            self.assertEqual(kind, "VOICE_CMD")
            self.assertEqual(title, "[CRITICAL] Overflow detected: pot_1")
            self.assertEqual(text, "Zone 1")
        finally:
            running.close()

    def test_notify_requires_token(self) -> None:
        running = _RunningServer(_make_config(api_token="secret-token"))
        try:
            status, body = _post(
                running.url("/notify"), {"title": "x"}, token="wrong-token"
            )
            self.assertEqual(status, 401)
            self.assertFalse(body["ok"])
            self.assertTrue(running.central_queue.empty())
        finally:
            running.close()

    def test_notify_requires_title(self) -> None:
        running = _RunningServer(_make_config(api_token="secret-token"))
        try:
            status, body = _post(running.url("/notify"), {"body": "x"}, token="secret-token")
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
        finally:
            running.close()


if __name__ == "__main__":
    unittest.main()
