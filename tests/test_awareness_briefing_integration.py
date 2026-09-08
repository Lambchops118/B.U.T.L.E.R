"""Phase 9A PostgreSQL/TimescaleDB reads against a migrated scratch database.

Reuses the existing suite's scratch lifecycle; skips when local DB is absent.
No broker, notification endpoint, model, or production database is mutated.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone

try:
    from tests import test_awareness_state_integration as fixture
    import sqlalchemy as sa
    from talos.awareness.context.briefing import BriefingAssembler
    from talos.awareness.db.models import Alert, AttentionItem, Event, Measurement, NotificationDelivery, Reminder, StateTransition
    from talos.awareness.db.session import build_engine
    from talos.awareness.registry.bootstrap import seed_registry
except ImportError as exc:
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


class BriefingIntegrationTest(unittest.TestCase):
    setUp = fixture.StateTelemetryIntegrationTest.setUp
    tearDown = fixture.StateTelemetryIntegrationTest.tearDown
    _create_scratch_database = fixture.StateTelemetryIntegrationTest._create_scratch_database

    def test_stored_categories_novelty_delivery_windows_and_bounds(self):
        asyncio.run(self._flow())

    async def _flow(self):
        engine = build_engine(self.settings)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        moment = now-timedelta(hours=1)
        assembler = BriefingAssembler(engine, self.settings)
        try:
            await seed_registry(engine)
            empty = await assembler.build("morning", now=now)
            self.assertEqual(empty["candidates"], [])
            alert_id, attention_id, agent_id, interaction_id = [uuid.uuid4() for _ in range(4)]
            async with engine.begin() as connection:
                await connection.execute(sa.insert(Alert).values(
                    alert_id=alert_id, alert_type="fixture", severity="critical", entity_id="fan",
                    title="Stored fault", last_updated_at=moment, opened_at=moment,
                ))
                await connection.execute(sa.insert(AttentionItem).values(
                    attention_item_id=attention_id, reason="Check plants", entity_id="owner", created_at=moment,
                ))
                await connection.execute(sa.insert(Reminder).values(
                    text="Check plants", due_at=moment, status="fired", attention_item_id=attention_id,
                ))
                for event_id, event_type in ((agent_id, "agent.job.completed"), (interaction_id, "person.interaction.ended")):
                    await connection.execute(sa.insert(Event).values(
                        event_id=event_id, schema_version=1, event_type=event_type, entity_id="talos",
                        source_id="talos_agent", received_at=moment, severity="info", provenance={},
                    ))
                await connection.execute(sa.insert(StateTransition).values(
                    entity_id="fan", property_name="power", occurred_at=moment, to_value={"value": False},
                    to_status="current", reason="value_changed", source_event_id=agent_id,
                ))
                # Baseline values 0,2,4,6 in unequally populated hourly buckets.
                # Pooled mean=3, sample variance=20/3; anomalous value=30.
                for offset, value in enumerate((0.0, 2.0, 4.0, 6.0)):
                    await connection.execute(sa.insert(Measurement).values(
                        time=now-timedelta(days=2, hours=offset//2, minutes=offset),
                        entity_id="fan", measurement_name="temperature", source_id="fan_pico",
                        received_at=moment, value_double=value, unit="C",
                    ))
                await connection.execute(sa.insert(Measurement).values(
                    time=moment, entity_id="fan", measurement_name="temperature", source_id="fan_pico",
                    received_at=moment, value_double=30.0, unit="C",
                ))
                # Different units must never borrow the Celsius baseline.
                await connection.execute(sa.insert(Measurement).values(
                    time=moment-timedelta(minutes=1), entity_id="fan", measurement_name="temperature",
                    source_id="fan_pico", received_at=moment, value_double=90.0, unit="F",
                ))
            async with engine.connect() as connection:
                connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(sa.text(
                    "CALL refresh_continuous_aggregate('measurements_1h', CAST(:start AS timestamptz), CAST(:end AS timestamptz))"
                ), {"start": now-timedelta(days=8), "end": now})
            result = await assembler.build("morning", now=now)
            self.assertEqual({c["category"] for c in result["candidates"]},
                             {"alert", "reminder", "transition", "agent_outcome", "interaction", "novelty"})
            unusual = [c for c in result["candidates"] if c["category"] == "novelty"]
            self.assertEqual(len(unusual), 1)
            self.assertAlmostEqual(unusual[0]["novelty_score"], 27/(20/3)**0.5)
            self.assertEqual(unusual[0]["evidence"]["baseline_samples"], 4)
            self.assertEqual(result["candidates"][0]["item_id"], f"alert:{alert_id}")
            self.assertTrue(all(c["query"].startswith("briefing.") for c in result["candidates"]))

            # A failed send cannot change the delivery-derived window.
            async with engine.begin() as connection:
                await connection.execute(sa.insert(NotificationDelivery).values(
                    alert_id=alert_id, attention_item_id=attention_id, channel="voice",
                    status="failed", attempted_at=moment+timedelta(minutes=1),
                    metadata_json={"briefing_kind": "morning"},
                ))
            self.assertEqual((await assembler.build("morning", now=now))["window"]["origin"],
                             "configured_first_run_window")
            async with engine.begin() as connection:
                await connection.execute(sa.insert(NotificationDelivery).values(
                    alert_id=alert_id, attention_item_id=attention_id, channel="voice",
                    status="delivered", attempted_at=moment+timedelta(minutes=2),
                    metadata_json={"briefing_kind": "morning"},
                ))
                await connection.execute(sa.update(AttentionItem).where(
                    AttentionItem.attention_item_id == attention_id).values(delivery_status="delivered"))
            delivered = await assembler.build("morning", now=now)
            self.assertEqual(delivered["window"]["start"], (moment+timedelta(minutes=2)).isoformat())
            self.assertEqual(delivered["candidates"], [])
            arrival = await assembler.build("arrival", now=now)
            self.assertEqual(arrival["window"]["origin"], "configured_first_run_window")
            self.assertNotIn(f"attention:{attention_id}", [c["item_id"] for c in arrival["candidates"]])
            self.assertNotIn(f"alert:{alert_id}", [c["item_id"] for c in arrival["candidates"]])

            bounded = BriefingAssembler(engine, self.settings.model_copy(update={"briefing_max_candidates": 1}))
            self.assertTrue((await bounded.build("arrival", now=now))["truncated"])
        finally:
            await engine.dispose()
