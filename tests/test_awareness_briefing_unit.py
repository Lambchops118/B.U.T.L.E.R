"""Phase 9A assembly contract and bounds without infrastructure."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

try:
    from talos.awareness.config import AwarenessSettings
    from talos.awareness.context.briefing import BriefingAssembler, BriefingAssemblyError, resolve_window
    from talos.awareness.history.briefing import BriefingRecords
    from talos.awareness.history.telemetry import QueryBoundsError
except ImportError as exc:
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def settings(**kwargs):
    return AwarenessSettings(_env_file=None, db_password="test-only", **kwargs)


def history():
    mock = AsyncMock()
    mock.last_delivery.return_value = None
    mock.unavailable.return_value = set()
    mock.preferences.return_value = {}
    for name in ("alerts", "attention", "transitions", "events", "novelty"):
        getattr(mock, name).return_value = BriefingRecords([], False, f"test.{name}")
    return mock


def event(number=1, event_type="agent.job.completed"):
    return Row(event_id=str(number), event_type=event_type, entity_id="talos",
               received_at=NOW-timedelta(minutes=1), observed_at=None,
               source_id="talos_agent", confidence=0.9, severity="info",
               provenance={}, payload={"text": "PRIVATE TRANSCRIPT MUST NOT RENDER"})


class BriefingWindowTest(unittest.TestCase):
    def test_first_run_is_explicit(self):
        window = resolve_window(settings(), NOW, None)
        self.assertEqual(window.start, NOW-timedelta(hours=24))
        self.assertEqual(window.origin, "configured_first_run_window")
        self.assertFalse(window.truncated)

    def test_delivery_drives_window(self):
        last = NOW-timedelta(hours=2)
        window = resolve_window(settings(), NOW, last)
        self.assertEqual(window.start, last)
        self.assertEqual(window.origin, "recorded_delivery")

    def test_old_delivery_is_bounded_and_audited(self):
        window = resolve_window(settings(max_query_range_days=2), NOW, NOW-timedelta(days=9))
        self.assertEqual(window.start, NOW-timedelta(days=2))
        self.assertTrue(window.truncated)

    def test_first_run_also_obeys_maximum_range(self):
        window = resolve_window(settings(max_query_range_days=1, briefing_default_window_hours=72), NOW, None)
        self.assertTrue(window.truncated)
        self.assertEqual(window.start, NOW-timedelta(days=1))

    def test_invalid_times_rejected(self):
        for end, last in ((NOW.replace(tzinfo=None), None), (NOW, NOW),
                          (NOW, NOW.replace(tzinfo=None))):
            with self.subTest(end=end, last=last), self.assertRaises(QueryBoundsError):
                resolve_window(settings(), end, last)


class BriefingAssemblyTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_window_has_no_filler(self):
        result = await BriefingAssembler(None, settings()).assemble(history(), "morning", NOW)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["audit"], [])
        self.assertFalse(result["truncated"])

    async def test_event_contract_and_no_payload_capture(self):
        repo = history()
        repo.events.return_value = BriefingRecords([event(), event(2, "person.interaction.ended")], False, "events.v1")
        result = await BriefingAssembler(None, settings()).assemble(repo, "morning", NOW)
        self.assertEqual({c["category"] for c in result["candidates"]}, {"agent_outcome", "interaction"})
        self.assertNotIn("PRIVATE TRANSCRIPT", str(result))
        for candidate in result["candidates"]:
            for key in ("item_id", "entity_id", "source_id", "timestamp", "query", "priority", "evidence"):
                self.assertIn(key, candidate)
            self.assertIn("recorded 2026-09-07", candidate["text"])
            self.assertEqual(candidate["query"], "events.v1")

    async def test_bounds_and_critical_priority(self):
        repo = history()
        repo.alerts.return_value = BriefingRecords([Row(alert_id="safety", severity="critical",
            entity_id="fan", last_updated_at=NOW-timedelta(minutes=2), title="Fault", status="open")], False, "alerts")
        repo.events.return_value = BriefingRecords([event()], True, "events")
        result = await BriefingAssembler(None, settings(briefing_max_candidates=1)).assemble(repo, "morning", NOW)
        self.assertEqual([c["item_id"] for c in result["candidates"]], ["alert:safety"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["audit"][-1]["reason"], "candidate_bound_exceeded")
        self.assertTrue(result["queries"][3]["truncated"])

    async def test_critical_query_overflow_fails_closed(self):
        repo = history()
        repo.alerts.return_value = BriefingRecords([Row(severity="critical")], True, "alerts")
        with self.assertRaises(BriefingAssemblyError):
            await BriefingAssembler(None, settings()).assemble(repo, "morning", NOW)

    async def test_all_queries_obey_configured_limits(self):
        repo = history()
        config = settings(max_query_points=2, max_event_page_size=3,
                          briefing_max_candidates=5, max_query_range_days=1)
        result = await BriefingAssembler(None, config).assemble(repo, "arrival", NOW)
        for query in result["queries"]:
            self.assertEqual(query["limit"], 2)
        self.assertEqual(repo.novelty.call_args.args[-1], 2)
        self.assertEqual(repo.novelty.call_args.args[2], NOW-timedelta(days=1))

    async def test_missing_constant_nonfinite_and_ordinary_scores_do_not_invent_novelty(self):
        repo = history()
        repo.novelty.return_value = BriefingRecords(
            [Row(novelty_score=v) for v in (None, float("nan"), float("inf"), 1.0)], False, "novelty")
        result = await BriefingAssembler(None, settings()).assemble(repo, "morning", NOW)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["queries"][-1]["unscored_missing_constant_or_bounded_baseline"], 3)

    async def test_query_failure_does_not_return_partial_result(self):
        repo = history()
        repo.events.return_value = BriefingRecords([event()], False, "events")
        repo.novelty.side_effect = RuntimeError("database unavailable")
        with self.assertRaises(RuntimeError):
            await BriefingAssembler(None, settings()).assemble(repo, "morning", NOW)

    async def test_kind_validated_before_database_access(self):
        assembler = BriefingAssembler(None, settings())
        for kind in ("", "Morning", "a"*51, "morning; DROP TABLE events"):
            with self.subTest(kind=kind), self.assertRaises(QueryBoundsError):
                await assembler.build(kind, now=NOW)

    async def test_critical_event_is_not_demoted_to_routine_outcome(self):
        repo = history()
        record = event()
        record.severity = "critical"
        repo.events.return_value = BriefingRecords([record], False, "events")
        result = await BriefingAssembler(None, settings()).assemble(repo, "morning", NOW)
        self.assertEqual(result["candidates"][0]["priority"], 1)
