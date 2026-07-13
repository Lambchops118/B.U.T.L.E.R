from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.phone.elevenlabs_twilio import ElevenLabsTwilioProvider
from talos.phone.provider import OutboundCallRequest, PhoneConfig
from talos.phone.store import PhoneCallStore


class ElevenLabsTwilioProviderTests(unittest.TestCase):
    def test_outbound_call_uses_documented_personalization_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
            provider = ElevenLabsTwilioProvider(
                PhoneConfig(
                    enabled=True,
                    provider_name="elevenlabs_twilio",
                    api_key="test-key",
                    agent_id="agent_123",
                    phone_number_id="phnum_123",
                    allowed_outbound=True,
                    bridge_url="",
                    bridge_token="",
                    contacts={},
                    allowlist=(),
                    bridge_sync_limit=25,
                    db_path=str(Path(tmpdir) / "phone.sqlite3"),
                ),
                store=store,
            )
            captured_payload: dict[str, object] = {}

            def _fake_request_json(
                url: str,
                *,
                payload: dict[str, object] | None = None,
                method: str = "POST",
            ) -> dict[str, object]:
                self.assertEqual(url, "https://api.elevenlabs.io/v1/convai/twilio/outbound-call")
                self.assertEqual(method, "POST")
                assert payload is not None
                captured_payload.update(payload)
                return {"conversation_id": "conv_123", "callSid": "CA123"}

            with mock.patch.object(provider, "_request_json", side_effect=_fake_request_json):
                record = provider.start_outbound_call(
                    OutboundCallRequest(
                        session_id="main-pc",
                        to_number="+15555550123",
                        purpose="Report the latest weather update",
                        brief_context=(
                            "You are TALOS, the user's personal AI assistant, placing an outbound phone call.\n"
                            "Deliver the requested report directly and concisely."
                        ),
                        contact_name="Thomas",
                        message_to_deliver="It is currently 76 degrees and clear in Ellicott City.",
                        caller_identity="TALOS",
                    )
                )

        self.assertEqual(record.call_id, "conv_123")
        self.assertEqual(record.provider_call_id, "CA123")
        self.assertEqual(captured_payload["agent_id"], "agent_123")
        self.assertEqual(captured_payload["agent_phone_number_id"], "phnum_123")
        self.assertEqual(captured_payload["to_number"], "+15555550123")

        initiation_data = captured_payload["conversation_initiation_client_data"]
        self.assertIsInstance(initiation_data, dict)
        assert isinstance(initiation_data, dict)
        self.assertNotIn("type", initiation_data)

        dynamic_variables = initiation_data["dynamic_variables"]
        self.assertIsInstance(dynamic_variables, dict)
        assert isinstance(dynamic_variables, dict)
        self.assertEqual(dynamic_variables["talos_session_id"], "main-pc")
        self.assertEqual(dynamic_variables["contact_name"], "Thomas")
        self.assertEqual(dynamic_variables["target_phone_number"], "+15555550123")
        self.assertEqual(dynamic_variables["purpose"], "Report the latest weather update")
        self.assertEqual(
            dynamic_variables["message_to_deliver"],
            "It is currently 76 degrees and clear in Ellicott City.",
        )
        self.assertEqual(dynamic_variables["caller_identity"], "TALOS")

        overrides = initiation_data["conversation_config_override"]
        self.assertIsInstance(overrides, dict)
        assert isinstance(overrides, dict)
        agent_override = overrides["agent"]
        self.assertIsInstance(agent_override, dict)
        assert isinstance(agent_override, dict)
        self.assertEqual(
            agent_override["first_message"],
            "Hello Thomas, this is TALOS. I'm calling with a quick message: "
            "It is currently 76 degrees and clear in Ellicott City.",
        )
        prompt_override = agent_override["prompt"]
        self.assertIsInstance(prompt_override, dict)
        assert isinstance(prompt_override, dict)
        self.assertIn("This is an outbound call that you initiated", prompt_override["prompt"])
        self.assertIn("Deliver this exact message verbatim", prompt_override["prompt"])
        self.assertIn("Intended recipient name: Thomas", prompt_override["prompt"])

    def test_outbound_call_first_message_falls_back_to_purpose(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
            provider = ElevenLabsTwilioProvider(
                PhoneConfig(
                    enabled=True,
                    provider_name="elevenlabs_twilio",
                    api_key="test-key",
                    agent_id="agent_123",
                    phone_number_id="phnum_123",
                    allowed_outbound=True,
                    bridge_url="",
                    bridge_token="",
                    contacts={},
                    allowlist=(),
                    bridge_sync_limit=25,
                    db_path=str(Path(tmpdir) / "phone.sqlite3"),
                ),
                store=store,
            )
            with mock.patch.object(
                provider,
                "_request_json",
                return_value={"conversation_id": "conv_456", "callSid": "CA456"},
            ) as request_mock:
                provider.start_outbound_call(
                    OutboundCallRequest(
                        session_id="main-pc",
                        to_number="+15555550124",
                        purpose="a pickup coordination update",
                        brief_context="Coordinate a pickup update.",
                        contact_name="Mom",
                    )
                )

        payload = request_mock.call_args.kwargs["payload"]
        initiation_data = payload["conversation_initiation_client_data"]
        self.assertEqual(
            initiation_data["conversation_config_override"]["agent"]["first_message"],
            "Hello Mom, this is TALOS. I'm calling regarding a pickup coordination update.",
        )

    def test_fetch_call_details_refreshes_from_elevenlabs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = PhoneCallStore(Path(tmpdir) / "phone.sqlite3")
            provider = ElevenLabsTwilioProvider(
                PhoneConfig(
                    enabled=True,
                    provider_name="elevenlabs_twilio",
                    api_key="test-key",
                    agent_id="agent_123",
                    phone_number_id="phnum_123",
                    allowed_outbound=True,
                    bridge_url="",
                    bridge_token="",
                    contacts={},
                    allowlist=(),
                    bridge_sync_limit=25,
                    db_path=str(Path(tmpdir) / "phone.sqlite3"),
                ),
                store=store,
            )
            store.create_call(
                call_id="conv_999",
                provider="elevenlabs_twilio",
                provider_call_id="CA999",
                conversation_id="conv_999",
                agent_id="agent_123",
                session_id="main-pc",
                direction="outbound",
                remote_number="+15555550123",
                contact_name="Thomas",
                purpose="Weather report",
                brief_context="Deliver a weather update.",
                status="initiated",
            )
            with mock.patch.object(
                provider,
                "_request_json",
                return_value={
                    "conversation_id": "conv_999",
                    "agent_id": "agent_123",
                    "status": "done",
                    "transcript": [{"role": "assistant", "message": "It is 76 degrees."}],
                    "metadata": {
                        "termination_reason": "caller_hung_up",
                        "accepted_time_unix_secs": 1782685338,
                        "phone_call": {
                            "direction": "outbound",
                            "external_number": "+15555550123",
                            "call_sid": "CA999",
                        },
                    },
                    "conversation_initiation_client_data": {
                        "dynamic_variables": {
                            "talos_session_id": "main-pc",
                            "contact_name": "Thomas",
                            "purpose": "Weather report",
                            "brief_context": "Deliver a weather update.",
                        }
                    },
                },
            ) as request_mock:
                record = provider.fetch_call_details("conv_999")

        self.assertEqual(request_mock.call_args.kwargs["method"], "GET")
        self.assertEqual(record.call_id, "conv_999")
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.outcome, "caller_hung_up")
        self.assertEqual(record.transcript, [{"role": "assistant", "message": "It is 76 degrees."}])


if __name__ == "__main__":
    unittest.main()
