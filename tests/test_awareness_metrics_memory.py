from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.awareness.api.routes.health import _memory_counts


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _Connection:
    def __init__(self, fail: bool):
        self.fail = fail
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement):
        if self.fail:
            raise RuntimeError("database is down")
        self.calls += 1
        if self.calls == 1:
            newest = datetime(2026, 10, 3, tzinfo=timezone.utc)
            return _Result(rows=[
                SimpleNamespace(status="active", memory_type="semantic", n=2, newest=newest),
                SimpleNamespace(status="candidate", memory_type="semantic", n=1, newest=None),
            ])
        return _Result(scalar=5)


class _Engine:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def connect(self):
        return _Connection(self.fail)


def _request(engine):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))


class AwarenessMetricsMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_counts_by_status_without_statements(self) -> None:
        result = await _memory_counts(_request(_Engine()))

        self.assertEqual(result["total"], 3)
        self.assertEqual(result["provenance_rows"], 5)
        self.assertEqual(result["newest_created_at"], "2026-10-03T00:00:00+00:00")
        self.assertEqual(
            {(row["status"], row["count"]) for row in result["by_status"]},
            {("active", 2), ("candidate", 1)},
        )

    async def test_database_failure_is_an_error_not_zero(self) -> None:
        result = await _memory_counts(_request(_Engine(fail=True)))

        self.assertIn("error", result)
        self.assertNotIn("total", result)

    async def test_missing_engine_is_reported(self) -> None:
        result = await _memory_counts(_request(None))

        self.assertEqual(result, {"error": "database engine unavailable"})


if __name__ == "__main__":
    unittest.main()
