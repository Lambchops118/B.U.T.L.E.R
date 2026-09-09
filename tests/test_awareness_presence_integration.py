"""Integration: internal ingestion, presence, interaction, and human context.

Covers the refactor that turns the awareness backend from a device backend
into one that also knows about the person in the room and about the agent's
own work:

- ``POST /ingest`` accepts a message on the internal transport and returns the
  pipeline's disposition synchronously (accepted / dead_letter:{reason});
- the internal source is pinned to its transport, so the same message arriving
  over MQTT is dead-lettered instead of forging presence;
- a presence signal becomes durable state on the ``owner`` person entity;
- the situation snapshot reports presence, honors ``interruptibility``, and
  orders attention items by ``conversation_relevance`` without ever letting a
  critical alert be reordered behind them.

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait
"""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

try:
    from talos.awareness.config import AwarenessSettings, SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


class _ScratchDatabaseTest(unittest.TestCase):
    """Shared scratch-database harness (same pattern as the other suites)."""

    prefix = "talos_awareness_presence"

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"{self.prefix}_{uuid.uuid4().hex[:8]}"
        self.settings = AwarenessSettings(
            _env_file=None,
            db_password=base.db_password.get_secret_value(),
            db_host=base.db_host,
            db_port=base.db_port,
            db_user=base.db_user,
            db_name=self.scratch_name,
            mqtt_enabled=False,  # API-only lifespan for the TestClient
        )
        from urllib.parse import quote_plus

        self.admin_dsn = (
            f"postgresql://{quote_plus(base.db_user)}:"
            f"{quote_plus(base.db_password.get_secret_value())}"
            f"@{base.db_host}:{base.db_port}/postgres"
        )
        if not asyncio.run(self._create_scratch_database()):
            self.skipTest("awareness Postgres is not reachable (start docker compose)")

        from talos.awareness.db.migrate import upgrade_to_head

        upgrade_to_head(self.settings.database_url)

    async def _create_scratch_database(self) -> bool:
        import asyncpg

        try:
            connection = await asyncpg.connect(self.admin_dsn, timeout=3)
        except Exception:
            return False
        try:
            await connection.execute(f'CREATE DATABASE "{self.scratch_name}"')
        finally:
            await connection.close()
        return True

    def tearDown(self) -> None:
        if not hasattr(self, "scratch_name"):
            return

        async def _drop() -> None:
            import asyncpg

            try:
                connection = await asyncpg.connect(self.admin_dsn, timeout=3)
            except Exception:
                return
            try:
                await connection.execute(
                    f'DROP DATABASE IF EXISTS "{self.scratch_name}" WITH (FORCE)'
                )
            finally:
                await connection.close()

        asyncio.run(_drop())


