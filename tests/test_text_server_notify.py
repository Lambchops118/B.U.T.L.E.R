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
    def setUp(self) -> None:
        # /speak holds back noncritical announcements while sleep mode is on, so
        # these assertions would otherwise depend on whether the machine running
        # the suite happens to be asleep. Pin it to a throwaway state file, which
        # reads as awake.
        from unittest.mock import patch
        import tempfile

        import talos.services.sleep_mode as sleep_mode

        state_path = Path(tempfile.mkdtemp(prefix="talos_notify_test_")) / "sleep.json"
        self.enterContext(patch.object(sleep_mode, "STATE_PATH", state_path))
        sleep_mode._cache, sleep_mode._cache_read_at = None, 0.0

        def _reset() -> None:
            sleep_mode._cache, sleep_mode._cache_read_at = None, 0.0

        self.addCleanup(_reset)

    def test_speak_routes_exact_text_without_task_or_presence(self) -> None:
        from unittest.mock import patch
        from talos import router
        running = _RunningServer(_make_config(api_token="secret-token"))
        try:
            text = "The user just arrived home. A background job completed."
            status, response = _post(running.url("/speak"),
                {"title": "Arrival briefing", "body": text}, token="secret-token")
            self.assertEqual(status, 200)
            self.assertTrue(response["enqueued"])
            running.central_queue.put(None)
            gui = queue.Queue()
            with patch.object(router, "JobManager") as jobs, \
                 patch.object(router, "_classify_with_context") as classify, \
                 patch.object(router, "_run_agent_command") as agent, \
                 patch.object(router, "_speak_via_voice_worker") as speak, \
                 patch.object(router, "awareness_signals") as signals:
                router.router_loop(running.central_queue, gui)
            speak.assert_called_once_with(text)
            classify.assert_not_called()
            agent.assert_not_called()
            jobs.return_value.submit.assert_not_called()
            self.assertEqual(signals.mock_calls, [])
            self.assertEqual(gui.get_nowait(), ("VOICE_CMD", "[NOTICE] Arrival briefing", text))
        finally:
            running.close()

    def test_speak_requires_auth_and_uses_title_for_empty_body(self) -> None:
        running = _RunningServer(_make_config(api_token="secret-token"))
        try:
            self.assertEqual(_post(running.url("/speak"), {"title": "Welcome home"})[0], 401)
            self.assertTrue(running.central_queue.empty())
            self.assertEqual(_post(running.url("/speak"), {"body": "hello"}, token="secret-token")[0], 400)
            self.assertTrue(running.central_queue.empty())
            self.assertEqual(_post(running.url("/speak"), {"title": "Welcome home"}, token="secret-token")[0], 200)
            message = running.central_queue.get_nowait()
            self.assertEqual(message.type, "announcement")
            self.assertFalse(message.needs_llm)
            self.assertEqual(message.payload.text, "Welcome home")
        finally:
            running.close()

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
