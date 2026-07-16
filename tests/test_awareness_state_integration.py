"""Phase 3 integration: state authority, freshness, telemetry, bounded queries.

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait

Drives the ingestion pipeline directly (no broker needed) against a scratch
database migrated to head, then exercises the freshness worker and the
bounded read modules. Covered acceptance criteria: durable state separate
from history; delayed/weaker data cannot replace newer authoritative state;
stale/offline at configured thresholds with deduplicated transitions;
conflict representation; deadband jitter suppression; typed telemetry +
aggregates; range/point bounds enforced.
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


class StateTelemetryIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"talos_awareness_state_{uuid.uuid4().hex[:8]}"
        self.settings = AwarenessSettings(
            _env_file=None,
            db_password=base.db_password.get_secret_value(),
            db_host=base.db_host,
            db_port=base.db_port,
            db_user=base.db_user,
            db_name=self.scratch_name,
            default_stale_after_seconds=3600.0,
            default_offline_after_seconds=7200.0,
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

    def test_state_and_telemetry_end_to_end(self) -> None:
        asyncio.run(self._run_flow())

    async def _run_flow(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.db.session import build_engine
        from talos.awareness.history.queries import query_events, read_entity_state
        from talos.awareness.history.telemetry import QueryBoundsError, query_measurements
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.registry.sources import SourceRepository
        from talos.awareness.state.freshness import FreshnessWorker

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            # Per-property deadband for the simulator source (hysteresis test).
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "UPDATE sources SET metadata = metadata || "
                        "'{\"deadbands\": {\"temperature\": 1.0}}'::jsonb "
                        "WHERE source_id = 'sim_device'"
                    )
                )
            sources = SourceRepository(engine)
            await sources.refresh(force=True)
            pipeline = IngestionPipeline(engine, sources, self.settings)
            boot_id = f"boot-{uuid.uuid4().hex[:8]}"
            now = datetime.now(timezone.utc)

            async def ingest(minutes_ago: float, sequence: int, value: float) -> str:
                observed = (now - timedelta(minutes=minutes_ago)).isoformat()
                return await pipeline.handle(
                    InboundMessage(
                        topic="home/sim/greenhouse/telemetry/temperature",
                        payload=json.dumps(
                            {
                                "event_id": str(uuid.uuid4()),
                                "observed_at": observed,
                                "sequence": sequence,
                                "boot_id": boot_id,
                                "value": value,
                                "unit": "F",
                            }
                        ).encode(),
                    )
                )

            async def query(sql: str, **params):
                async with engine.connect() as connection:
                    return (await connection.execute(sa.text(sql), params)).all()

            # --- initial value creates state + telemetry + transition --------
            self.assertEqual(await ingest(10, 1, 70.0), "accepted")
            state = await read_entity_state(engine, self.settings, "sim_greenhouse")
            self.assertEqual(state["properties"][0]["value"], 70.0)
            self.assertEqual(state["properties"][0]["status"], "current")
            self.assertIn("as_of", state)

            # --- jitter inside the 1.0 deadband: no transition ---------------
            self.assertEqual(await ingest(9, 2, 70.4), "accepted")
            # --- move beyond the deadband: transition ------------------------
            self.assertEqual(await ingest(8, 3, 72.0), "accepted")
            transitions = await query(
                "SELECT reason FROM state_transitions "
                "WHERE entity_id = 'sim_greenhouse' AND property_name = 'temperature' "
                "ORDER BY id"
            )
            self.assertEqual([row.reason for row in transitions], ["initial", "update"])
            state = await read_entity_state(engine, self.settings, "sim_greenhouse")
            self.assertEqual(state["properties"][0]["value"], 72.0)

            # --- delayed older event: history keeps it, state does not move --
            self.assertEqual(await ingest(30, 4, 55.0), "accepted")
            state = await read_entity_state(engine, self.settings, "sim_greenhouse")
            self.assertEqual(state["properties"][0]["value"], 72.0)
            events = await query(
                "SELECT count(*) AS n FROM events "
                "WHERE event_type = 'sim.telemetry.temperature'"
            )
            self.assertEqual(events[0].n, 4)
            measurements = await query("SELECT count(*) AS n FROM measurements")
            self.assertEqual(measurements[0].n, 4)

            # --- freshness: configured thresholds mark stale then offline ----
            worker = FreshnessWorker(engine, self.settings)
            self.assertEqual(await worker.tick(now=now), 0)  # nothing overdue yet

            later = now + timedelta(hours=1, minutes=1)
            marked = await worker.tick(now=later)
            self.assertGreaterEqual(marked, 1)
            state = await read_entity_state(engine, self.settings, "sim_greenhouse")
            self.assertEqual(state["properties"][0]["stored_status"], "stale")
            self.assertEqual(await worker.tick(now=later), 0)  # idempotent re-run

            much_later = now + timedelta(hours=2, minutes=1)
            self.assertGreaterEqual(await worker.tick(now=much_later), 1)
            state = await read_entity_state(engine, self.settings, "sim_greenhouse")
            self.assertEqual(state["properties"][0]["stored_status"], "offline")
            health = await query(
                "SELECT health_status, previous_status FROM source_health_history "
                "WHERE source_id = 'sim_device' ORDER BY id"
            )
            self.assertEqual(health[-1].health_status, "offline")
            self.assertEqual(await worker.tick(now=much_later), 0)

            # --- a new message recovers state and source health --------------
            self.assertEqual(await ingest(0, 5, 71.0), "accepted")
            state = await read_entity_state(engine, self.settings, "sim_greenhouse")
            self.assertEqual(state["properties"][0]["stored_status"], "current")
            self.assertEqual(state["properties"][0]["value"], 71.0)
            recovery = await query(
                "SELECT reason FROM state_transitions "
                "WHERE property_name = 'temperature' ORDER BY id DESC LIMIT 1"
            )
            self.assertEqual(recovery[0].reason, "recovered")
            health = await query(
                "SELECT health_status FROM source_health_history "
                "WHERE source_id = 'sim_device' ORDER BY id DESC LIMIT 1"
            )
            self.assertEqual(health[0].health_status, "healthy")

            # --- conflict: equal comparison time, different value ------------
            await self._conflict_scenario(engine, sources)

            # --- bounded queries ---------------------------------------------
            result = await query_measurements(
                engine,
                self.settings,
                entity_id="sim_greenhouse",
                measurement="temperature",
                start=now - timedelta(hours=1),
                end=now + timedelta(minutes=5),
                max_points=3,
            )
            self.assertTrue(result["truncated"])
            self.assertEqual(len(result["points"]), 3)
            self.assertIn("as_of", result)

            with self.assertRaises(QueryBoundsError):
                await query_measurements(
                    engine,
                    self.settings,
                    entity_id="sim_greenhouse",
                    measurement="temperature",
                    start=now - timedelta(days=45),
                    end=now,
                )
            with self.assertRaises(QueryBoundsError):
                await query_measurements(
                    engine,
                    self.settings,
                    entity_id="sim_greenhouse",
                    measurement="temperature",
                    start=now - timedelta(hours=1),
                    end=now,
                    max_points=self.settings.max_query_points + 1,
                )
            with self.assertRaises(QueryBoundsError):
                await query_events(
                    engine, self.settings, start=now, end=now - timedelta(hours=1)
                )

            events_page = await query_events(
                engine,
                self.settings,
                start=now - timedelta(hours=1),
                end=now + timedelta(minutes=5),
                event_type="sim.telemetry.temperature",
                limit=2,
            )
            self.assertTrue(events_page["truncated"])
            self.assertEqual(len(events_page["events"]), 2)

            # --- aggregates match raw data ------------------------------------
            async with engine.connect() as connection:
                autocommit = await connection.execution_options(
                    isolation_level="AUTOCOMMIT"
                )
                await autocommit.execute(
                    sa.text(
                        "CALL refresh_continuous_aggregate('measurements_1h', NULL, NULL)"
                    )
                )
            aggregate = await query_measurements(
                engine,
                self.settings,
                entity_id="sim_greenhouse",
                measurement="temperature",
                start=now - timedelta(hours=2),
                end=now + timedelta(hours=1),
                aggregation="1h",
            )
            total = sum(point["count"] for point in aggregate["points"])
            self.assertEqual(total, 5)
            values = [70.0, 70.4, 72.0, 55.0, 71.0]
            self.assertEqual(
                min(point["min"] for point in aggregate["points"]), min(values)
            )
            self.assertEqual(
                max(point["max"] for point in aggregate["points"]), max(values)
            )
        finally:
            await engine.dispose()

    async def _conflict_scenario(self, engine, sources) -> None:
        """Equal comparison time + different value marks the row conflicting."""
        from talos.awareness.registry.sources import SourceRecord
        from talos.awareness.schemas.events import EventEnvelope, Provenance
        from talos.awareness.state.classification import StateUpdate
        from talos.awareness.state.manager import StateManager

        moment = datetime.now(timezone.utc)
        source = sources.get("sim_device")
        assert isinstance(source, SourceRecord)
        manager = StateManager()

        def envelope(value: str) -> EventEnvelope:
            return EventEnvelope(
                event_type="sim.state.reported",
                entity_id="sim_greenhouse",
                source_id="sim_device",
                received_at=moment,
                observed_at=moment,
                payload={"pump": value},
                provenance=Provenance(
                    transport="mqtt",
                    topic_or_endpoint="home/sim/greenhouse/state",
                    clock_quality="device_synced",
                ),
            )

        import sqlalchemy as sa

        from talos.awareness.db.models import Event

        async with engine.begin() as connection:
            for value in ("on", "off"):
                env = envelope(value)
                await connection.execute(
                    sa.insert(Event).values(
                        event_id=env.event_id,
                        schema_version=env.schema_version,
                        event_type=env.event_type,
                        entity_id=env.entity_id,
                        source_id=env.source_id,
                        observed_at=env.observed_at,
                        received_at=env.received_at,
                        severity=env.severity,
                        payload=env.payload,
                        provenance=env.provenance.model_dump(mode="json"),
                    )
                )
                await manager.apply(
                    connection,
                    env,
                    (StateUpdate("sim_greenhouse", "pump", value, "string"),),
                    source,
                )

        from talos.awareness.history.queries import read_entity_state

        state = await read_entity_state(engine, self.settings, "sim_greenhouse")
        pump = next(
            item for item in state["properties"] if item["property_name"] == "pump"
        )
        self.assertEqual(pump["status"], "conflicting")
        self.assertEqual(pump["value"], "on")  # kept, not fabricated
        self.assertEqual(pump["conflict"]["value"], "off")  # contender preserved


if __name__ == "__main__":
    unittest.main()