class InternalIngestionTest(_ScratchDatabaseTest):
    prefix = "talos_awareness_ingest"

    def test_ingest_endpoint_reports_disposition_synchronously(self) -> None:
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            # --- accepted: presence on a topic the internal source owns ------
            response = client.post(
                "/ingest",
                json={
                    "topic": "home/presence/owner/state",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "present": True,
                        "modality": "wake_word",
                        "confidence": 0.95,
                    },
                },
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertTrue(body["accepted"], body)
            self.assertEqual(body["disposition"], "accepted")

            # --- the disposition is the point: a bad topic says why, now -----
            response = client.post(
                "/ingest",
                json={"topic": "home/nobody/owns/this", "payload": {"value": 1}},
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["accepted"])
            self.assertEqual(
                response.json()["disposition"], "dead_letter:unauthorized_topic"
            )

            # --- transport pinning: the same message over MQTT is refused ----
            response = client.post(
                "/ingest",
                json={
                    "topic": "home/presence/owner/state",
                    "transport": "mqtt",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "present": True,
                        "modality": "voice",
                    },
                },
            )
            self.assertEqual(
                response.json()["disposition"], "dead_letter:unauthorized_transport"
            )

            # --- a device source stays unrestricted (no regression) ---------
            response = client.post(
                "/ingest",
                json={
                    "topic": "home/sim/greenhouse/telemetry/temperature",
                    "transport": "mqtt",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "value": 70.5,
                        "unit": "F",
                    },
                },
            )
            self.assertEqual(response.json()["disposition"], "accepted")

            # --- oversized bodies are refused, not dead-lettered ------------
            response = client.post(
                "/ingest",
                json={
                    "topic": "home/presence/owner/state",
                    "payload": {"blob": "x" * (self.settings.max_event_payload_bytes + 10)},
                },
            )
            self.assertEqual(response.status_code, 413)

    def test_presence_becomes_durable_state_on_the_person_entity(self) -> None:
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            client.post(
                "/ingest",
                json={
                    "topic": "home/presence/owner/state",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "present": True,
                        "modality": "voice",
                    },
                },
            )
            state = client.get("/state/owner")
            self.assertEqual(state.status_code, 200)
            properties = {
                row["property_name"]: row for row in state.json()["properties"]
            }
            self.assertIn("present", properties)
            self.assertTrue(properties["present"]["value"])
            self.assertEqual(properties["modality"]["value"], "voice")
            # Every read stays temporally qualified (INV-06).
            self.assertIn("status", properties["present"])
            self.assertIn("age_seconds", properties["present"])
            self.assertEqual(properties["present"]["source_id"], "talos_agent")

    def test_interaction_is_history_not_state(self) -> None:
        """Conversation facts are events; they must not become device state."""
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            response = client.post(
                "/ingest",
                json={
                    "topic": "home/interaction/owner/event",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "event_type": "person.interaction.started",
                        "session_id": "voice",
                        "modality": "voice",
                        "entity_ids": ["quad_pump"],
                    },
                },
            )
            self.assertEqual(response.json()["disposition"], "accepted")

            now = datetime.now(timezone.utc)
            events = client.get(
                "/events",
                params={
                    "start": (now - timedelta(minutes=5)).isoformat(),
                    "end": (now + timedelta(minutes=5)).isoformat(),
                    "entity_id": "owner",
                },
            )
            self.assertEqual(events.status_code, 200)
            types = [row["event_type"] for row in events.json()["events"]]
            self.assertIn("person.interaction.started", types)

            # No state property was invented from an interaction event.
            state = client.get("/state/owner")
            names = {row["property_name"] for row in state.json()["properties"]}
            self.assertNotIn("session_id", names)
            self.assertNotIn("modality", names)


class AgentOutcomeRuleTest(_ScratchDatabaseTest):
    prefix = "talos_awareness_agentfail"

    def test_failed_background_job_raises_a_deferred_alert(self) -> None:
        """A job the user asked for that failed becomes recallable, not just logged."""
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            response = client.post(
                "/ingest",
                json={
                    "topic": "home/agent/talos/event",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "event_type": "agent.job.failed",
                        "severity": "warning",
                        "job_id": "job-1",
                        "session_id": "voice",
                        "source": "voice",
                        "error": "pump driver timed out",
                    },
                },
            )
            self.assertEqual(response.json()["disposition"], "accepted")

            alerts = client.get("/alerts", params={"limit": 10}).json()["alerts"]
            titles = [alert["title"] for alert in alerts]
            self.assertIn("Background work failed", titles)

            # Deliberately deferred, not an interruption, and not notified.
            snapshot = client.get("/situation").json()
            self.assertIn("A background job failed", snapshot["text"])
            self.assertIn("next_interaction", snapshot["text"])

    def test_failed_tool_calls_are_history_only(self) -> None:
        """Tool failures are recorded but must not raise an alert each time."""
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            client.post(
                "/ingest",
                json={
                    "topic": "home/agent/talos/event",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "event_type": "agent.tool.failed",
                        "severity": "warning",
                        "tool_name": "get_current_state",
                        "round": 1,
                    },
                },
            )
            alerts = client.get("/alerts", params={"limit": 10}).json()["alerts"]
            self.assertEqual(alerts, [])


