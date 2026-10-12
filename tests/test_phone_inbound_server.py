from __future__ import annotations

import hashlib
import hmac
import json
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

from butler.memory.store import MemoryStore
from butler.phone.inbound_server import (
    PHONE_TURN_CONTEXT,
    REJECT_TWIML,
    UNAVAILABLE_TWIML,
    PhoneInboundConfig,
    PhoneInboundServer,
    is_valid_elevenlabs_signature,
)
from butler.sms.twilio import compute_signature


TOKEN = "inbound-test-token"
TWILIO_TOKEN = "twilio-auth-token"
VOICE_URL = "https://talos.example.ts.net/phone/twilio/voice"
OWNER = "+15555550123"
STRANGER = "+15555550999"
TWILIO_NUMBER = "+15555550100"
AGENT_TWIML = '<?xml version="1.0"?><Response><Connect><Stream url="wss://x"/></Connect></Response>'


def _config(**overrides) -> PhoneInboundConfig:
    values = dict(
        enabled=True,
        host="127.0.0.1",
        port=0,
        token=TOKEN,
        webhook_secret="whsec",
        allowed_callers=(OWNER,),
        accepted_attestations=("TN-Validation-Passed-A",),
        twilio_auth_token=TWILIO_TOKEN,
        voice_public_url=VOICE_URL,
        ask_timeout=2,
        briefing_max_chars=2400,
    )
    values.update(overrides)
    return PhoneInboundConfig(**values)


class _Running:
    def __init__(self, config: PhoneInboundConfig, *, register_error: Exception | None = None) -> None:
        self.central_queue: queue.Queue = queue.Queue()
        self.memory = MemoryStore(":memory:")
        self.memory.upsert_fact("user", "favorite_color", "Owner's favorite color is green.", salience=5)
        self.registered: list[tuple[str, str, dict]] = []

        def register(from_number: str, to_number: str, variables: dict) -> str:
            if register_error is not None:
                raise register_error
            self.registered.append((from_number, to_number, variables))
            return AGENT_TWIML

        self.server = PhoneInboundServer(
            ("127.0.0.1", 0),
            self.central_queue,
            config,
            register_fn=register,
            situation_fn=lambda: "Kitchen lights on.",
            memory_store=self.memory,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, caller: str, verstat: str | None, *, signature: str | None = None) -> tuple[int, str]:
        params = {"From": caller, "To": TWILIO_NUMBER, "CallSid": "CA1"}
        if verstat is not None:
            params["StirVerstat"] = verstat
        if signature is None:
            signature = compute_signature(TWILIO_TOKEN, VOICE_URL, {k: [v] for k, v in params.items()})
        request = urllib.request.Request(
            f"{self.base}/phone/twilio/voice",
            data=urllib.parse.urlencode(params).encode("utf-8"),
            method="POST",
        )
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("X-Twilio-Signature", signature)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def admitted_token(self) -> str:
        status, twiml = self.call(OWNER, "TN-Validation-Passed-A")
        assert status == 200 and twiml == AGENT_TWIML, twiml
        return self.registered[-1][2]["talos_call_token"]

    def post(self, path: str, body: dict, *, token: str | None = TOKEN, headers: dict | None = None):
        raw = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(f"{self.base}{path}", data=raw, method="POST")
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def answer_next(self, response: str = "Done.") -> threading.Thread:
        """Play the router: take the next queued turn and reply."""
        captured: dict = {}

        def run() -> None:
            message = self.central_queue.get(timeout=5)
            captured["payload"] = message.payload
            message.payload.reply_queue.put({"ok": True, "response": response})

        thread = threading.Thread(target=run, daemon=True)
        thread.captured = captured  # type: ignore[attr-defined]
        thread.start()
        return thread

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.memory.close()


class IncomingCallGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.running = _Running(_config())

    def tearDown(self) -> None:
        self.running.close()

    def test_a_attested_allowlisted_call_is_handed_to_agent_with_briefing(self) -> None:
        status, twiml = self.running.call(OWNER, "TN-Validation-Passed-A")
        self.assertEqual(status, 200)
        self.assertEqual(twiml, AGENT_TWIML)
        from_number, to_number, variables = self.running.registered[0]
        self.assertEqual((from_number, to_number), (OWNER, TWILIO_NUMBER))
        self.assertEqual(variables["talos_session_id"], f"phone:{OWNER}")
        self.assertIn("favorite color is green", variables["talos_briefing"])
        self.assertIn("Kitchen lights on.", variables["talos_briefing"])
        self.assertIn("does not track phones", variables["talos_briefing"])
        self.assertTrue(variables["talos_call_token"])

    def test_rejects_weaker_or_missing_attestation(self) -> None:
        for verstat in ("TN-Validation-Passed-B", "TN-Validation-Passed-C", "TN-Validation-Failed-A", "No-TN-Validation", None):
            status, twiml = self.running.call(OWNER, verstat)
            self.assertEqual((status, twiml), (200, REJECT_TWIML), verstat)
        self.assertEqual(self.running.registered, [])

    def test_rejects_attested_stranger(self) -> None:
        status, twiml = self.running.call(STRANGER, "TN-Validation-Passed-A")
        self.assertEqual(twiml, REJECT_TWIML)
        self.assertEqual(self.running.registered, [])

    def test_rejects_bad_twilio_signature(self) -> None:
        status, _ = self.running.call(OWNER, "TN-Validation-Passed-A", signature="forged")
        self.assertEqual(status, 403)
        self.assertEqual(self.running.registered, [])

    def test_register_failure_plays_unavailable(self) -> None:
        self.running.close()
        self.running = _Running(_config(), register_error=RuntimeError("down"))
        _, twiml = self.running.call(OWNER, "TN-Validation-Passed-A")
        self.assertEqual(twiml, UNAVAILABLE_TWIML)

    def test_accepted_attestations_are_configurable(self) -> None:
        self.running.close()
        self.running = _Running(
            _config(accepted_attestations=("TN-Validation-Passed-A", "TN-Validation-Passed-A-Diverted"))
        )
        _, twiml = self.running.call(OWNER, "TN-Validation-Passed-A-Diverted")
        self.assertEqual(twiml, AGENT_TWIML)


class MidCallToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.running = _Running(_config())
        self.call_token = self.running.admitted_token()

    def tearDown(self) -> None:
        self.running.close()

    def test_tools_need_bearer_token(self) -> None:
        status, _ = self.running.post("/phone/tools/recall", {"call_token": self.call_token}, token=None)
        self.assertEqual(status, 401)

    def test_tools_refuse_unadmitted_call(self) -> None:
        for tool in ("recall", "house_status", "ask_talos"):
            _, body = self.running.post(f"/phone/tools/{tool}", {"call_token": "made-up", "request": "x"})
            self.assertEqual(body["error"], "call_not_admitted", tool)
        self.assertTrue(self.running.central_queue.empty())

    def test_recall_searches_memory(self) -> None:
        _, body = self.running.post("/phone/tools/recall", {"call_token": self.call_token, "query": "favorite color"})
        self.assertTrue(body["found"])
        self.assertIn("green", body["memory"])

    def test_house_status(self) -> None:
        _, body = self.running.post("/phone/tools/house_status", {"call_token": self.call_token})
        self.assertEqual(body, {"ok": True, "status": "Kitchen lights on."})

    def test_ask_talos_runs_full_access_foreground_turn_in_caller_session(self) -> None:
        router = self.running.answer_next("Kitchen lights are off.")
        _, body = self.running.post(
            "/phone/tools/ask_talos", {"call_token": self.call_token, "request": "turn off the kitchen lights"}
        )
        router.join(5)
        self.assertEqual(body, {"ok": True, "answer": "Kitchen lights are off."})
        payload = router.captured["payload"]  # type: ignore[attr-defined]
        self.assertEqual(payload.command, "turn off the kitchen lights")
        self.assertEqual(payload.source, "phone")
        self.assertEqual(payload.session_id, f"phone:{OWNER}")
        self.assertEqual(payload.tool_access, "full")
        self.assertEqual(payload.requested_mode, "foreground")
        self.assertEqual(payload.extra_context, PHONE_TURN_CONTEXT)

    def test_ask_talos_timeout_returns_in_progress(self) -> None:
        _, body = self.running.post("/phone/tools/ask_talos", {"call_token": self.call_token, "request": "slow"})
        self.assertEqual(body["error"], "timeout")


class PostCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.running = _Running(_config())

    def tearDown(self) -> None:
        self.running.close()

    def test_postcall_ingests_and_announces(self) -> None:
        fake_record = mock.Mock(call_id="conv9", session_id=f"phone:{OWNER}", direction="inbound", summary="s")
        with mock.patch("butler.phone.service.ingest_phone_webhook_event", return_value=fake_record) as ingest:
            status, body = self.running.post(
                "/phone/postcall", {"type": "post_call_transcription", "data": {"conversation_id": "conv9"}}
            )
        self.assertEqual((status, body["call_id"]), (200, "conv9"))
        ingest.assert_called_once()
        event = self.running.central_queue.get_nowait()
        self.assertEqual(event.payload.name, "phone_call_completed")
        self.assertEqual(event.payload.data["session_id"], f"phone:{OWNER}")

    def test_postcall_accepts_elevenlabs_signature(self) -> None:
        payload = {"type": "call_initiation_failure", "data": {"conversation_id": "x"}}
        raw = json.dumps(payload).encode("utf-8")
        timestamp = str(int(time.time()))
        digest = hmac.new(b"whsec", f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
        with mock.patch("butler.phone.service.ingest_phone_webhook_event", return_value=mock.Mock(call_id="x")):
            status, _ = self.running.post(
                "/phone/postcall", payload, token=None, headers={"ElevenLabs-Signature": f"t={timestamp},v0={digest}"}
            )
        self.assertEqual(status, 200)
        # A failure event is stored but not announced.
        self.assertTrue(self.running.central_queue.empty())

    def test_postcall_rejects_unauthenticated(self) -> None:
        status, _ = self.running.post("/phone/postcall", {"type": "x"}, token=None)
        self.assertEqual(status, 401)


class HelperTests(unittest.TestCase):
    def test_signature_checks(self) -> None:
        raw = b'{"a":1}'
        digest = hmac.new(b"s", b"100." + raw, hashlib.sha256).hexdigest()
        self.assertTrue(is_valid_elevenlabs_signature("s", raw, f"t=100,v0={digest}", now=100))
        self.assertFalse(is_valid_elevenlabs_signature("s", raw, f"t=100,v0={digest}", now=100 + 3600))
        self.assertFalse(is_valid_elevenlabs_signature("other", raw, f"t=100,v0={digest}", now=100))
        self.assertFalse(is_valid_elevenlabs_signature("", raw, f"t=100,v0={digest}", now=100))

    def test_missing_requirements(self) -> None:
        self.assertEqual(
            _config(token="", allowed_callers=(), twilio_auth_token="", voice_public_url="").missing_requirements(),
            [
                "BUTLER_PHONE_INBOUND_TOKEN",
                "BUTLER_PHONE_INBOUND_ALLOWED_CALLERS",
                "TWILIO_AUTH_TOKEN",
                "BUTLER_PHONE_VOICE_PUBLIC_URL",
            ],
        )
        self.assertEqual(_config().missing_requirements(), [])


class RegisterCallTests(unittest.TestCase):
    def test_register_inbound_call_posts_documented_shape_and_returns_twiml(self) -> None:
        from butler.phone.elevenlabs_twilio import ElevenLabsTwilioProvider
        from butler.phone.provider import PhoneConfig

        provider = ElevenLabsTwilioProvider(
            PhoneConfig(
                enabled=True, provider_name="elevenlabs_twilio", api_key="k", agent_id="agent_1",
                phone_number_id="", allowed_outbound=False, bridge_url="", bridge_token="",
                contacts={}, allowlist=(), bridge_sync_limit=25,
            ),
            store=mock.Mock(),
        )
        captured: dict = {}

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(AGENT_TWIML).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["key"] = request.headers.get("Xi-api-key")
            return _Response()

        with mock.patch("butler.phone.elevenlabs_twilio.urllib.request.urlopen", side_effect=fake_urlopen):
            twiml = provider.register_inbound_call(
                from_number=OWNER, to_number=TWILIO_NUMBER, dynamic_variables={"talos_call_token": "t"}
            )
        self.assertEqual(twiml, AGENT_TWIML)
        self.assertEqual(captured["url"], "https://api.elevenlabs.io/v1/convai/twilio/register-call")
        self.assertEqual(captured["key"], "k")
        self.assertEqual(
            captured["body"],
            {
                "agent_id": "agent_1",
                "from_number": OWNER,
                "to_number": TWILIO_NUMBER,
                "direction": "inbound",
                "conversation_initiation_client_data": {"dynamic_variables": {"talos_call_token": "t"}},
            },
        )


if __name__ == "__main__":
    unittest.main()
