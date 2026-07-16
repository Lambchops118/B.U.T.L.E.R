"""Phase 7 integration: action lifecycle end to end (simulated devices).

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait

No broker is required: dispatch publishes through an injected fake publisher,
and acknowledgements/state evidence run through the real ingestion pipeline.
Per ADR-014 the physical Picos are not exercised — completion semantics are
proven against the simulator's command_ack and the legacy pin-status shape.
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


class ActionsIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"talos_awareness_act_{uuid.uuid4().hex[:8]}"
        self.settings = AwarenessSettings(
            _env_file=None,
            db_password=base.db_password.get_secret_value(),
            db_host=base.db_host,
            db_port=base.db_port,
            db_user=base.db_user,
            db_name=self.scratch_name,
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

    def test_action_lifecycle(self) -> None:
        asyncio.run(self._run_flow())

    async def _run_flow(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.actions.registry import load_registry
        from talos.awareness.actions.service import ActionService
        from talos.awareness.db.session import build_engine
        from talos.awareness.ingestion.pipeline import InboundMessage, IngestionPipeline
        from talos.awareness.outbox.worker import OutboxWorker
        from talos.awareness.registry.bootstrap import seed_registry
        from talos.awareness.registry.sources import SourceRepository

        engine = build_engine(self.settings)
        published: list[tuple[str, bytes]] = []

        async def fake_publish(topic: str, body: bytes) -> None:
            published.append((topic, body))

        try:
            await seed_registry(engine)
            sources = SourceRepository(engine)
            await sources.refresh(force=True)
            service = ActionService(engine, self.settings, load_registry())
            pipeline = IngestionPipeline(
                engine, sources, self.settings, action_service=service
            )
            worker = OutboxWorker(
                engine,
                self.settings,
                {
                    "action_dispatch": service.dispatch_handler(fake_publish),
                    "action_timeout": service.timeout_handler,
                },
            )

            async def query(sql: str, **params):
                async with engine.connect() as connection:
                    return (await connection.execute(sa.text(sql), params)).all()

            # --- unsupported action / parameters / actor rejected ------------
            result = await service.request(
                action_name="open_pod_bay_doors", parameters={}, actor="llm"
            )
            self.assertFalse(result["accepted"])
            self.assertIn("unsupported action", result["reason"])

            result = await service.request(
                action_name="water_plants", parameters={"pot_pin": 16}, actor="llm"
            )
            self.assertFalse(result["accepted"])
            self.assertIn("must be one of", result["reason"])

            result = await service.request(
                action_name="water_plants", parameters={"pot_pin": 17, "extra": 1}, actor="llm"
            )
            self.assertFalse(result["accepted"])
            self.assertIn("unsupported parameters", result["reason"])

            result = await service.request(
                action_name="water_plants", parameters={"pot_pin": 17}, actor="stranger"
            )
            self.assertFalse(result["accepted"])
            self.assertIn("not permitted", result["reason"])

            # --- legacy action: dispatch + state confirmation -----------------
            result = await service.request(
                action_name="water_plants", parameters={"pot_pin": 17}, actor="llm"
            )
            self.assertTrue(result["accepted"])
            request_id = result["action_request_id"]
            self.assertEqual(result["status"], "approved")
            self.assertEqual(published, [])  # durable intent, nothing sent yet

            self.assertEqual(await worker.run_once(), 1)
            self.assertEqual(published, [("quad_pump/17", b"1")])  # registered payload only
            detail = await service.get(uuid.UUID(request_id))
            self.assertEqual(detail["status"], "dispatched")

            # cooldown while the first request is live
            blocked = await service.request(
                action_name="water_plants", parameters={"pot_pin": 19}, actor="llm"
            )
            self.assertFalse(blocked["accepted"])
            self.assertIn("cooldown", blocked["reason"])

            # firmware reports the pin state -> completion via state evidence
            ingest_result = await pipeline.handle(
                InboundMessage(topic="status/17", payload=b"1")
            )
            self.assertEqual(ingest_result, "accepted")
            detail = await service.get(uuid.UUID(request_id))
            self.assertEqual(detail["status"], "completed")
            statuses = [t["to"] for t in detail["transitions"]]
            self.assertEqual(
                statuses, ["requested", "approved", "dispatched", "completed"]
            )

            # --- confirmation flow (simulator action) -------------------------
            result = await service.request(
                action_name="sim_command", parameters={"setting": 42}, actor="llm"
            )
            self.assertEqual(result["status"], "awaiting_confirmation")
            sim_id = uuid.UUID(result["action_request_id"])
            token = result["confirmation_token"]

            wrong = await service.confirm(sim_id, token="0" * 32, actor="llm")
            self.assertFalse(wrong["ok"])
            wrong_actor = await service.confirm(sim_id, token=token, actor="operator")
            self.assertFalse(wrong_actor["ok"])  # bound to the requesting actor
            confirmed = await service.confirm(sim_id, token=token, actor="llm")
            self.assertTrue(confirmed["ok"])

            self.assertEqual(await worker.run_once(), 1)
            topic, body = published[-1]
            self.assertEqual(topic, "home/sim/greenhouse/command")
            envelope = json.loads(body)
            self.assertEqual(envelope["action"], "sim_command")
            self.assertEqual(envelope["parameters"], {"setting": 42})
            command_id = envelope["command_id"]

            # duplicate dispatch attempt is a no-op (idempotent handler)
            self.assertEqual(await worker.run_once(), 0)

            # device acknowledges through the real ingestion path
            ack = await pipeline.handle(
                InboundMessage(
                    topic="home/sim/greenhouse/event",
                    payload=json.dumps(
                        {
                            "event_id": str(uuid.uuid4()),
                            "event_type": "sim.command_ack",
                            "payload": {"command_id": command_id, "ok": True},
                        }
                    ).encode(),
                )
            )
            self.assertEqual(ack, "accepted")
            detail = await service.get(sim_id)
            self.assertEqual(detail["status"], "completed")
            self.assertIsNotNone(detail["acknowledged_at"])

            # late duplicate ack is audited, status unchanged
            await pipeline.handle(
                InboundMessage(
                    topic="home/sim/greenhouse/event",
                    payload=json.dumps(
                        {
                            "event_id": str(uuid.uuid4()),
                            "event_type": "sim.command_ack",
                            "payload": {"command_id": command_id, "ok": True},
                        }
                    ).encode(),
                )
            )
            detail = await service.get(sim_id)
            self.assertEqual(detail["status"], "completed")
            self.assertIn("late acknowledgement", detail["transitions"][-1]["detail"])

            # --- timeout: silence is never success -----------------------------
            result = await service.request(
                action_name="sim_command", parameters={"setting": 7}, actor="llm"
            )
            timeout_id = uuid.UUID(result["action_request_id"])
            await service.confirm(timeout_id, token=result["confirmation_token"], actor="llm")
            self.assertEqual(await worker.run_once(), 1)  # dispatched
            # make the scheduled timeout work due now
            async with engine.begin() as connection:
                await connection.execute(
                    sa.text(
                        "UPDATE outbox SET available_at = now() - interval '1 second' "
                        "WHERE work_type = 'action_timeout' AND status = 'pending'"
                    )
                )
            self.assertGreaterEqual(await worker.run_once(), 1)
            detail = await service.get(timeout_id)
            self.assertEqual(detail["status"], "timed_out")

            # a late ack cannot revive the timed-out command
            late_body = json.loads(published[-1][1])
            await pipeline.handle(
                InboundMessage(
                    topic="home/sim/greenhouse/event",
                    payload=json.dumps(
                        {
                            "event_id": str(uuid.uuid4()),
                            "event_type": "sim.command_ack",
                            "payload": {"command_id": late_body["command_id"], "ok": True},
                        }
                    ).encode(),
                )
            )
            detail = await service.get(timeout_id)
            self.assertEqual(detail["status"], "timed_out")

            # --- negative acknowledgement fails truthfully ----------------------
            result = await service.request(
                action_name="sim_command", parameters={"setting": 8}, actor="llm"
            )
            fail_id = uuid.UUID(result["action_request_id"])
            await service.confirm(fail_id, token=result["confirmation_token"], actor="llm")
            await worker.run_once()
            fail_body = json.loads(published[-1][1])
            await pipeline.handle(
                InboundMessage(
                    topic="home/sim/greenhouse/event",
                    payload=json.dumps(
                        {
                            "event_id": str(uuid.uuid4()),
                            "event_type": "sim.command_ack",
                            "payload": {
                                "command_id": fail_body["command_id"],
                                "ok": False,
                                "error": "valve stuck",
                            },
                        }
                    ).encode(),
                )
            )
            detail = await service.get(fail_id)
            self.assertEqual(detail["status"], "failed")
            self.assertIn("valve stuck", detail["error"])

            # --- cancel before dispatch -----------------------------------------
            result = await service.request(
                action_name="sim_command", parameters={"setting": 9}, actor="llm"
            )
            cancel_id = uuid.UUID(result["action_request_id"])
            cancelled = await service.cancel(cancel_id, actor="operator")
            self.assertTrue(cancelled["ok"])
            expired_confirm = await service.confirm(
                cancel_id, token=result["confirmation_token"], actor="llm"
            )
            self.assertFalse(expired_confirm["ok"])

            # --- every transition durable and queryable --------------------------
            transitions = await query(
                "SELECT count(*) AS n FROM action_transitions"
            )
            self.assertGreaterEqual(transitions[0].n, 12)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
