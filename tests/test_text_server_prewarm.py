"""POST /prewarm: the voice worker warning that a turn is a moment away."""

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


class PrewarmEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.running = _RunningServer(_make_config(api_token="secret-token"))
        self.addCleanup(self.running.close)

    def test_triggers_the_pre_ramp(self) -> None:
        called = threading.Event()
        with mock.patch.object(
            agent_runtime, "prewarm_llm", side_effect=lambda: called.set()
        ):
            status, body = _post(
                self.running.url("/prewarm"), {}, token="secret-token"
            )
            self.assertTrue(called.wait(timeout=5))

        self.assertEqual(status, 202)
        self.assertTrue(body["ok"])

    def test_answers_without_waiting_for_the_model_server(self) -> None:
        """The caller is on the audio path, so the response cannot block on Ollama."""
        release = threading.Event()
        self.addCleanup(release.set)

        def slow_preramp() -> dict:
            release.wait(timeout=5)
            return {"ok": True}

        with mock.patch.object(agent_runtime, "prewarm_llm", side_effect=slow_preramp):
            status, body = _post(
                self.running.url("/prewarm"), {}, token="secret-token"
            )

        self.assertEqual(status, 202)
        self.assertTrue(body["ok"])

    def test_requires_the_api_token(self) -> None:
        with mock.patch.object(agent_runtime, "prewarm_llm") as preramp:
            status, _body = _post(self.running.url("/prewarm"), {})

        self.assertEqual(status, 401)
        preramp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
