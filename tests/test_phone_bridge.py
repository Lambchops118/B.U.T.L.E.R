from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.phone import PhoneCallStore

try:
    from starlette.testclient import TestClient
    from talos.phone_bridge import create_app

    # talos/phone_bridge/__init__.py does `from talos.phone_bridge.app import app, ...`,
    # which rebinds the `app` attribute on the `talos.phone_bridge` package to the
    # Starlette instance. That shadows the submodule for any attribute-chain lookup
    # (including `import talos.phone_bridge.app as x` and mock.patch's dotted-string
    # resolution), so fetch the real module straight from sys.modules instead.
    phone_bridge_app_module = sys.modules["talos.phone_bridge.app"]
    _push_call_to_main = phone_bridge_app_module._push_call_to_main
except ModuleNotFoundError:  # pragma: no cover - depends on local test environment
    TestClient = None
    create_app = None
    phone_bridge_app_module = None
    _push_call_to_main = None


class PhoneBridgeTests(unittest.TestCase):
    @unittest.skipIf(TestClient is None or create_app is None, "starlette is not installed in this test environment")
    def test_bridge_webhook_ingests_event_and_lists_calls(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            store = PhoneCallStore(Path(tmpdir) / "bridge.sqlite3")
            try:
                app = create_app(store=store, api_token="bridge-api", webhook_token="bridge-hook")
                client = TestClient(app)

                webhook_payload = {
                    "type": "post_call_transcription",
                    "event_timestamp": 1739537297,
                    "data": {
                        "agent_id": "agent_123",
                        "conversation_id": "conv_123",
                        "status": "done",
                        "transcript": [{"role": "user", "message": "Hello from the phone bridge."}],
                    },
                }

                webhook_response = client.post(
                    "/webhooks/elevenlabs?token=bridge-hook",
                    json=webhook_payload,
                )
                self.assertEqual(webhook_response.status_code, 200)

                unauthenticated = client.get("/calls")
                self.assertEqual(unauthenticated.status_code, 401)

                calls_response = client.get(
                    "/calls",
                    headers={"Authorization": "Bearer bridge-api"},
                )
                self.assertEqual(calls_response.status_code, 200)
                body = calls_response.json()
                self.assertTrue(body["ok"])
                self.assertEqual(len(body["calls"]), 1)
                self.assertEqual(body["calls"][0]["call_id"], "conv_123")
                self.assertEqual(body["calls"][0]["status"], "completed")
            finally:
                store.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class PhoneBridgePushTests(unittest.TestCase):
    @unittest.skipIf(TestClient is None or create_app is None, "starlette is not installed in this test environment")
    def test_terminal_webhook_event_triggers_main_push(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            store = PhoneCallStore(Path(tmpdir) / "bridge.sqlite3")
            try:
                app = create_app(store=store, api_token="bridge-api", webhook_token="bridge-hook")
                client = TestClient(app)

                push_calls: list[str] = []
                push_done = threading.Event()

                def fake_push(config: object, record: object) -> None:
                    push_calls.append(record.call_id)  # type: ignore[attr-defined]
                    push_done.set()

                with mock.patch.object(phone_bridge_app_module, "_push_call_to_main", side_effect=fake_push):
                    response = client.post(
                        "/webhooks/elevenlabs?token=bridge-hook",
                        json={
                            "type": "post_call_transcription",
                            "event_timestamp": 1739537297,
                            "data": {
                                "agent_id": "agent_123",
                                "conversation_id": "conv_push",
                                "status": "done",
                                "transcript": [{"role": "user", "message": "Push me please."}],
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(push_done.wait(timeout=2), "main-process push was not triggered")

                self.assertEqual(push_calls, ["conv_push"])
            finally:
                store.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @unittest.skipIf(TestClient is None or create_app is None, "starlette is not installed in this test environment")
    def test_non_terminal_webhook_event_does_not_push(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            store = PhoneCallStore(Path(tmpdir) / "bridge.sqlite3")
            try:
                app = create_app(store=store, api_token="bridge-api", webhook_token="bridge-hook")
                client = TestClient(app)

                with mock.patch.object(phone_bridge_app_module, "_push_call_to_main") as push_mock:
                    response = client.post(
                        "/webhooks/elevenlabs?token=bridge-hook",
                        json={
                            "type": "conversation_started",
                            "event_timestamp": 1739537297,
                            "data": {
                                "agent_id": "agent_123",
                                "conversation_id": "conv_other",
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    time.sleep(0.05)

                push_mock.assert_not_called()
            finally:
                store.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_push_call_to_main_skips_silently_when_url_unset(self) -> None:
        if _push_call_to_main is None:
            self.skipTest("starlette is not installed in this test environment")
        from talos.phone.provider import PhoneConfig

        config = PhoneConfig(
            enabled=True,
            provider_name="elevenlabs_twilio",
            api_key="",
            agent_id="",
            phone_number_id="",
            allowed_outbound=False,
            bridge_url="",
            bridge_token="",
            contacts={},
            allowlist=(),
            bridge_sync_limit=25,
            main_notify_url="",
            main_notify_token="",
        )
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            _push_call_to_main(config, mock.Mock())
        urlopen_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
