from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.phone import (
    PhoneCallStore,
    ingest_phone_bridge_snapshot,
    phone_call_status,
    place_phone_call,
    reset_default_phone_provider,
    reset_default_phone_store,
    summarize_phone_call,
)
from talos.phone.elevenlabs_twilio import ElevenLabsTwilioProvider
from talos.phone.provider import OutboundCallRequest, PhoneConfig, PhoneProvider
from talos.phone.service import _build_call_transcript_digest
from talos.phone.store import PhoneCallRecord


class _FakeProvider(PhoneProvider):
    def __init__(self) -> None:
        self.requests: list[OutboundCallRequest] = []

    def start_outbound_call(self, request: OutboundCallRequest):
        self.requests.append(request)
        with tempfile.TemporaryDirectory():
            raise AssertionError("This fake should be patched at the service layer.")

    def get_call(self, call_id: str):
        return None

    def list_recent_calls(self, *, limit: int = 25):
        return []

    def ingest_call_event(self, payload: dict[str, object]):
        raise NotImplementedError

    def fetch_transcript(self, call_id: str):
        return None


class _UrlOpenResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_UrlOpenResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class PhoneServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_default_phone_provider()
        reset_default_phone_store()

    def test_place_phone_call_rejects_background_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {
                "TALOS_PHONE_ENABLED": "1",
                "TALOS_PHONE_ALLOWED_OUTBOUND": "1",
                "TALOS_PHONE_DB_PATH": str(Path(tmpdir) / "phone.sqlite3"),
                "TALOS_PHONE_CONTACTS": json.dumps({"mom": "+15555550123"}),
                "TALOS_PHONE_ALLOWLIST": json.dumps(["+15555550123"]),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "active foreground user session"):
                place_phone_call("mom", session_id="main-pc", runtime_lane="background")

    def test_place_phone_call_rejects_non_allowlisted_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {
                "TALOS_PHONE_ENABLED": "1",
                "TALOS_PHONE_ALLOWED_OUTBOUND": "1",
                "TALOS_PHONE_DB_PATH": str(Path(tmpdir) / "phone.sqlite3"),
                "TALOS_PHONE_ALLOWLIST": json.dumps(["+15555550123"]),
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "not in TALOS_PHONE_ALLOWLIST"):
                place_phone_call("+15555550999", session_id="main-pc")

    def test_place_phone_call_uses_configured_contact_and_returns_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {
                "TALOS_PHONE_ENABLED": "1",
                "TALOS_PHONE_ALLOWED_OUTBOUND": "1",
                "TALOS_PHONE_DB_PATH": str(Path(tmpdir) / "phone.sqlite3"),
                "TALOS_PHONE_CONTACTS": json.dumps({"mom": "+15555550123"}),
                "TALOS_PHONE_ALLOWLIST": json.dumps(["+15555550123"]),
            },
            clear=False,
        ):
            store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
            config = PhoneConfig.from_env()
            fake_provider = ElevenLabsTwilioProvider(config, store=store)
            with mock.patch.object(
                fake_provider,
                "start_outbound_call",
                return_value=store.create_call(
                    call_id="conv_123",
                    provider="elevenlabs_twilio",
                    provider_call_id="CA123",
                    conversation_id="conv_123",
                    agent_id="agent_123",
                    session_id="main-pc",
                    direction="outbound",
                    remote_number="+15555550123",
                    contact_name="mom",
                    purpose="Pickup",
                    brief_context="At the train station.",
                    status="initiated",
                ),
            ) as start_mock, mock.patch("talos.phone.service._build_provider", return_value=fake_provider):
                result = place_phone_call(
                    "mom",
                    purpose="Pickup",
                    brief_context="At the train station.",
                    message_to_deliver="Tell her I am arriving at 6:20 PM and need a pickup.",
                    session_id="main-pc",
                )

        self.assertTrue(result["success"])
        self.assertEqual(result["call"]["remote_number"], "+15555550123")
        start_mock.assert_called_once()
        outbound_request = start_mock.call_args.args[0]
        self.assertEqual(outbound_request.to_number, "+15555550123")
        self.assertEqual(outbound_request.session_id, "main-pc")
        self.assertEqual(
            outbound_request.message_to_deliver,
            "Tell her I am arriving at 6:20 PM and need a pickup.",
        )
        self.assertIn("identify yourself as TALOS", outbound_request.brief_context)
        self.assertIn("Exact message to deliver", outbound_request.brief_context)

    def test_phone_call_status_refreshes_from_bridge_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {
                "TALOS_PHONE_PROVIDER": "elevenlabs_twilio",
                "TALOS_PHONE_BRIDGE_URL": "https://bridge.example.com",
                "TALOS_PHONE_BRIDGE_TOKEN": "bridge-token",
                "TALOS_PHONE_DB_PATH": str(Path(tmpdir) / "phone.sqlite3"),
            },
            clear=False,
        ):
            store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
            config = PhoneConfig.from_env()
            provider = ElevenLabsTwilioProvider(config, store=store)
            store.create_call(
                call_id="conv_123",
                provider="elevenlabs_twilio",
                provider_call_id="CA123",
                conversation_id="conv_123",
                agent_id="agent_123",
                session_id="main-pc",
                direction="outbound",
                remote_number="+15555550123",
                contact_name="Mom",
                purpose="Pickup",
                brief_context="At the station.",
                status="initiated",
            )
            bridge_payload = {
                "calls": [
                    {
                        "call_id": "conv_123",
                        "provider": "elevenlabs_twilio",
                        "provider_call_id": "CA123",
                        "conversation_id": "conv_123",
                        "agent_id": "agent_123",
                        "session_id": "main-pc",
                        "direction": "outbound",
                        "remote_number": "+15555550123",
                        "contact_name": "Mom",
                        "purpose": "Pickup",
                        "brief_context": "At the station.",
                        "status": "completed",
                        "outcome": "completed",
                        "transcript": [{"role": "user", "message": "Can you pick me up?"}],
                    }
                ]
            }
            with mock.patch("talos.phone.service._build_provider", return_value=provider), mock.patch(
                "urllib.request.urlopen",
                return_value=_UrlOpenResponse(bridge_payload),
            ):
                result = phone_call_status("conv_123", refresh=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["call"]["status"], "completed")
        self.assertIn("Can you pick me up?", json.dumps(result["call"]["transcript"]))
        self.assertIn("Caller said", result["call"]["summary"])

    def test_phone_call_status_refreshes_directly_from_provider_when_no_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {
                "TALOS_PHONE_PROVIDER": "elevenlabs_twilio",
                "TALOS_PHONE_DB_PATH": str(Path(tmpdir) / "phone.sqlite3"),
            },
            clear=False,
        ):
            store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
            config = PhoneConfig.from_env()
            provider = ElevenLabsTwilioProvider(config, store=store)
            store.create_call(
                call_id="conv_789",
                provider="elevenlabs_twilio",
                provider_call_id="CA789",
                conversation_id="conv_789",
                agent_id="agent_123",
                session_id="main-pc",
                direction="outbound",
                remote_number="+15555550123",
                contact_name="Mom",
                purpose="Weather report",
                brief_context="Deliver a weather update.",
                status="initiated",
            )
            with mock.patch("talos.phone.service._build_provider", return_value=provider), mock.patch.object(
                provider,
                "fetch_call_details",
                return_value=store.update_call(
                    "conv_789",
                    status="completed",
                    outcome="caller_hung_up",
                    transcript=[{"role": "assistant", "message": "It is 78 degrees and cloudy."}],
                ),
            ) as refresh_mock:
                result = phone_call_status("conv_789", refresh=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["call"]["status"], "completed")
        self.assertEqual(result["call"]["outcome"], "caller_hung_up")
        refresh_mock.assert_called_once_with("conv_789")

    def test_summarize_phone_call_uses_bridge_ingested_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {
                "TALOS_PHONE_PROVIDER": "elevenlabs_twilio",
                "TALOS_PHONE_DB_PATH": str(Path(tmpdir) / "phone.sqlite3"),
            },
            clear=False,
        ):
            store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
            config = PhoneConfig.from_env()
            provider = ElevenLabsTwilioProvider(config, store=store)
            with mock.patch("talos.phone.service._build_provider", return_value=provider):
                ingest_phone_bridge_snapshot(
                    {
                        "call_id": "conv_456",
                        "provider": "elevenlabs_twilio",
                        "provider_call_id": "CA456",
                        "conversation_id": "conv_456",
                        "direction": "inbound",
                        "remote_number": "+15555550199",
                        "status": "completed",
                        "outcome": "completed",
                        "transcript": [{"role": "user", "message": "Please call me back tomorrow."}],
                    }
                )
                result = summarize_phone_call("conv_456", refresh=False)

        self.assertTrue(result["success"])
        self.assertIn("Inbound call", result["summary"])
        self.assertIn("call me back tomorrow", result["summary"])


