"""Phase 4 integration: rules, alert lifecycle, outbox, notification delivery.

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait

Covered acceptance criteria: overflow stores event + opens one critical alert
and immediate attention; the notification outbox row commits with the event
(crash-safe by construction — it exists before any worker runs); the fallback
wording needs no Ollama; repeats update one incident without a second
notification inside the cooldown; adapter failure retries with backoff, falls
back to the next channel, and dead-letters after the attempt budget with
manual retry; an explicit all-clear auto-resolves; acknowledgement does not
erase the active condition; stale worker locks are reclaimed.
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


class _RecordingAdapter:
    confirmation_semantics = "test stub"

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.sent: list = []

    async def send(self, content):
        from talos.awareness.notifications.base import DeliveryResult

        self.sent.append(content)
        if self.fail:
            return DeliveryResult(confirmed=False, detail="stub failure")
        return DeliveryResult(confirmed=True, detail="stub delivered")


class AlertsIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"talos_awareness_alerts_{uuid.uuid4().hex[:8]}"
        self.settings = AwarenessSettings(
            _env_file=None,
            db_password=base.db_password.get_secret_value(),
            db_host=base.db_host,
            db_port=base.db_port,
            db_user=base.db_user,
            db_name=self.scratch_name,
            outbox_max_attempts=2,
            outbox_stale_lock_seconds=60.0,
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

    def test_overflow_end_to_end(self) -> None:
        asyncio.run(self._run_flow())

    async def _run_flow(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.session import build_engine
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.notifications.handler import NotificationHandler
        from talos.awareness.outbox.worker import OutboxWorker, retry_outbox_item
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.registry.sources import SourceRepository
        from talos.awareness.rules.engine import RuleEngine
        from talos.awareness.rules.policy import load_policy

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            sources = SourceRepository(engine)
            await sources.refresh(force=True)
            rule_engine = RuleEngine(load_policy(), AlertService(self.settings))
            pipeline = IngestionPipeline(
                engine, sources, self.settings, rule_engine=rule_engine
            )
            boot_id = f"boot-{uuid.uuid4().hex[:8]}"
            sequence = 0

            async def overflow(value: bool) -> str:
                nonlocal sequence
                sequence += 1
                return await pipeline.handle(
                    InboundMessage(
                        topic="home/sim/greenhouse/event",
                        payload=json.dumps(
                            {
                                "event_id": str(uuid.uuid4()),
                                "event_type": "plant.overflow.detected",
                                "severity": "critical",
                                "sequence": sequence,
                                "boot_id": boot_id,
                                "payload": {"overflow": value, "zone": 1},
                            }
                        ).encode(),
                    )
                )

            async def query(sql: str, **params):
                async with engine.connect() as connection:
                    return (await connection.execute(sa.text(sql), params)).all()

            # --- overflow: one alert, immediate attention, queued outbox -----
            self.assertEqual(await overflow(True), "accepted")
            alerts = await query("SELECT * FROM alerts")
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0].severity, "critical")
            self.assertEqual(alerts[0].status, "open")
            self.assertEqual(alerts[0].occurrence_count, 1)
            attention = await query("SELECT * FROM attention_items")
            self.assertEqual(len(attention), 1)
            self.assertEqual(attention[0].interruptibility, "immediate")
            outbox = await query("SELECT * FROM outbox WHERE work_type = 'notification'")
            self.assertEqual(len(outbox), 1)  # committed with the event: crash-safe
            self.assertEqual(outbox[0].status, "pending")
            evidence = await query("SELECT count(*) AS n FROM alert_events")
            self.assertEqual(evidence[0].n, 1)

            # --- repeat: same incident, occurrence up, cooldown suppresses ---
            self.assertEqual(await overflow(True), "accepted")
            alerts = await query("SELECT * FROM alerts")
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0].occurrence_count, 2)
            attention = await query("SELECT * FROM attention_items")
            self.assertEqual(len(attention), 1)  # cooldown: no interruption spam
            outbox = await query("SELECT * FROM outbox WHERE work_type = 'notification'")
            self.assertEqual(len(outbox), 1)
            evidence = await query("SELECT count(*) AS n FROM alert_events")
            self.assertEqual(evidence[0].n, 2)  # but evidence accumulates

            # --- delivery: preferred channel fails, fallback confirms --------
            gui = _RecordingAdapter("gui", fail=True)
            log = _RecordingAdapter("log")
            worker = OutboxWorker(
                engine,
                self.settings,
                {"notification": NotificationHandler(engine, {"gui": gui, "log": log})},
            )
            self.assertEqual(await worker.run_once(), 1)
            self.assertEqual(len(gui.sent), 1)  # preferred tried first
            self.assertEqual(len(log.sent), 1)  # fallback succeeded
            self.assertIn("[CRITICAL]", log.sent[0].title)
            self.assertIn("Occurred 2 times.", log.sent[0].body)
            deliveries = await query(
                "SELECT channel, status FROM notification_deliveries ORDER BY id"
            )
            self.assertEqual(
                [(row.channel, row.status) for row in deliveries],
                [("gui", "failed"), ("log", "delivered")],
            )
            outbox = await query("SELECT status FROM outbox WHERE work_type = 'notification'")
            self.assertEqual(outbox[0].status, "completed")
            attention = await query("SELECT delivery_status FROM attention_items")
            self.assertEqual(attention[0].delivery_status, "delivered")

            # --- total failure: bounded retries then dead-letter + manual ----
            async with engine.begin() as connection:
                await rule_engine._alerts.raise_attention(
                    connection,
                    alert_id=None,
                    entity_id=None,
                    severity="warning",
                    reason="second notification",
                    priority=5,
                    interruptibility="next_interaction",
                    preferred_channel="gui",
                    available_after_seconds=0.0,
                    expires_after_seconds=None,
                    cooldown_key=None,
                    cooldown_seconds=0.0,
                    notify=True,
                    notification_payload={
                        "severity": "warning",
                        "reason": "second notification",
                    },
                    now=datetime.now(timezone.utc),
                )
            failing = _RecordingAdapter("gui", fail=True)
            worker_fail = OutboxWorker(
                engine,
                self.settings,
                {"notification": NotificationHandler(engine, {"gui": failing})},
            )
            self.assertEqual(await worker_fail.run_once(), 1)  # attempt 1 → backoff
            rows = await query(
                "SELECT status, attempt_count, next_attempt_at FROM outbox "
                "WHERE status != 'completed'"
            )
            self.assertEqual(rows[0].status, "pending")
            self.assertEqual(rows[0].attempt_count, 1)
            self.assertIsNotNone(rows[0].next_attempt_at)

            # make it due now and simulate a stale lock from a crashed worker
            await self._force_due(engine)
            self.assertEqual(await worker_fail.run_once(), 1)  # attempt 2 → dead letter
            rows = await query("SELECT outbox_id, status FROM outbox WHERE status = 'dead_letter'")
            self.assertEqual(len(rows), 1)
            self.assertTrue(await retry_outbox_item(engine, rows[0].outbox_id))
            rows = await query(
                "SELECT status FROM outbox WHERE outbox_id = :i", i=rows[0].outbox_id
            )
            self.assertEqual(rows[0].status, "pending")

            # --- acknowledge keeps the condition active ----------------------
            service = AlertService(self.settings)
            alert_id = (await query("SELECT alert_id FROM alerts"))[0].alert_id
            self.assertTrue(await service.set_status(engine, alert_id, "acknowledged"))
            self.assertEqual(await overflow(True), "accepted")
            alerts = await query("SELECT status, occurrence_count FROM alerts")
            self.assertEqual(alerts[0].status, "acknowledged")  # still one incident
            self.assertEqual(alerts[0].occurrence_count, 3)

            # --- deterministic auto-resolution -------------------------------
            self.assertEqual(await overflow(False), "accepted")
            alerts = await query(
                "SELECT status, resolved_at, metadata FROM alerts"
            )
            self.assertEqual(alerts[0].status, "resolved")
            self.assertIsNotNone(alerts[0].resolved_at)
            self.assertIn("overflow-resolved", alerts[0].metadata["resolution"])

            # --- a fresh overflow after resolution opens a NEW incident ------
            self.assertEqual(await overflow(True), "accepted")
            alerts = await query("SELECT count(*) AS n FROM alerts")
            self.assertEqual(alerts[0].n, 2)
        finally:
            await engine.dispose()

    def test_source_offline_alert_lifecycle(self) -> None:
        asyncio.run(self._run_offline_flow())

    async def _run_offline_flow(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.session import build_engine
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.registry.sources import SourceRepository
        from talos.awareness.rules.engine import RuleEngine
        from talos.awareness.rules.policy import load_policy
        from talos.awareness.state.freshness import FreshnessWorker

        engine = build_engine(self.settings)
        try:
            await seed_registry(engine)
            sources = SourceRepository(engine)
            await sources.refresh(force=True)
            rule_engine = RuleEngine(load_policy(), AlertService(self.settings))
            pipeline = IngestionPipeline(
                engine, sources, self.settings, rule_engine=rule_engine
            )

            async def offline_hook(connection, transition):
                if transition["kind"] == "source_offline":
                    await rule_engine.apply_source_offline(
                        connection,
                        source_id=transition["source_id"],
                        source_type=transition.get("source_type", ""),
                        silence_seconds=transition["silence_seconds"],
                    )

            worker = FreshnessWorker(engine, self.settings, alert_hook=offline_hook)

            async def heartbeat() -> str:
                return await pipeline.handle(
                    InboundMessage(
                        topic="home/sim/greenhouse/heartbeat",
                        payload=json.dumps({"event_id": str(uuid.uuid4())}).encode(),
                    )
                )

            async def query(sql: str, **params):
                async with engine.connect() as connection:
                    return (await connection.execute(sa.text(sql), params)).all()

            self.assertEqual(await heartbeat(), "accepted")

            # Silence beyond the offline deadline opens a warning incident.
            later = datetime.now(timezone.utc) + timedelta(
                seconds=self.settings.default_offline_after_seconds + 60
            )
            self.assertGreaterEqual(await worker.tick(now=later), 1)
            alerts = await query(
                "SELECT alert_type, severity, status FROM alerts "
                "WHERE alert_type = 'source_offline'"
            )
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0].severity, "warning")  # silence ≠ critical
            self.assertEqual(alerts[0].status, "open")
            attention = await query(
                "SELECT interruptibility FROM attention_items "
                "WHERE reason LIKE 'Source sim_device%'"
            )
            self.assertEqual(len(attention), 1)
            self.assertEqual(attention[0].interruptibility, "next_interaction")

            # A new message recovers the source and auto-resolves the incident.
            self.assertEqual(await heartbeat(), "accepted")
            alerts = await query(
                "SELECT status FROM alerts WHERE alert_type = 'source_offline'"
            )
            self.assertEqual(alerts[0].status, "resolved")
        finally:
            await engine.dispose()

    async def _force_due(self, engine) -> None:
        import sqlalchemy as sa

        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        stale = datetime.now(timezone.utc) - timedelta(seconds=3600)
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox SET next_attempt_at = :past, locked_at = :stale "
                    "WHERE status = 'pending'"
                ),
                {"past": past, "stale": stale},
            )


if __name__ == "__main__":
    unittest.main()
