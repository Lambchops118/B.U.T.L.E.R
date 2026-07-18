from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_persists_facts_summaries_and_session_turns_across_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.sqlite3"

            store = MemoryStore(db_path)
            store.upsert_summary("user", "default", "User prefers compact engineering updates.")
            store.upsert_summary("project", "Talos", "TALOS uses MCP tools for grounded actions.")
            store.upsert_fact(
                "user",
                "response_style",
                "Prefers concise answers with exact file references.",
                salience=9,
            )
            store.record_turn("session-a", "Remember this detail.", "Stored, sir.")
            store.close()

            reopened = MemoryStore(db_path)
            memory = reopened.get_prompt_memory(
                "session-a",
                "How should you answer code questions?",
                max_chars=2000,
            )
            reopened.close()

        self.assertIn("compact engineering updates", memory)
        self.assertIn("TALOS uses MCP tools", memory)
        self.assertIn("response_style", memory)
        self.assertIn("Recent session turns", memory)
        self.assertIn("Remember this detail.", memory)

    def test_query_returns_relevant_fact_before_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir) / "memory.sqlite3")
            store.upsert_fact("user", "color", "Likes cobalt blue.", salience=8)
            store.upsert_fact("project", "kicad", "KiCad work should verify IPC state.", salience=5)

            facts = store.search_facts("What should I do for KiCad placement?", limit=1)
            store.close()

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].key, "kicad")

    def test_clear_session_removes_turns_but_preserves_explicit_facts(self) -> None:
        store = MemoryStore(":memory:")
        store.record_turn("voice", "My code is cobalt.", "Understood.")
        store.upsert_fact(
            "session:voice",
            "preferred_code",
            "cobalt",
            source_session_id="voice",
        )

        store.clear_session("voice")
        memory = store.get_prompt_memory("voice", "preferred code", max_chars=2000)
        store.close()

        self.assertNotIn("My code is cobalt", memory)
        self.assertIn("preferred_code", memory)


if __name__ == "__main__":
    unittest.main()
