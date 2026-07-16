from __future__ import annotations

import ipaddress
import json
import os
import queue
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.phone import reset_default_phone_provider, reset_default_phone_store
from talos.text.server import TextAgentHTTPServer, TextServerConfig


def _make_config(*, phone_push_token: str) -> TextServerConfig:
    return TextServerConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,
        api_token="",
        request_timeout=5,
        terminal_request_timeout=0,
        allowed_networks=(ipaddress.ip_network("127.0.0.1/32"),),
        phone_push_token=phone_push_token,
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
        self.thread.join(timeout=2)


def _post_json(url: str, body: dict, *, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class PhoneEventsEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TALOS_PHONE_PROVIDER": "elevenlabs_twilio",
                "TALOS_PHONE_DB_PATH": str(Path(self._tmpdir.name) / "phone.sqlite3"),
            },
            clear=False,
        )
        self._env_patch.start()
        reset_default_phone_provider()
        reset_default_phone_store()

    def tearDown(self) -> None:
        reset_default_phone_provider()
        reset_default_phone_store()
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _sample_call(self, call_id: str = "conv_evt") -> dict:
        return {
            "call_id": call_id,
            "provider": "elevenlabs_twilio",
            "conversation_id": call_id,
            "agent_id": "agent_123",
            "session_id": "main-pc",
            "direction": "outbound",
            "remote_number": "+15555550123",
            "contact_name": "Mom",
            "purpose": "Pickup",
            "status": "completed",
            "outcome": "completed",
            "transcript": [
                {"role": "user", "message": "Can you pick me up at six?"},
                {"role": "assistant", "message": "Sure, I will let TALOS know."},
            ],
        }

    def test_valid_push_persists_call_and_enqueues_event(self) -> None:
        running = _RunningServer(_make_config(phone_push_token="push-secret"))
        try:
            status, body = _post_json(
                running.url("/phone/events"),
                {"call": self._sample_call()},
                headers={"X-Phone-Push-Token": "push-secret"},
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["call_id"], "conv_evt")

            message = running.central_queue.get(timeout=2)
            self.assertEqual(message.type, "event")
            self.assertEqual(message.payload.name, "phone_call_completed")
            self.assertEqual(message.payload.data["call_id"], "conv_evt")
            self.assertEqual(message.payload.data["session_id"], "main-pc")
            self.assertTrue(message.needs_llm)
        finally:
            running.close()

    def test_missing_token_is_rejected(self) -> None:
        running = _RunningServer(_make_config(phone_push_token="push-secret"))
        try:
            status, body = _post_json(running.url("/phone/events"), {"call": self._sample_call()})
            self.assertEqual(status, 401)
            self.assertFalse(body["ok"])
        finally:
            running.close()

    def test_wrong_token_is_rejected(self) -> None:
        running = _RunningServer(_make_config(phone_push_token="push-secret"))
        try:
            status, body = _post_json(
                running.url("/phone/events"),
                {"call": self._sample_call()},
                headers={"X-Phone-Push-Token": "wrong-token"},
            )
            self.assertEqual(status, 401)
            self.assertFalse(body["ok"])
        finally:
            running.close()

    def test_unconfigured_token_fails_closed(self) -> None:
        running = _RunningServer(_make_config(phone_push_token=""))
        try:
            status, body = _post_json(
                running.url("/phone/events"),
                {"call": self._sample_call()},
                headers={"X-Phone-Push-Token": "anything"},
            )
            self.assertEqual(status, 401)
            self.assertIn("not configured", body["error"])
        finally:
            running.close()

    def test_malformed_body_is_rejected(self) -> None:
        running = _RunningServer(_make_config(phone_push_token="push-secret"))
        try:
            status, body = _post_json(
                running.url("/phone/events"),
                {"not_call": True},
                headers={"X-Phone-Push-Token": "push-secret"},
            )
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
        finally:
            running.close()


if __name__ == "__main__":
    unittest.main()