class OfflineDetectionOptOutTest(_ScratchDatabaseTest):
    prefix = "talos_awareness_offline"

    def test_device_sources_still_go_offline_but_the_agent_does_not(self) -> None:
        """The opt-out must apply to the agent alone, never fleet-wide.

        Guards the SQL NULL trap: a source with no ``offline_detection`` key
        must still be checked, so a missing key can never silently disable
        offline detection for every device.
        """
        asyncio.run(self._check())

    def test_presence_never_expires_and_pump_snapshot_has_cadence_margin(self) -> None:
        asyncio.run(self._check_state_freshness_policy())

    def test_superseded_pump_source_false_fault_is_reconciled(self) -> None:
        asyncio.run(self._check_superseded_source_reconciliation())

    def test_quiet_hours_do_not_read_the_owner_as_gone(self) -> None:
        asyncio.run(self._check_reads_never_expire_presence())

    async def _check_reads_never_expire_presence(self) -> None:
        """Silence must not age a presence reading on the *read* path either.

        The worker being told to leave presence alone is only half the policy:
        every read re-derives expiry from the deadline, so without the same
        opt-out here a long quiet stretch still reported "not detected as
        present (stale)" -- the fact behind the unasked-for welcome back.
        """
        import sqlalchemy as sa

        from talos.awareness.context.broker import SituationBroker
        from talos.awareness.db.models import CurrentState
        from talos.awareness.db.session import build_engine
        from talos.awareness.registry.bootstrap import seed_registry

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            now = datetime.now(timezone.utc)
            long_ago = now - timedelta(hours=9)
            async with engine.begin() as connection:
                await connection.execute(sa.insert(CurrentState).values(
                    entity_id="owner", property_name="present", value_json={"value": True},
                    value_type="boolean", source_id="talos_agent",
                    observed_at=long_ago, received_at=long_ago, updated_at=long_ago,
                    state_status="current"))
            context = await SituationBroker(engine, self.settings).build()
            self.assertEqual(context["presence"], "present via unknown modality")
            self.assertNotIn("stale", context["limitations"])
            self.assertIn("STATE owner.present = True (current", context["text"])
        finally:
            await engine.dispose()

    def test_a_presence_row_left_stale_by_the_old_policy_is_restored(self) -> None:
        asyncio.run(self._check_stale_presence_reconciliation())

    async def _check_stale_presence_reconciliation(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.db.models import CurrentState
        from talos.awareness.db.session import build_engine
        from talos.awareness.registry.bootstrap import seed_registry

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            now = datetime.now(timezone.utc)
            async with engine.begin() as connection:
                await connection.execute(sa.insert(CurrentState).values(
                    entity_id="owner", property_name="present", value_json={"value": True},
                    value_type="boolean", source_id="talos_agent",
                    received_at=now-timedelta(hours=9), state_status="stale"))
            await seed_registry(engine)
            async with engine.connect() as connection:
                status = (await connection.execute(sa.select(CurrentState.state_status).where(
                    CurrentState.entity_id == "owner",
                    CurrentState.property_name == "present"))).scalar_one()
            self.assertEqual(status, "current")
        finally:
            await engine.dispose()

    async def _check_superseded_source_reconciliation(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.db.models import Alert, AttentionItem, Source
        from talos.awareness.db.session import build_engine
        from talos.awareness.registry.bootstrap import seed_registry

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            async with engine.begin() as connection:
                await connection.execute(sa.update(Source).where(
                    Source.source_id == "quad_pump_pico").values(health_status="offline"))
                alert_id = (await connection.execute(sa.insert(Alert).values(
                    alert_type="source_offline", severity="warning",
                    title="Source offline: quad_pump_pico", status="open",
                    deduplication_key="source_offline:quad_pump_pico").returning(
                        Alert.alert_id))).scalar_one()
                await connection.execute(sa.insert(AttentionItem).values(
                    alert_id=alert_id, reason="legacy source offline"))
            await seed_registry(engine)
            async with engine.connect() as connection:
                source_status = (await connection.execute(sa.select(Source.health_status).where(
                    Source.source_id == "quad_pump_pico"))).scalar_one()
                alert_status = (await connection.execute(sa.select(Alert.status).where(
                    Alert.alert_id == alert_id))).scalar_one()
                delivery_status = (await connection.execute(sa.select(
                    AttentionItem.delivery_status).where(
                        AttentionItem.alert_id == alert_id))).scalar_one()
            self.assertEqual((source_status, alert_status, delivery_status),
                             ("unknown", "resolved", "cancelled"))
        finally:
            await engine.dispose()

    async def _check_state_freshness_policy(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.db.models import CurrentState
        from talos.awareness.db.session import build_engine
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.state.freshness import FreshnessWorker

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            now = datetime.now(timezone.utc)
            async with engine.begin() as connection:
                await connection.execute(sa.insert(CurrentState).values(
                    entity_id="owner", property_name="present", value_json={"value": True},
                    value_type="boolean", source_id="talos_agent",
                    received_at=now-timedelta(days=2), state_status="current"))
                await connection.execute(sa.insert(CurrentState).values(
                    entity_id="quad_pump", property_name="relay_1", value_json={"value": False},
                    value_type="boolean", source_id="quad_pump_canonical",
                    received_at=now-timedelta(seconds=350), state_status="current"))
            await FreshnessWorker(engine, self.settings).tick(now)
            async with engine.connect() as connection:
                statuses = dict((await connection.execute(sa.select(
                    CurrentState.entity_id, CurrentState.state_status
                ).where(CurrentState.property_name.in_(("present", "relay_1"))))).all())
            self.assertEqual(statuses, {"owner": "current", "quad_pump": "current"})

            await FreshnessWorker(engine, self.settings).tick(now+timedelta(seconds=71))
            async with engine.connect() as connection:
                pump_status = (await connection.execute(sa.select(CurrentState.state_status).where(
                    CurrentState.entity_id == "quad_pump",
                    CurrentState.property_name == "relay_1"))).scalar_one()
            self.assertEqual(pump_status, "stale")
        finally:
            await engine.dispose()

    async def _check(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.models import Source
        from talos.awareness.db.session import build_engine
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.state.freshness import FreshnessWorker

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            long_ago = datetime.now(timezone.utc) - timedelta(days=30)
            async with engine.begin() as connection:
                # Both sources have been silent for a month.
                await connection.execute(
                    sa.update(Source)
                    .where(Source.source_id.in_(("sim_device", "talos_agent")))
                    .values(last_received_at=long_ago, health_status="healthy")
                )

            worker = FreshnessWorker(engine, self.settings, alert_hook=None)
            await worker.tick()

            async with engine.connect() as connection:
                statuses = dict(
                    (
                        await connection.execute(
                            sa.select(Source.source_id, Source.health_status).where(
                                Source.source_id.in_(("sim_device", "talos_agent"))
                            )
                        )
                    ).all()
                )
            # The device is faulted; the agent is simply quiet.
            self.assertEqual(statuses["sim_device"], "offline")
            self.assertEqual(statuses["talos_agent"], "healthy")
        finally:
            await engine.dispose()


class SituationHumanContextTest(_ScratchDatabaseTest):
    prefix = "talos_awareness_situation"

    def test_presence_relevance_and_interruptibility(self) -> None:
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            now = datetime.now(timezone.utc)
            client.post(
                "/ingest",
                json={
                    "topic": "home/presence/owner/state",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "observed_at": now.isoformat(),
                        "present": True,
                        "modality": "voice",
                    },
                },
            )
            client.post(
                "/ingest",
                json={
                    "topic": "home/interaction/owner/event",
                    "payload": {
                        "event_id": str(uuid.uuid4()),
                        "event_type": "person.interaction.started",
                        "session_id": "voice",
                        "modality": "voice",
                        "entity_ids": ["quad_pump"],
                    },
                },
            )

            asyncio.run(self._raise_attention_items())

            snapshot = client.get("/situation").json()
            text = snapshot["text"]

            # Presence is reported, with its freshness qualification.
            self.assertIn("PRESENCE owner", text)
            self.assertIn("present via voice", text)
            self.assertIn("INTERACTION last", text)
            self.assertIn("quad_pump", text)
            self.assertEqual(snapshot["presence"], "present via voice")

            audit = {entry["item_id"]: entry for entry in snapshot["audit"]}
            relevant = [
                entry
                for entry in audit.values()
                if entry["item_id"].startswith("attention:")
                and "matches_conversation_entity" in entry["reason"]
            ]
            self.assertTrue(relevant, "expected the quad_pump item to score relevance")

            # The relevant attention item sorts ahead of the irrelevant one.
            attention_order = [
                entry["item_id"]
                for entry in snapshot["audit"]
                if entry["item_id"].startswith("attention:")
            ]
            self.assertIn("matches_conversation_entity", audit[attention_order[0]]["reason"])

            # A passive item is present while the user is (present=True).
            self.assertIn("passive-while-present", text)

    def test_passive_items_are_withheld_when_nobody_is_present(self) -> None:
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            asyncio.run(self._raise_attention_items())
            snapshot = client.get("/situation").json()
            # No presence was ever reported in this test.
            self.assertNotIn("passive-while-present", snapshot["text"])
            self.assertIn("no presence signal", snapshot["limitations"])
            # Non-passive items are unaffected.
            self.assertIn("pump needs attention", snapshot["text"])

    async def _raise_attention_items(self) -> None:
        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.session import build_engine
        from talos.awareness.registry.bootstrap import seed_registry

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            alerts = AlertService(self.settings)
            now = datetime.now(timezone.utc)
            async with engine.begin() as connection:
                await alerts.raise_attention(
                    connection,
                    alert_id=None,
                    entity_id="quad_pump",
                    severity="notice",
                    reason="pump needs attention",
                    priority=5,
                    interruptibility="next_interaction",
                    preferred_channel="voice",
                    available_after_seconds=0.0,
                    expires_after_seconds=None,
                    cooldown_key=None,
                    cooldown_seconds=0.0,
                    notify=False,
                    notification_payload={},
                    now=now,
                    conversation_relevance={"entity_id": "quad_pump"},
                )
                await alerts.raise_attention(
                    connection,
                    alert_id=None,
                    entity_id="fan",
                    severity="notice",
                    reason="unrelated fan note",
                    priority=5,
                    interruptibility="next_interaction",
                    preferred_channel="voice",
                    available_after_seconds=0.0,
                    expires_after_seconds=None,
                    cooldown_key=None,
                    cooldown_seconds=0.0,
                    notify=False,
                    notification_payload={},
                    now=now,
                    conversation_relevance={"entity_id": "fan"},
                )
                await alerts.raise_attention(
                    connection,
                    alert_id=None,
                    entity_id="fan",
                    severity="debug",
                    reason="passive-while-present",
                    priority=6,
                    interruptibility="passive",
                    preferred_channel="voice",
                    available_after_seconds=0.0,
                    expires_after_seconds=None,
                    cooldown_key=None,
                    cooldown_seconds=0.0,
                    notify=False,
                    notification_payload={},
                    now=now,
                    conversation_relevance={},
                )
        finally:
            await engine.dispose()


class CriticalAlertOrderingTest(_ScratchDatabaseTest):
    prefix = "talos_awareness_critical"

    def test_relevance_never_reorders_across_priority_bands(self) -> None:
        """A relevant attention item must never outrank a critical alert."""
        from talos.awareness.context.broker import (
            PRIORITY_ATTENTION,
            PRIORITY_CRITICAL_ALERTS,
            Candidate,
            select_items,
        )

        candidates = [
            Candidate(
                item_id="attention:relevant",
                priority=PRIORITY_ATTENTION,
                text="x" * 70,
                reason="pending_attention+matches_conversation_entity",
                relevance=99.0,
            ),
            Candidate(
                item_id="alert:critical",
                priority=PRIORITY_CRITICAL_ALERTS,
                text="x" * 70,
                reason="active_critical_alert",
            ),
        ]
        selected, _ = select_items(candidates, budget_tokens=10_000)
        self.assertEqual(selected[0].item_id, "alert:critical")


if __name__ == "__main__":
    unittest.main()
