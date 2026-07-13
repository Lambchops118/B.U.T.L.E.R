from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.phone import PhoneCallStore

try:
    from starlette.testclient import TestClient
    from talos.phone_bridge import create_app
except ModuleNotFoundError:  # pragma: no cover - depends on local test environment
    TestClient = None
    create_app = None


class PhoneBridgeTests(unittest.TestCase):
    @unittest.skipIf(TestClient is None or create_app is None, "starlette is not installed in this test environment")
    def test_bridge_webhook_ingests_event_and_lists_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PhoneCallStore(Path(tmpdir) / "bridge.sqlite3")
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


if __name__ == "__main__":
    unittest.main()