def _make_call_record(transcript: list[dict[str, object]]) -> PhoneCallRecord:
    return PhoneCallRecord(
        call_id="conv_digest",
        provider="elevenlabs_twilio",
        provider_call_id="CA_digest",
        conversation_id="conv_digest",
        agent_id="agent_123",
        session_id="main-pc",
        direction="outbound",
        remote_number="+15555550123",
        contact_name="Mom",
        purpose="Pickup",
        brief_context=None,
        status="completed",
        outcome="completed",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        transcript=transcript,
    )


class TranscriptDigestTests(unittest.TestCase):
    def test_labels_and_orders_turns_by_role(self) -> None:
        record = _make_call_record(
            [
                {"role": "user", "message": "Can you pick me up?"},
                {"role": "assistant", "message": "Sure, what time?"},
                {"role": "user", "message": "Six PM."},
            ]
        )
        digest = _build_call_transcript_digest(record)
        self.assertEqual(
            digest,
            "Caller: Can you pick me up?\nTALOS: Sure, what time?\nCaller: Six PM.",
        )

    def test_empty_transcript_returns_empty_string(self) -> None:
        record = _make_call_record([])
        self.assertEqual(_build_call_transcript_digest(record), "")

    def test_skips_turns_without_message_text(self) -> None:
        record = _make_call_record([{"role": "user", "message": ""}, {"role": "agent", "message": "Hello."}])
        self.assertEqual(_build_call_transcript_digest(record), "TALOS: Hello.")

    def test_truncates_oversized_transcript_keeping_head_and_tail(self) -> None:
        turns = [{"role": "user", "message": f"Message number {i} with some extra padding text."} for i in range(200)]
        record = _make_call_record(turns)
        digest = _build_call_transcript_digest(record, max_chars=500)
        self.assertLessEqual(len(digest), 500)
        self.assertIn("Message number 0", digest)
        self.assertIn("...[truncated]...", digest)
        self.assertIn("Message number 199", digest)


class PhoneCallMemoryTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_default_phone_provider()
        reset_default_phone_store()

    def test_ingest_writes_transcript_fact_findable_by_search(self) -> None:
        from talos.memory import MemoryStore

        tmpdir = tempfile.mkdtemp()
        try:
            with mock.patch.dict(
                os.environ,
                {
                    "TALOS_PHONE_PROVIDER": "elevenlabs_twilio",
                    "TALOS_PHONE_DB_PATH": str(Path(tmpdir) / "phone.sqlite3"),
                    "TALOS_MEMORY_ENABLED": "1",
                },
                clear=False,
            ):
                store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
                config = PhoneConfig.from_env()
                provider = ElevenLabsTwilioProvider(config, store=store)
                memory_store = MemoryStore(db_path=":memory:")

                snapshot = {
                    "call_id": "conv_mem",
                    "provider": "elevenlabs_twilio",
                    "conversation_id": "conv_mem",
                    "session_id": "main-pc",
                    "direction": "outbound",
                    "remote_number": "+15555550123",
                    "status": "completed",
                    "outcome": "completed",
                    "transcript": [
                        {"role": "user", "message": "The pharmacy said the prescription is ready tomorrow."},
                    ],
                }
                try:
                    with mock.patch("talos.phone.service._build_provider", return_value=provider), mock.patch(
                        "talos.phone.service.get_default_memory_store", return_value=memory_store
                    ):
                        ingest_phone_bridge_snapshot(snapshot)
                        facts_first = memory_store.search_facts("pharmacy")

                        # Re-ingesting an identical snapshot should not create a duplicate fact write.
                        ingest_phone_bridge_snapshot(snapshot)
                        facts_second = memory_store.search_facts("pharmacy")

                    self.assertEqual(len(facts_first), 1)
                    self.assertIn("pharmacy", facts_first[0].value.lower())
                    self.assertEqual(facts_first[0].key, "phone_call_transcript:conv_mem")
                    self.assertEqual(facts_first[0].scope, "session:main-pc")
                    self.assertEqual(len(facts_second), 1)
                    self.assertEqual(facts_first[0].updated_at, facts_second[0].updated_at)
                finally:
                    store.close()
                    memory_store.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
