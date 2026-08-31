"""Unit tests for registry seeding and the freshness deadline rule (no database).

These cover the quad-pump false-offline defect at the level that does not
need Postgres: the seeded expectations for each known source, the conditional
migrations that carry those expectations onto an already-booted database, and
the pure rule that decides whether silence means anything for a source.
"""

from __future__ import annotations

import unittest

try:
    from talos.awareness.registry.bootstrap import _SOURCE_MIGRATIONS, _SOURCES
    from talos.awareness.state.freshness import offline_deadline
except ImportError as exc:  # awareness deps live in .venv-awareness
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")


def _source(source_id: str) -> dict:
    return next(row for row in _SOURCES if row["source_id"] == source_id)


class OfflineDeadlineTest(unittest.TestCase):
    def test_unset_falls_back_to_the_configured_default(self) -> None:
        self.assertEqual(offline_deadline(None, 900.0), 900.0)

    def test_registry_value_overrides_the_default(self) -> None:
        self.assertEqual(offline_deadline(180.0, 900.0), 180.0)

    def test_zero_means_liveness_is_not_monitored(self) -> None:
        self.assertIsNone(offline_deadline(0, 900.0))
        self.assertIsNone(offline_deadline(-1, 900.0))


class SeededSourceExpectationsTest(unittest.TestCase):
    def test_legacy_pump_source_is_retired(self) -> None:
        legacy = _source("quad_pump_pico")
        self.assertFalse(legacy["enabled"])
        self.assertEqual(legacy["offline_after_seconds"], 0)

    def test_command_only_fan_is_not_liveness_monitored(self) -> None:
        self.assertEqual(_source("fan_pico")["offline_after_seconds"], 0)

    def test_canonical_pump_deadlines_follow_the_firmware_cadences(self) -> None:
        canonical = _source("quad_pump_canonical")
        # HEARTBEAT_INTERVAL_MS = 30 s, STATE_SNAPSHOT_INTERVAL_MS = 300 s
        self.assertEqual(canonical["offline_after_seconds"], 180)
        self.assertEqual(canonical["stale_after_seconds"], 900)
        self.assertTrue(canonical.get("enabled", True))

    def test_every_seeded_source_declares_its_liveness_expectation(self) -> None:
        for row in _SOURCES:
            if row["source_id"] == "sim_device":
                continue  # the simulator uses the configured defaults
            self.assertIn(
                "offline_after_seconds",
                row,
                f"{row['source_id']} must say whether its silence is evidence",
            )


class SourceMigrationsTest(unittest.TestCase):
    def test_each_migration_changes_the_value_it_expects(self) -> None:
        for migration in _SOURCE_MIGRATIONS:
            self.assertNotEqual(
                migration["expected"],
                migration["value"],
                f"no-op migration for {migration['source_id']}.{migration['column']}",
            )

    def test_migrations_match_the_seeded_values(self) -> None:
        """An already-booted database must converge on what a fresh one seeds."""
        for migration in _SOURCE_MIGRATIONS:
            if migration["column"] == "display_name":
                continue  # the seed keeps the original name; only old rows move
            seeded = _source(migration["source_id"])
            self.assertEqual(
                seeded[migration["column"]],
                migration["value"],
                f"{migration['source_id']}.{migration['column']} disagrees with the seed",
            )


if __name__ == "__main__":
    unittest.main()
