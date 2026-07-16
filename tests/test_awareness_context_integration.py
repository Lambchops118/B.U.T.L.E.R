"""Phase 5 integration: situation snapshot, provenance, capabilities API.

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait

Exercises the real API surface with the FastAPI TestClient (lifespan runs
with MQTT disabled) against a scratch database populated through the real
ingestion pipeline: critical alerts always survive tiny budgets, unrelated
data is excluded under pressure, every model-visible fact carries temporal
qualification, and provenance/capability endpoints answer truthfully.
"""

from __future__ import annotations

import asyncio
import json
import unittest
import uuid

try:
    from talos.awareness.config import AwarenessSettings, SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


class ContextIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"talos_awareness_ctx_{uuid.uuid4().hex[:8]}"
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
        asyncio.run(self._populate())

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

    async def _populate(self) -> None:
        """Ingest telemetry + a critical overflow through the real pipeline."""
        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.session import build_engine
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.registry.sources import SourceRepository
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
                body = {"event_id": str(uuid.uuid4()), "sequence": sequence, "boot_id": boot, **body}
                result = await pipeline.handle(
                    InboundMessage(topic=topic, payload=json.dumps(body).encode())
                )
                assert result == "accepted", result

            await publish(
                "home/sim/greenhouse/telemetry/temperature", {"value": 71.2, "unit": "F"}, 1
            )
            await publish(
                "home/sim/greenhouse/event",
                {
                    "event_type": "plant.overflow.detected",
                    "severity": "critical",
                    "payload": {"overflow": True, "zone": 1},
                },
                2,
            )
        finally:
            await engine.dispose()

    def test_situation_provenance_capabilities(self) -> None:
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app

        app = create_app(self.settings)
        with TestClient(app) as client:
            # --- full budget: state + alert, all temporally qualified --------
            response = client.get("/situation")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertIn("as_of", payload)
            self.assertIn("ALERT[critical] Overflow detected", payload["text"])
            self.assertIn("STATE sim_greenhouse.temperature", payload["text"])
            self.assertIn("received", payload["text"])  # temporal qualification
            self.assertIn("src sim_device", payload["text"])  # source
            self.assertLessEqual(payload["used_tokens"], payload["budget_tokens"])
            self.assertTrue(payload["audit"])

            # --- tiny budget: critical alert survives, the rest is dropped ---
            response = client.get("/situation", params={"budget_tokens": 50})
            payload = response.json()
            self.assertIn("ALERT[critical]", payload["text"])
            self.assertNotIn("STATE ", payload["text"])
            self.assertTrue(payload["truncated"])
            dropped = [a for a in payload["audit"] if not a["included"]]
            self.assertTrue(dropped)
            self.assertTrue(all(a["reason"] == "budget_exceeded" for a in dropped))

            # --- out-of-range budget rejected ---------------------------------
            self.assertEqual(
                client.get("/situation", params={"budget_tokens": 10}).status_code, 422
            )

            # --- provenance route ---------------------------------------------
            events = client.get(
                "/events",
                params={
                    "start": "2020-01-01T00:00:00+00:00",
                    "end": "2030-01-01T00:00:00+00:00",
                },
            )
            self.assertEqual(events.status_code, 422)  # unbounded range rejected
            alerts = client.get("/alerts").json()["alerts"]
            self.assertEqual(len(alerts), 1)
            deliveries_alert = alerts[0]["alert_id"]
            evidence = client.get(f"/alerts/{deliveries_alert}/deliveries")
            self.assertEqual(evidence.status_code, 200)

            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            events = client.get(
                "/events",
                params={
                    "start": (now - timedelta(hours=1)).isoformat(),
                    "end": (now + timedelta(minutes=5)).isoformat(),
                    "event_type": "plant.overflow.detected",
                },
            ).json()
            event_id = events["events"][0]["event_id"]
            provenance = client.get(f"/provenance/{event_id}")
            self.assertEqual(provenance.status_code, 200)
            body = provenance.json()
            self.assertEqual(body["source_id"], "sim_device")
            self.assertIn("clock_quality", body["provenance"])
            self.assertEqual(len(body["linked_alerts"]), 1)
            self.assertEqual(
                client.get(f"/provenance/{uuid.uuid4()}").status_code, 404
            )

            # --- capabilities are truthful ------------------------------------
            capabilities = client.get("/capabilities").json()["capabilities"]
            self.assertEqual(capabilities["get_current_state"], "available")
            self.assertIn("available", capabilities["search_memory"])
            self.assertEqual(capabilities["request_device_action"], "available")


if __name__ == "__main__":
    unittest.main()
