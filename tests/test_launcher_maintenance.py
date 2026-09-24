from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from butler.launcher import maintenance
from butler.memory.store import MemoryStore


class ClearChatHistoryTests(unittest.TestCase):
    def test_clears_every_session_but_preserves_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.sqlite3"
            store = MemoryStore(db_path)
            store.record_turn("voice-worker", "turn off the desk lamp.", "Desk lamp off.")
            store.record_turn("main-pc", "hi", "hello")
            store.upsert_fact("user", "name", "Al", source_session_id="main-pc")
            store.close()

            with mock.patch.object(maintenance, "conversation_db_path", return_value=db_path):
                summary = maintenance.clear_chat_history()

            self.assertIn("voice-worker", summary)
            self.assertIn("main-pc", summary)

            check = MemoryStore(db_path)
            remaining_sessions = check.list_session_ids()
            memory = check.get_prompt_memory("main-pc", "name", max_chars=2000)
            check.close()

        self.assertEqual(remaining_sessions, [])
        self.assertIn("name", memory)

    def test_missing_db_file_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "does-not-exist.sqlite3"
            with mock.patch.object(maintenance, "conversation_db_path", return_value=missing):
                summary = maintenance.clear_chat_history()

        self.assertIn("no chat history", summary)

    def test_in_memory_store_is_a_no_op(self) -> None:
        with mock.patch.object(maintenance, "conversation_db_path", return_value=Path(":memory:")):
            summary = maintenance.clear_chat_history()

        self.assertIn("in-memory", summary)

    def test_does_not_require_butler_to_be_stopped(self) -> None:
        # Unlike clear_conversation_memory, this deletes rows through a second
        # connection rather than unlinking the file, so a second, still-open
        # MemoryStore connection to the same path must not block it.
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.sqlite3"
            held_open = MemoryStore(db_path)
            held_open.record_turn("voice-worker", "hi", "hello")
            try:
                with mock.patch.object(maintenance, "conversation_db_path", return_value=db_path):
                    summary = maintenance.clear_chat_history()
            finally:
                held_open.close()

        self.assertIn("voice-worker", summary)


if __name__ == "__main__":
    unittest.main()
