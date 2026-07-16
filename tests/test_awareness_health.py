"""Unit tests for health aggregation, endpoints, and structured logging."""

from __future__ import annotations

import io
import json
import logging
import os
import unittest
from unittest.mock import patch

try:
    import sqlalchemy  # noqa: F401  (awareness deps live in .venv-awareness)
except ImportError as exc:
    raise unittest.SkipTest(f"awareness dependencies not installed: {exc}")

from talos.awareness.health.service import (
    DEGRADED,
    HEALTHY,
    UNAVAILABLE,
    ComponentStatus,
    aggregate_status,
)
from talos.awareness.logging_utils import JsonLogFormatter, configure_logging


def _component(name: str, status: str) -> ComponentStatus:
    return ComponentStatus(name=name, status=status)


class AggregateStatusTest(unittest.TestCase):
    def test_all_healthy(self) -> None:
        components = [
            _component("database", HEALTHY),
            _component("extensions", HEALTHY),
            _component("migrations", HEALTHY),
        ]
        self.assertEqual(aggregate_status(components), HEALTHY)

    def test_database_down_is_unavailable(self) -> None:
        components = [
            _component("database", UNAVAILABLE),
            _component("extensions", UNAVAILABLE),
            _component("migrations", UNAVAILABLE),
        ]
        self.assertEqual(aggregate_status(components), UNAVAILABLE)

    def test_missing_extension_degrades(self) -> None:
        components = [
            _component("database", HEALTHY),
            _component("extensions", DEGRADED),
            _component("migrations", HEALTHY),
        ]
        self.assertEqual(aggregate_status(components), DEGRADED)

    def test_stale_migrations_degrade(self) -> None:
        components = [
            _component("database", HEALTHY),
            _component("extensions", HEALTHY),
            _component("migrations", DEGRADED),
        ]
        self.assertEqual(aggregate_status(components), DEGRADED)


class _StubHealthService:
    def __init__(self, status: str) -> None:
        self._status = status

    async def report(self, extra_components=None) -> dict:
        components = {"database": {"status": self._status, "detail": "", "data": {}}}
        for component in extra_components or []:
            components[component.name] = component.to_dict()
        return {
            "status": self._status,
            "as_of": "2026-07-15T12:00:00+00:00",
            "components": components,
        }


class HealthEndpointTest(unittest.TestCase):
    def _client(self, status: str):
        from fastapi.testclient import TestClient

        from talos.awareness.api.app import create_app
        from talos.awareness.api.routes.health import get_health_service
        from talos.awareness.config import load_settings

        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(_env_file=None, db_password="test-only", mqtt_enabled=False)
        app = create_app(settings)
        app.dependency_overrides[get_health_service] = lambda: _StubHealthService(status)
        return TestClient(app)

    def test_mqtt_component_reports_disabled_without_degrading(self) -> None:
        with self._client(HEALTHY) as client:
            response = client.get("/health/components")
        body = response.json()
        self.assertEqual(body["components"]["mqtt"]["status"], "disabled")
        self.assertEqual(body["status"], HEALTHY)

    def test_healthy_reports_200(self) -> None:
        with self._client(HEALTHY) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], HEALTHY)

    def test_unavailable_reports_503_truthfully(self) -> None:
        with self._client(UNAVAILABLE) as client:
            summary = client.get("/health")
            detail = client.get("/health/components")
        self.assertEqual(summary.status_code, 503)
        self.assertEqual(summary.json()["status"], UNAVAILABLE)
        self.assertEqual(detail.status_code, 503)
        self.assertIn("database", detail.json()["components"])

    def test_degraded_still_answers_200_with_status(self) -> None:
        with self._client(DEGRADED) as client:
            response = client.get("/health/components")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], DEGRADED)


class StructuredLoggingTest(unittest.TestCase):
    def test_json_line_includes_whitelisted_context_only(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("talos.awareness.test")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
        try:
            logger.info(
                "event stored",
                extra={"event_id": "abc-123", "source_id": "fan_pico", "api_key": "leaky"},
            )
        finally:
            logger.removeHandler(handler)

        record = json.loads(stream.getvalue())
        self.assertEqual(record["message"], "event stored")
        self.assertEqual(record["event_id"], "abc-123")
        self.assertEqual(record["source_id"], "fan_pico")
        self.assertNotIn("api_key", record)
        self.assertIn("ts", record)
        self.assertEqual(record["level"], "INFO")

    def test_configure_logging_is_idempotent(self) -> None:
        root = logging.getLogger()
        before = list(root.handlers)
        try:
            configure_logging("INFO")
            configure_logging("INFO")
            marked = [
                handler
                for handler in root.handlers
                if getattr(handler, "_talos_awareness_handler", False)
            ]
            self.assertEqual(len(marked), 1)
        finally:
            root.handlers = before


if __name__ == "__main__":
    unittest.main()
