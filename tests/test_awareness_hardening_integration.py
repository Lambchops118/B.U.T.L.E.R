"""Phase 8 integration: retention, consolidation, artifacts, write auth.

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from talos.awareness.config import AwarenessSettings, SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


class HardeningIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"talos_awareness_hard_{uuid.uuid4().hex[:8]}"
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings = AwarenessSettings(
            _env_file=None,
            db_password=base.db_password.get_secret_value(),
            db_host=base.db_host,
            db_port=base.db_port,
            db_user=base.db_user,
            db_name=self.scratch_name,
            data_directory=Path(self.tempdir.name),
            mqtt_enabled=False,
            retention_batch_size=2,  # force multiple resumable batches
            retention_heartbeat_events_days=1,
            retention_raw_measurements_days=1,
            consolidation_episode_threshold=3,
            consolidation_window_days=30,
            consolidation_decay_after_days=1,
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
        self.tempdir.cleanup()
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

    def test_retention_plan_execute_protections(self) -> None:
        asyncio.run(self._run_retention())

    async def _run_retention(self) -> None:
        import json

        import sqlalchemy as sa

        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.session import build_engine
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.registry.sources import SourceRepository
        from talos.awareness.retention.service import RetentionService
        from talos.awareness.rules.engine import RuleEngine
        from talos.awareness.rules.policy import load_policy

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            sources = SourceRepository(engine)
            await sources.refresh(force=True)
            pipeline = IngestionPipeline(
                engine,
                sources,
                self.settings,
                rule_engine=RuleEngine(load_policy(), AlertService(self.settings)),
            )
            boot = f"boot-{uuid.uuid4().hex[:6]}"

            async def publish(topic: str, body: dict, sequence: int) -> None:
                payload = {
                    "event_id": str(uuid.uuid4()),
                    "sequence": sequence,
                    "boot_id": boot,
                    **body,
                }
                result = await pipeline.handle(
                    InboundMessage(topic=topic, payload=json.dumps(payload).encode())
                )
                assert result == "accepted", result

            # five old heartbeats + five old measurements + an OPEN alert.
            # The simulator source has a trusted clock, so an old observed_at
            # lands the measurement in an old hypertable chunk directly.
            old = datetime.now(timezone.utc) - timedelta(days=3)
            sequence = 0
            for index in range(5):
                sequence += 1
                await publish("home/sim/greenhouse/heartbeat", {}, sequence)
                sequence += 1
                await publish(
                    "home/sim/greenhouse/telemetry/temperature",
                    {
                        "value": 70.0 + sequence,
                        "unit": "F",
                        "observed_at": (old + timedelta(minutes=index)).isoformat(),
                    },
                    sequence,
                )
            sequence += 1
            await publish(
                "home/sim/greenhouse/event",
                {
                    "event_type": "plant.overflow.detected",
                    "severity": "critical",
                    "payload": {"overflow": True, "zone": 1},
                },
                sequence,
            )

            async def execute(sql: str, **params):
                async with engine.begin() as connection:
                    await connection.execute(sa.text(sql), params)

            async def query(sql: str, **params):
                async with engine.connect() as connection:
                    return (await connection.execute(sa.text(sql), params)).all()

            # age the events past the 1-day window (received_at is not the
            # hypertable partition column, so this is a plain update)
            await execute("UPDATE events SET received_at = :old", old=old)

            service = RetentionService(engine, self.settings)

            # --- dry run: exact plan, nothing deleted --------------------------
            plan = await service.plan()
            plan_by_name = {p["policy"]: p for p in plan["policies"]}
            self.assertTrue(plan["dry_run"])
            self.assertEqual(plan_by_name["heartbeat_events"]["eligible"], 5)
            self.assertEqual(plan_by_name["raw_measurements"]["eligible"], 5)
            self.assertEqual(plan_by_name["resolved_alerts"]["eligible"], 0)  # open: protected
            counts = await query("SELECT count(*) AS n FROM events")
            self.assertEqual(counts[0].n, 11)  # dry run deleted nothing

            # --- execute: batched deletion, evidence protected ------------------
            report = await service.execute()
            report_by_name = {p["policy"]: p for p in report["policies"]}
            self.assertEqual(report_by_name["heartbeat_events"]["deleted"], 5)
            self.assertEqual(report_by_name["raw_measurements"]["deleted"], 5)
            self.assertIn("aggregates_refreshed_through", report_by_name["raw_measurements"])
            counts = await query("SELECT count(*) AS n FROM measurements")
            self.assertEqual(counts[0].n, 0)
            # aggregates preserved the deleted raw data's statistics
            aggregate = await query(
                "SELECT sum(sample_count) AS n FROM measurements_1h "
                "WHERE entity_id = 'sim_greenhouse'"
            )
            self.assertEqual(aggregate[0].n, 5)
            # the critical overflow event (alert evidence) survived
            survivors = await query(
                "SELECT event_type FROM events WHERE event_type = 'plant.overflow.detected'"
            )
            self.assertEqual(len(survivors), 1)
            alerts = await query("SELECT status FROM alerts")
            self.assertEqual(alerts[0].status, "open")

            # --- idempotent re-run ----------------------------------------------
            rerun = await service.execute()
            rerun_by_name = {p["policy"]: p for p in rerun["policies"]}
            self.assertEqual(rerun_by_name["heartbeat_events"]["deleted"], 0)
            self.assertEqual(rerun_by_name["raw_measurements"]["deleted"], 0)
        finally:
            await engine.dispose()

    def test_consolidation_and_artifacts(self) -> None:
        asyncio.run(self._run_consolidation())

    async def _run_consolidation(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.artifacts import ArtifactStore
        from talos.awareness.db.session import build_engine
        from talos.awareness.memory.service import EvidenceRef, MemoryService
        from talos.awareness.registry.bootstrap import seed_registry

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            service = MemoryService(engine, self.settings)

            # three episodes in one incident scope
            for index in range(3):
                await service.write_deterministic(
                    statement=f"Incident overflow episode {index}",
                    memory_type="episodic",
                    scope="incident:overflow",
                    structured_content={"occurrence": index},
                    evidence=[EvidenceRef(kind="extraction_job", reference=f"e{index}")],
                    supersede_same_key=False,
                )
            # one weak inference eligible for decay (old, unaccessed, no user confirmation)
            weak = await service.write_deterministic(
                statement="The fan seems to run mostly at night.",
                scope="patterns",
                structured_content={},
                evidence=[EvidenceRef(kind="source", reference="inference")],
                confidence=0.5,
            )
            old = datetime.now(timezone.utc) - timedelta(days=5)
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text("UPDATE memories SET learned_at = :old WHERE memory_id = :m"),
                    {"old": old, "m": weak["memory_id"]},
                )

            report = await service.consolidate()
            self.assertEqual(len(report["summaries_created"]), 1)
            self.assertEqual(report["inferences_decayed"], 1)

            async with engine.connect() as connection:
                summary = (
                    await connection.execute(
                        sa.text(
                            "SELECT memory_id, statement FROM memories "
                            "WHERE structured_content ? 'consolidated_count'"
                        )
                    )
                ).one()
                links = (
                    await connection.execute(
                        sa.text(
                            "SELECT count(*) AS n FROM memory_relationships "
                            "WHERE from_memory_id = :m AND relation = 'derived_from'"
                        ),
                        {"m": summary.memory_id},
                    )
                ).one()
                decayed = (
                    await connection.execute(
                        sa.text("SELECT confidence FROM memories WHERE memory_id = :m"),
                        {"m": weak["memory_id"]},
                    )
                ).one()
            self.assertIn("occurred 3 times", summary.statement)
            self.assertEqual(links.n, 3)  # sources preserved and linked
            self.assertAlmostEqual(decayed.confidence, 0.4, places=5)

            # re-run is idempotent (summary content hash already live)
            rerun = await service.consolidate()
            self.assertEqual(rerun["summaries_created"], [])

            # --- artifact store: generated safe paths + checksums ----------------
            store = ArtifactStore(engine, self.settings)
            stored = await store.store(
                content=b"benchmark report",
                display_name="../../etc/passwd",  # hostile name is sanitized
                kind="report",
                mime_type="text/plain",
                provenance={"source": "test"},
            )
            self.assertNotIn("..", stored["relative_path"])
            loaded = await store.load(uuid.UUID(stored["artifact_id"]))
            self.assertIsNotNone(loaded)
            metadata, content = loaded
            self.assertEqual(content, b"benchmark report")
            self.assertTrue(metadata["checksum_ok"])
            root = Path(self.tempdir.name) / "artifacts"
            files = list(root.rglob("*"))
            self.assertTrue(all(f.resolve().is_relative_to(root.resolve()) for f in files))
        finally:
            await engine.dispose()

    def test_write_auth_enforced_when_token_configured(self) -> None:
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        # token configured: mutations require it, reads stay open
        secured = AwarenessSettings(
            _env_file=None,
            db_password=self.settings.db_password.get_secret_value(),
            db_host=self.settings.db_host,
            db_port=self.settings.db_port,
            db_user=self.settings.db_user,
            db_name=self.scratch_name,
            data_directory=Path(self.tempdir.name),
            mqtt_enabled=False,
            api_token="super-secret-token-1234",
        )
        app = create_app(secured)
        with TestClient(app) as client:
            body = {"statement": "auth test fact", "scope": "test"}
            denied = client.post("/memory/deterministic", json=body)
            self.assertEqual(denied.status_code, 401)
            allowed = client.post(
                "/memory/deterministic",
                json=body,
                headers={"Authorization": "Bearer super-secret-token-1234"},
            )
            self.assertEqual(allowed.status_code, 200)
            reads = client.get("/alerts")
            self.assertEqual(reads.status_code, 200)  # reads stay loopback-open
            # actions are fail-closed but honor the same token
            action_denied = client.post(
                "/actions/request", json={"action": "toggle_fan", "parameters": {"state": 1}}
            )
            self.assertEqual(action_denied.status_code, 401)

        # no token configured: non-physical writes allowed, actions disabled
        app_open = create_app(self.settings)
        with TestClient(app_open) as client:
            allowed = client.post(
                "/memory/deterministic",
                json={"statement": "open mode fact", "scope": "test"},
            )
            self.assertEqual(allowed.status_code, 200)
            disabled = client.post(
                "/actions/request", json={"action": "toggle_fan", "parameters": {"state": 1}}
            )
            self.assertEqual(disabled.status_code, 503)


if __name__ == "__main__":
    unittest.main()
