"""Integration test: a clean database is created from migrations (Phase 1).

Requires the awareness Postgres to be reachable (docker compose
-f docker-compose.awareness.yml up -d) and TALOS_AWARENESS_DB_PASSWORD to be
configured. Skips cleanly otherwise, so the unit suite never needs
infrastructure. No cloud services are involved.

The test creates a uniquely named scratch database, runs ``alembic upgrade
head`` against it, asserts extensions/tables/revision, verifies the migrated
schema matches ``models.py`` exactly (autogenerate diff must be empty), and
drops the scratch database afterwards.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from urllib.parse import quote_plus

try:
    from talos.awareness.config import SettingsError, load_settings
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

EXPECTED_TABLES = {
    "locations",
    "entities",
    "entity_relationships",
    "sources",
    "events",
    "dead_letter_events",
    "current_state",
    "alerts",
    "alert_events",
    "attention_items",
    "outbox",
    "schema_registry",
}


class MigrationIntegrationTest(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        try:
            self.settings = load_settings()
        except SettingsError as exc:
            self.skipTest(f"awareness settings unavailable: {exc}")

        try:
            import asyncpg  # noqa: F401
        except ImportError:
            self.skipTest("asyncpg not installed in this environment")

        self.scratch_name = f"talos_awareness_test_{uuid.uuid4().hex[:8]}"
        password = quote_plus(self.settings.db_password.get_secret_value())
        user = quote_plus(self.settings.db_user)
        host_port = f"{self.settings.db_host}:{self.settings.db_port}"
        self.admin_dsn = f"postgresql://{user}:{password}@{host_port}/postgres"
        self.scratch_url = f"postgresql+asyncpg://{user}:{password}@{host_port}/{self.scratch_name}"

        created = asyncio.run(self._create_scratch_database())
        if not created:
            self.skipTest("awareness Postgres is not reachable (start docker compose)")

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
        asyncio.run(self._drop_scratch_database())

    async def _drop_scratch_database(self) -> None:
        import asyncpg

        try:
            connection = await asyncpg.connect(self.admin_dsn, timeout=3)
        except Exception:
            return
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{self.scratch_name}" WITH (FORCE)')
        finally:
            await connection.close()

    def test_clean_database_from_migrations(self) -> None:
        from talos.awareness.db.migrate import expected_head_revision, upgrade_to_head

        upgrade_to_head(self.scratch_url)

        results = asyncio.run(self._inspect_schema())

        self.assertEqual(results["revision"], expected_head_revision())
        self.assertIn("timescaledb", results["extensions"])
        self.assertIn("vector", results["extensions"])
        missing_tables = EXPECTED_TABLES - results["tables"]
        self.assertFalse(missing_tables, f"missing tables: {sorted(missing_tables)}")
        self.assertEqual(
            results["metadata_diff"],
            [],
            "migrated schema differs from models.py — update the migration or the models",
        )
        self.assertIn(
            ("event_envelope", "core", 1),
            results["schema_registry_rows"],
            "initial migration should seed the envelope schema version",
        )

    async def _inspect_schema(self) -> dict:
        import sqlalchemy as sa
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from sqlalchemy.ext.asyncio import create_async_engine

        from talos.awareness.db.models import Base

        engine = create_async_engine(self.scratch_url)
        try:
            async with engine.connect() as connection:
                revision = (
                    await connection.execute(sa.text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                extensions = {
                    row.extname
                    for row in await connection.execute(
                        sa.text("SELECT extname FROM pg_extension")
                    )
                }
                tables = {
                    row.tablename
                    for row in await connection.execute(
                        sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                }
                registry_rows = [
                    (row.kind, row.name, row.version)
                    for row in await connection.execute(
                        sa.text("SELECT kind, name, version FROM schema_registry")
                    )
                ]

                def _diff(sync_connection):
                    context = MigrationContext.configure(
                        sync_connection, opts={"compare_type": True}
                    )
                    return compare_metadata(context, Base.metadata)

                metadata_diff = await connection.run_sync(_diff)
        finally:
            await engine.dispose()

        return {
            "revision": revision,
            "extensions": extensions,
            "tables": tables,
            "metadata_diff": metadata_diff,
            "schema_registry_rows": registry_rows,
        }


if __name__ == "__main__":
    unittest.main()
