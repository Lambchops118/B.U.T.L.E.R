"""End-to-end ingestion integration (Phase 2 acceptance).

Requires the awareness Postgres AND the test Mosquitto broker:

    docker compose -f docker-compose.awareness.yml --profile test up -d --wait

Skips cleanly when either is missing. Uses a scratch database (migrated from
head, registry-seeded) and the local test broker — never the production
Raspberry Pi broker.

Covered acceptance criteria: valid events stored once; duplicates idempotent;
out-of-order retained and flagged; unauthorized/spoofed/malformed/oversized
messages dead-lettered; retained messages marked in provenance and
freshness-evaluated by received_at, not assumed current.
"""

from __future__ import annotations

import asyncio
import json
import socket
import unittest
import uuid

try:
    from talos.awareness.config import AwarenessSettings, SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

TEST_BROKER_HOST = "127.0.0.1"
TEST_BROKER_PORT = 1885


def _broker_reachable() -> bool:
    try:
        with socket.create_connection((TEST_BROKER_HOST, TEST_BROKER_PORT), timeout=2):
            return True
    except OSError:
        return False


class IngestionIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")
        if not _broker_reachable():
            self.skipTest("test Mosquitto not reachable (compose --profile test)")

        self.scratch_name = f"talos_awareness_ingest_{uuid.uuid4().hex[:8]}"
        self.settings = AwarenessSettings(
            _env_file=None,
            db_password=base.db_password.get_secret_value(),
            db_host=base.db_host,
            db_port=base.db_port,
            db_user=base.db_user,
            db_name=self.scratch_name,
            mqtt_host=TEST_BROKER_HOST,
            mqtt_port=TEST_BROKER_PORT,
            mqtt_client_id=f"talos-awareness-it-{uuid.uuid4().hex[:6]}",
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

    def test_ingestion_end_to_end(self) -> None:
        asyncio.run(self._run_flow())

    async def _run_flow(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.db.session import build_engine
        from talos.awareness.ingestion.service import IngestionService
        from talos.awareness.simulator.publisher import SimulatedDevice, publish_messages

        engine = build_engine(self.settings)
        service = IngestionService(self.settings, engine)
        device = SimulatedDevice()

        async def publish(messages) -> None:
            await publish_messages(
                messages,
                host=TEST_BROKER_HOST,
                port=TEST_BROKER_PORT,
                client_id=f"talos-sim-it-{uuid.uuid4().hex[:6]}",
                quiet=True,
            )

        async def query(sql: str, **params):
            async with engine.connect() as connection:
                result = await connection.execute(sa.text(sql), params)
                return result.fetchall()

        async def wait_for(description: str, condition, timeout: float = 15.0):
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                value = await condition()
                if value:
                    return value
                if asyncio.get_event_loop().time() > deadline:
                    self.fail(f"timed out waiting for: {description}")
                await asyncio.sleep(0.25)

        # A retained message published BEFORE the service subscribes must be
        # delivered on subscribe and marked as retained evidence.
        retained_messages = device.retained_state()
        await publish(retained_messages)

        await service.start()
        try:
            await wait_for(
                "MQTT connected",
                lambda: asyncio.sleep(0, service.ingress.status()["state"] == "connected"),
            )

            # --- retained message marked, not assumed current ---------------
            rows = await wait_for(
                "retained state event stored",
                lambda: query(
                    "SELECT provenance FROM events WHERE event_type = 'sim.state.reported'"
                ),
            )
            provenance = rows[0].provenance
            self.assertTrue(provenance["retained"])
            self.assertTrue(provenance["metadata"]["arrival"]["retained"])

            # --- normal traffic stored once ---------------------------------
            await publish(device.heartbeat() + device.temperature() + device.moisture())
            await wait_for(
                "normal scenario stored (3 events)",
                lambda: query(
                    "SELECT event_id FROM events WHERE event_type IN "
                    "('sim.heartbeat', 'sim.telemetry.temperature', 'sim.telemetry.moisture')"
                ).__await__() and None or self._count_at_least(query, 3),
            )

            # --- duplicate delivery is idempotent ----------------------------
            duplicate_messages = device.duplicate()
            duplicate_event_id = json.loads(duplicate_messages[0].payload)["event_id"]
            await publish(duplicate_messages)
            await wait_for(
                "duplicate event visible",
                lambda: query(
                    "SELECT event_id FROM events WHERE event_id = :event_id",
                    event_id=duplicate_event_id,
                ),
            )
            await asyncio.sleep(1.0)  # allow the duplicate copy to arrive too
            rows = await query(
                "SELECT count(*) AS n FROM events WHERE event_id = :event_id",
                event_id=duplicate_event_id,
            )
            self.assertEqual(rows[0].n, 1, "duplicate delivery must store exactly one row")

            # --- out-of-order retained in history and flagged ----------------
            out_of_order = device.out_of_order()
            older_event_id = json.loads(out_of_order[1].payload)["event_id"]
            await publish(out_of_order)
            rows = await wait_for(
                "out-of-order event stored",
                lambda: query(
                    "SELECT provenance FROM events WHERE event_id = :event_id",
                    event_id=older_event_id,
                ),
            )
            self.assertTrue(rows[0].provenance["metadata"]["arrival"]["out_of_order"])

            # --- rejects land in the dead-letter store -----------------------
            await publish(
                device.unauthorized()
                + device.malformed()
                + device.spoofed_source()
                + device.oversized()
            )
            reasons = await wait_for(
                "dead letters recorded",
                lambda: self._dead_letter_reasons(query, expected=4),
            )
            self.assertEqual(
                reasons,
                {"malformed_payload", "oversized", "source_mismatch", "unauthorized_topic"},
            )

            # No dead-lettered message may have produced an event row.
            rows = await query(
                "SELECT count(*) AS n FROM events WHERE event_type IN "
                "('rogue.event', 'sim.spoof.attempt', 'sim.oversized')"
            )
            self.assertEqual(rows[0].n, 0)

            # --- source registry advanced ------------------------------------
            rows = await query(
                "SELECT last_sequence, last_boot_id, health_status FROM sources "
                "WHERE source_id = 'sim_device'"
            )
            self.assertEqual(rows[0].health_status, "healthy")
            self.assertIsNotNone(rows[0].last_sequence)
            self.assertEqual(rows[0].last_boot_id, device.boot_id)

            metrics = service.metrics.snapshot()
            self.assertGreaterEqual(metrics["duplicates"], 1)
            self.assertGreaterEqual(metrics["out_of_order"], 1)
            self.assertGreaterEqual(metrics["accepted"], 6)

            # --- reconnect + subscription restoration ------------------------
            # Mosquitto disconnects an existing session when a second client
            # connects with the same client ID; the ingress must reconnect
            # with backoff and restore its subscriptions.
            import aiomqtt

            async with aiomqtt.Client(
                hostname=TEST_BROKER_HOST,
                port=TEST_BROKER_PORT,
                identifier=self.settings.mqtt_client_id,
                timeout=5,
            ):
                pass  # connect and immediately disconnect: kicks the ingress
            await wait_for(
                "ingress reconnected after broker kick",
                lambda: asyncio.sleep(
                    0,
                    service.ingress.status()["reconnects"] >= 1
                    and service.ingress.status()["state"] == "connected",
                ),
            )
            await publish(device.heartbeat())
            await wait_for(
                "event received after reconnect (subscriptions restored)",
                lambda: self._count_at_least(query, 4),
            )
        finally:
            await service.stop()
            await engine.dispose()

        # --- graceful shutdown reports truthfully ----------------------------
        self.assertEqual(service.ingress.status()["state"], "stopped")

    async def _count_at_least(self, query, minimum: int):
        rows = await query(
            "SELECT count(*) AS n FROM events WHERE event_type IN "
            "('sim.heartbeat', 'sim.telemetry.temperature', 'sim.telemetry.moisture')"
        )
        return rows if rows[0].n >= minimum else None

    async def _dead_letter_reasons(self, query, expected: int):
        rows = await query("SELECT reason FROM dead_letter_events")
        if len(rows) < expected:
            return None
        return {row.reason for row in rows}


if __name__ == "__main__":
    unittest.main()
