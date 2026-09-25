from __future__ import annotations

import queue
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.sms import server as sms_server
from butler.sms.server import SMS_TURN_CONTEXT, SmsWebhookServer
from butler.sms.twilio import SmsConfig, compute_signature, fit_sms_body, is_valid_signature


PUBLIC_URL = "https://butler.example.ts.net/sms"
AUTH_TOKEN = "test-auth-token"
OWNER = "+15555550123"


def _config(**overrides) -> SmsConfig:
    values = dict(
        enabled=True,
        host="127.0.0.1",
        port=0,
        public_url=PUBLIC_URL,
        account_sid="AC123",
        auth_token=AUTH_TOKEN,
        from_number="+15555550100",
        allowed_senders=(OWNER,),
        reply_timeout=5,
        job_follow_up_timeout=5,
        max_reply_chars=1500,
    )
    values.update(overrides)
    return SmsConfig(**values)


class _Running:
    def __init__(self, config: SmsConfig) -> None:
        self.central_queue: queue.Queue = queue.Queue()
        self.sent: list[tuple[str, str]] = []
        self.sent_event = threading.Event()

        def send(to: str, body: str) -> None:
            self.sent.append((to, body))
            self.sent_event.set()

        self.server = SmsWebhookServer(("127.0.0.1", 0), self.central_queue, config, send=send)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/sms"

    def post(self, params: dict[str, str], *, signature: str | None = None) -> int:
        if signature is None:
            signature = compute_signature(AUTH_TOKEN, PUBLIC_URL, {k: [v] for k, v in params.items()})
        request = urllib.request.Request(
            self.url,
            data=urllib.parse.urlencode(params).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Twilio-Signature": signature,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _message(body: str = "water the plants", sender: str = OWNER, sid: str = "SM1") -> dict[str, str]:
    return {"MessageSid": sid, "From": sender, "To": "+15555550100", "Body": body}


class SignatureTests(unittest.TestCase):
    def test_signature_round_trip_and_tamper(self) -> None:
        params = {"Body": ["hi"], "From": [OWNER]}
        signature = compute_signature(AUTH_TOKEN, PUBLIC_URL, params)
        self.assertTrue(is_valid_signature(AUTH_TOKEN, PUBLIC_URL, params, signature))
        self.assertFalse(is_valid_signature(AUTH_TOKEN, PUBLIC_URL, {"Body": ["bye"], "From": [OWNER]}, signature))
        self.assertFalse(is_valid_signature(AUTH_TOKEN, PUBLIC_URL + "x", params, signature))
        self.assertFalse(is_valid_signature("", PUBLIC_URL, params, signature))

    def test_matches_twilio_documented_example(self) -> None:
        params = {
            "CallSid": ["CA1234567890ABCDE"],
            "Caller": ["+12349013030"],
            "Digits": ["1234"],
            "From": ["+12349013030"],
            "To": ["+18005551212"],
        }
        self.assertEqual(
            compute_signature("12345", "https://mycompany.com/myapp.php?foo=1&bar=2", params),
            "0/KCTR6DLpKmkAf8muzZqo1nDgQ=",
        )

    def test_fit_body_truncates(self) -> None:
        self.assertEqual(fit_sms_body("", 200), "(no response)")
        self.assertEqual(len(fit_sms_body("x" * 500, 200)), 200)


class ConfigTests(unittest.TestCase):
    def test_missing_requirements_blocks_start(self) -> None:
        config = _config(public_url="", auth_token="", allowed_senders=())
        missing = config.missing_requirements()
        self.assertIn("BUTLER_SMS_PUBLIC_URL", missing)
        self.assertIn("TWILIO_AUTH_TOKEN", missing)
        self.assertIn("BUTLER_SMS_ALLOWED_SENDERS", missing)
        with mock.patch.object(sms_server.SmsConfig, "from_env", return_value=config):
            self.assertIsNone(sms_server.start_sms_server(queue.Queue()))

    def test_complete_config_has_no_missing_requirements(self) -> None:
        self.assertEqual(_config().missing_requirements(), [])


class WebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.running = _Running(_config())

    def tearDown(self) -> None:
        self.running.close()

    def _next_command(self):
        return self.running.central_queue.get(timeout=3)

    def test_rejects_bad_signature(self) -> None:
        self.assertEqual(self.running.post(_message(), signature="bogus"), 403)
        self.assertTrue(self.running.central_queue.empty())

    def test_ignores_unknown_sender_without_reply(self) -> None:
        self.assertEqual(self.running.post(_message(sender="+15555559999")), 200)
        time.sleep(0.2)
        self.assertTrue(self.running.central_queue.empty())
        self.assertEqual(self.running.sent, [])

    def test_foreground_command_is_replied_by_text(self) -> None:
        self.assertEqual(self.running.post(_message()), 200)
        message = self._next_command()
        payload = message.payload
        self.assertEqual(message.type, "text_cmd")
        self.assertEqual(payload.command, "water the plants")
        self.assertEqual(payload.session_id, f"sms:{OWNER}")
        self.assertEqual(payload.source, "sms")
        self.assertEqual(payload.extra_context, SMS_TURN_CONTEXT)
        payload.reply_queue.put({"ok": True, "mode": "foreground", "response": "Watered pot 1."})
        self.assertTrue(self.running.sent_event.wait(3))
        self.assertEqual(self.running.sent, [(OWNER, "Watered pot 1.")])

    def test_duplicate_message_sid_is_processed_once(self) -> None:
        self.assertEqual(self.running.post(_message(sid="SMdup")), 200)
        self.assertEqual(self.running.post(_message(sid="SMdup")), 200)
        self._next_command()
        time.sleep(0.2)
        self.assertTrue(self.running.central_queue.empty())

    def test_agent_error_is_reported(self) -> None:
        self.running.post(_message())
        self._next_command().payload.reply_queue.put({"ok": False, "error": "pump offline"})
        self.assertTrue(self.running.sent_event.wait(3))
        self.assertEqual(self.running.sent, [(OWNER, "That failed: pump offline")])

    def test_background_job_result_follows_ack(self) -> None:
        job = mock.Mock(status="succeeded", result_payload={"response": "Report ready."}, result_summary=None)
        store = mock.Mock()
        store.get_job.return_value = job
        with mock.patch("butler.jobs.get_default_job_store", return_value=store):
            self.running.post(_message(body="research grow lights"))
            self._next_command().payload.reply_queue.put(
                {"ok": True, "mode": "background", "job_id": "job-1", "response": "Working on it."}
            )
            deadline = time.monotonic() + 3
            while len(self.running.sent) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertEqual(self.running.sent, [(OWNER, "Working on it."), (OWNER, "Report ready.")])
        store.get_job.assert_called_with("job-1")


if __name__ == "__main__":
    unittest.main()
