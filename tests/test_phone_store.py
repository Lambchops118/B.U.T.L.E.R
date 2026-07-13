from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.phone import PhoneCallStore


class PhoneCallStoreTests(unittest.TestCase):
    def test_persists_calls_and_events_across_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "phone.sqlite3"
            store = PhoneCallStore(db_path)
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
                purpose="Schedule pickup",
                brief_context="Ask about train arrival time.",
                status="initiated",
                metadata={"source": "test"},
            )
            store.update_call(
                "conv_123",
                status="completed",
                outcome="completed",
                summary="Outbound call with Mom. Status: completed.",
                transcript=[{"role": "user", "message": "Can you pick me up?"}],
            )
            store.add_event(
                call_id="conv_123",
                event_type="post_call_transcription",
                message="Transcript synced.",
                payload={"ok": True},
            )
            store.close()

            reopened = PhoneCallStore(db_path)
            record = reopened.get_call("conv_123")
            events = reopened.list_call_events("conv_123")
            reopened.close()

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.provider_call_id, "CA123")
        self.assertEqual(record.contact_name, "Mom")
        self.assertEqual(record.transcript[0]["message"], "Can you pick me up?")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "post_call_transcription")


if __name__ == "__main__":
    unittest.main()
