"""Integration: durable reminders and the due-time worker.

Requires the awareness Postgres (skips cleanly when absent):

    docker compose -f docker-compose.awareness.yml up -d --wait

Covers: deterministic create validation (future + timezone-aware), idempotent
creation, cancellation, and the worker firing a due reminder into one attention
item plus one notification outbox row on the spoken ('voice') channel — the
same egress overflow alerts use — while marking the reminder 'fired' exactly
once (a second tick fires nothing).
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone

try:
    from talos.awareness.config import AwarenessSettings, SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


class RemindersIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            base = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        self.scratch_name = f"talos_awareness_reminders_{uuid.uuid4().hex[:8]}"
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

    def test_create_validation(self) -> None:
        asyncio.run(self._run_validation())

    async def _run_validation(self) -> None:
        from talos.awareness.db.session import build_engine
        from talos.awareness.reminders.service import ReminderService

        engine = build_engine(self.settings)
        try:
            service = ReminderService(engine, self.settings)
            now = datetime.now(timezone.utc)

            with self.assertRaises(ValueError):  # past
                await service.create(text="x", due_at=now - timedelta(minutes=1))
            with self.assertRaises(ValueError):  # naive (no tzinfo)
                await service.create(
                    text="x", due_at=(now + timedelta(hours=1)).replace(tzinfo=None)
                )
            with self.assertRaises(ValueError):  # empty text
                await service.create(text="   ", due_at=now + timedelta(hours=1))

            # idempotent create returns the same row for a repeated key
            first = await service.create(
                text="water the plants",
                due_at=now + timedelta(hours=1),
                idempotency_key="k1",
            )
            second = await service.create(
                text="water the plants",
                due_at=now + timedelta(hours=1),
                idempotency_key="k1",
            )
            self.assertEqual(first["reminder_id"], second["reminder_id"])
            self.assertEqual(first["status"], "scheduled")

            listing = await service.list(status="scheduled")
            self.assertEqual(listing["count"], 1)

            from uuid import UUID

            self.assertTrue(await service.cancel(UUID(first["reminder_id"])))
            self.assertFalse(await service.cancel(UUID(first["reminder_id"])))  # already cancelled
        finally:
            await engine.dispose()

    def test_worker_fires_due_reminder(self) -> None:
        asyncio.run(self._run_fire())

    async def _run_fire(self) -> None:
        import sqlalchemy as sa

        from talos.awareness.alerts.service import AlertService
        from talos.awareness.db.session import build_engine
        from talos.awareness.reminders.service import ReminderService
        from talos.awareness.reminders.worker import ReminderWorker

        engine = build_engine(self.settings)
        try:
            service = ReminderService(engine, self.settings)
            worker = ReminderWorker(engine, self.settings, AlertService(self.settings))

            async def query(sql: str, **params):
                async with engine.connect() as connection:
                    return (await connection.execute(sa.text(sql), params)).all()

            now = datetime.now(timezone.utc)
            created = await service.create(
                text="take the laundry out", due_at=now + timedelta(minutes=5)
            )

            # Not due yet: a tick at 'now' fires nothing.
            self.assertEqual(await worker.tick(now=now), 0)
            self.assertEqual(
                (await query("SELECT status FROM reminders"))[0].status, "scheduled"
            )

            # Due: one tick fires it once.
            fire_time = now + timedelta(minutes=6)
            self.assertEqual(await worker.tick(now=fire_time), 1)

            reminders = await query("SELECT status, attention_item_id FROM reminders")
            self.assertEqual(reminders[0].status, "fired")
            self.assertIsNotNone(reminders[0].attention_item_id)

            attention = await query("SELECT reason, preferred_channel FROM attention_items")
            self.assertEqual(len(attention), 1)
            self.assertEqual(attention[0].reason, "take the laundry out")
            self.assertEqual(attention[0].preferred_channel, "voice")

            outbox = await query(
                "SELECT payload, status FROM outbox WHERE work_type = 'notification'"
            )
            self.assertEqual(len(outbox), 1)
            self.assertEqual(outbox[0].payload["channel"], "voice")
            self.assertEqual(outbox[0].payload["reason"], "take the laundry out")

            # Idempotent: firing again does nothing (status already 'fired').
            self.assertEqual(await worker.tick(now=fire_time + timedelta(minutes=1)), 0)
            self.assertEqual(
                len(await query("SELECT 1 FROM attention_items")), 1
            )
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
