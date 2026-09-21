from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    @staticmethod
    def _exchange():
        return [
            {"role": "assistant", "content": "Checking", "tool_calls": [
                {"id": "call-1", "type": "function", "function": {
                    "name": "get_current_state", "arguments": '{"entity_id":"pump"}',
                }},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "content": '{"status":"offline"}'},
        ]

    def test_tool_exchange_and_used_schema_survive_restart(self):
        schema = {"type": "function", "name": "get_current_state", "parameters": {
            "type": "object", "properties": {"entity_id": {"type": "string"}},
        }}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.sqlite3"
            store = MemoryStore(path)
            store.record_turn("voice", "Check pump", "It is offline.",
                              tool_messages=self._exchange(),
                              tool_schemas=[schema, {"name": "unused"}])
            store.close()
            store = MemoryStore(path)
            self.addCleanup(store.close)
            history = store.get_recent_messages("voice")
            self.assertEqual([m["role"] for m in history], ["user", "assistant", "tool", "assistant"])
            self.assertEqual(history[1]["tool_calls"][0]["function"]["arguments"], '{"entity_id":"pump"}')
            self.assertEqual(history[2], self._exchange()[1])
            row = store._conn.execute("SELECT metadata_json FROM messages WHERE role='assistant'").fetchone()
            self.assertEqual(json.loads(row[0])["tool_history"]["schemas"], [schema])
            self.assertEqual(history[1]["content"], "")

    def test_tool_turn_is_dropped_whole_under_either_budget(self):
        store = MemoryStore(":memory:")
        self.addCleanup(store.close)
        store.record_turn("voice", "Check pump", "Offline", tool_messages=self._exchange())
        self.assertEqual(store.get_recent_messages("voice", message_limit=3), [])
        self.assertEqual(store.get_recent_messages("voice", max_chars=50), [])
        store.record_turn("voice", "Thanks", "Understood")
        self.assertEqual(store.get_recent_messages("voice", message_limit=4), [
            {"role": "user", "content": "Thanks"}, {"role": "assistant", "content": "Understood"},
        ])

    def test_rejects_incomplete_history_without_writing_a_partial_turn(self):
        store = MemoryStore(":memory:")
        self.addCleanup(store.close)
        for messages in (self._exchange()[:1], self._exchange()[1:]):
            with self.assertRaises(ValueError):
                store.record_turn("voice", "Check", "Done", tool_messages=messages)
        self.assertEqual(store.get_recent_messages("voice"), [])

    def test_interruption_amends_dialogue_but_preserves_tool_evidence(self):
        store = MemoryStore(":memory:")
        self.addCleanup(store.close)
        store.record_turn("voice", "Check pump", "Offline. Here is more.", tool_messages=self._exchange())
        store.amend_last_assistant_message("voice", "Offline. [interrupted]")
        history = store.get_recent_messages("voice")
        self.assertEqual(history[2], self._exchange()[1])
        self.assertEqual(history[-1]["content"], "Offline. [interrupted]")
        self.assertNotIn("Here is more", str(history))

    def test_multiple_calls_and_rounds_keep_results_paired(self):
        store = MemoryStore(":memory:")
        self.addCleanup(store.close)
        first = self._exchange()
        second = self._exchange()
        second[0]["tool_calls"][0]["id"] = "call-2"
        second[1]["tool_call_id"] = "call-2"
        second[1]["content"] = '{"error":"unavailable"}'
        store.record_turn("voice", "Check both", "One check failed", tool_messages=first + second)
        history = store.get_recent_messages("voice")
        self.assertEqual([m["tool_call_id"] for m in history if m["role"] == "tool"], ["call-1", "call-2"])
        self.assertIn('"error":"unavailable"', history[-2]["content"])

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

    def test_recent_messages_are_bounded_chat_history(self) -> None:
        store = MemoryStore(":memory:")
        store.record_turn("voice", "Water the plants.", "Which pot, one or two?")
        store.record_turn("voice", "Go with both.", "Done.")

        messages = store.get_recent_messages("voice", message_limit=3, max_chars=1000)
        store.close()

        self.assertEqual(
            messages,
            [
                {"role": "assistant", "content": "Which pot, one or two?"},
                {"role": "user", "content": "Go with both."},
                {"role": "assistant", "content": "Done."},
            ],
        )


if __name__ == "__main__":
    unittest.main()
