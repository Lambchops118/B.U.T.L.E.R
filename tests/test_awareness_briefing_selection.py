"""Bounded selection and preference guards, without Ollama or a database."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

try:
    from talos.awareness.briefing.selection import OllamaSelector, PROMPT_VERSION, select
    from talos.awareness.briefing.feedback import BriefingFeedback, apply_preferences
    from talos.awareness.config import AwarenessSettings
except ImportError as exc:
    raise unittest.SkipTest(f"awareness dependencies unavailable: {exc}")


def candidate(item_id, priority=4, category="agent_outcome"):
    return {"item_id": item_id, "priority": priority, "category": category,
            "text": f"Stored fact {item_id}", "relevance": 0, "evidence": {}}


def config(**kwargs):
    return AwarenessSettings(_env_file=None, db_password="test", chat_model="local-test",
                             briefing_model_enabled=True, **kwargs)


class SelectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_id_rejected_with_truthful_fallback(self):
        model = AsyncMock(return_value={"chosen": [{"item_id": "invented", "reason": "made up"}]})
        chosen, audit = await select([candidate("fact")], config(), model=model)
        self.assertEqual([c["item_id"] for c in chosen], ["fact"])
        self.assertEqual(audit["selection_mode"], "deterministic_fallback")
        self.assertIn("ValueError", audit["reason"])
        self.assertEqual(audit["prompt_version"], PROMPT_VERSION)
        self.assertEqual(audit["candidates_offered"], ["fact"])

    async def test_critical_omission_overridden_and_overflow_batched(self):
        model = AsyncMock(return_value={"chosen": [{"item_id": "optional", "reason": "interesting"}]})
        chosen, audit = await select([candidate("critical1", 1), candidate("critical2", 1),
                                      candidate("optional")], config(briefing_max_items=1), model=model)
        self.assertEqual([c["item_id"] for c in chosen], ["critical1", "critical2"])
        self.assertEqual(audit["delivery_batches"], 2)
        self.assertEqual(audit["critical_overrides"], ["critical1", "critical2"])
        self.assertTrue(audit["truncated"])

    async def test_cap_after_ranking_and_no_model_prose_used(self):
        model = AsyncMock(return_value={"chosen": [{"item_id": i, "reason": "selected"} for i in ("b", "a")]})
        chosen, audit = await select([candidate("a"), candidate("b")], config(briefing_max_items=1), model=model)
        self.assertEqual(chosen[0]["text"], "Stored fact b")
        self.assertEqual(audit["chosen"], ["b"])
        self.assertEqual(audit["selection_mode"], "model_selection")
        self.assertTrue(audit["truncated"])

    async def test_timeout_falls_back(self):
        async def slow(prompt):
            await asyncio.sleep(1)
        chosen, audit = await select([candidate("low", 6), candidate("high", 2)],
            config(briefing_model_timeout_seconds=0.01), model=slow)
        self.assertEqual([c["item_id"] for c in chosen], ["high", "low"])
        self.assertEqual(audit["reason"], "model_unavailable_or_invalid:TimeoutError")

    async def test_empty_model_ranking_is_silence(self):
        chosen, audit = await select([candidate("a")], config(), model=AsyncMock(return_value={"chosen": []}))
        self.assertEqual(chosen, [])
        self.assertEqual(audit["selection_mode"], "model_selection")

    async def test_disabled_model_never_called(self):
        settings = config().model_copy(update={"briefing_model_enabled": False})
        model = AsyncMock()
        chosen, audit = await select([candidate("a")], settings, model=model)
        model.assert_not_called()
        self.assertEqual(audit["reason"], "model_disabled")

    async def test_duplicate_ids_and_extra_phrasing_rejected(self):
        choice = {"item_id": "a", "reason": "ok"}
        for response in ({"chosen": [choice, choice]}, {"chosen": [choice], "phrasing": "invented fact"}):
            _, audit = await select([candidate("a")], config(), model=AsyncMock(return_value=response))
            self.assertEqual(audit["selection_mode"], "deterministic_fallback")

    async def test_prompt_bound_and_remote_ollama_rejected_before_network(self):
        model = AsyncMock(return_value={"chosen": []})
        items = [dict(candidate(str(i)), text="x"*1000) for i in range(10)]
        _, audit = await select(items, config(briefing_prompt_max_chars=2000), model=model)
        self.assertLessEqual(len(model.call_args.args[0]), 2000)
        self.assertTrue(audit["prompt_truncated"])
        with patch("talos.awareness.briefing.selection.httpx.AsyncClient") as client:
            with self.assertRaises(ValueError):
                await OllamaSelector(config(ollama_host="https://example.com"))("private")
            client.assert_not_called()

    async def test_dismissal_precedes_selection_but_never_suppresses_critical(self):
        kept, audit = apply_preferences([candidate("normal"), candidate("critical", 1)],
                                        {"class:agent_outcome": "dismiss"})
        self.assertEqual([c["item_id"] for c in kept], ["critical"])
        self.assertFalse(audit[0]["included"])

    async def test_interest_never_crosses_priority_band(self):
        kept, _ = apply_preferences([candidate("higher", 2, "alert"), candidate("interest", 6)],
                                    {"class:agent_outcome": "interest"})
        chosen, _ = await select(kept, config().model_copy(update={"briefing_model_enabled": False}))
        self.assertEqual([c["item_id"] for c in chosen], ["higher", "interest"])

    async def test_feedback_requires_one_bounded_target(self):
        for body in ({"value": "dismiss"}, {"category": "alert", "item_id": "x", "value": "dismiss"},
                     {"category": "unknown", "value": "dismiss"}, {"item_id": "x"*2001, "value": "dismiss"}):
            with self.assertRaises(ValueError):
                BriefingFeedback(**body)

    async def test_unreachable_ollama_endpoint_uses_fallback(self):
        import socket
        # Reserve a loopback port without listening: no running service is stopped.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            settings = config(ollama_host=f"http://127.0.0.1:{sock.getsockname()[1]}",
                              briefing_model_timeout_seconds=0.5)
            chosen, audit = await select([candidate("stored")], settings)
        self.assertEqual([c["item_id"] for c in chosen], ["stored"])
        self.assertEqual(audit["selection_mode"], "deterministic_fallback")
        self.assertTrue(audit["reason"].startswith("model_unavailable_or_invalid:"))
