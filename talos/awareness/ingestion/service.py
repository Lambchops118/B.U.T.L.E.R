"""Ingestion service: registry bootstrap + MQTT ingress + pipeline lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.ingestion.mqtt_client import MqttIngress
from talos.awareness.ingestion.pipeline import IngestionMetrics, IngestionPipeline
from talos.awareness.logging_utils import get_logger
from talos.awareness.registry.bootstrap import seed_registry
from talos.awareness.registry.sources import SourceRepository

logger = get_logger("talos.awareness.ingestion.service")

# Canonical scheme for new sources plus the deployed legacy status topics.
DEFAULT_SUBSCRIPTIONS = ("home/#", "status/#")


class IngestionService:
    def __init__(
        self,
        settings: AwarenessSettings,
        engine: AsyncEngine,
        rule_engine=None,
        action_service=None,
        *,
        pipeline: IngestionPipeline | None = None,
        sources: SourceRepository | None = None,
        seed_on_start: bool = True,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._seed_on_start = seed_on_start
        # An already-built pipeline may be supplied so the broker ingress and
        # the internal /ingest endpoint share one pipeline, one registry
        # snapshot, and one set of metrics. Standalone construction (tests,
        # benchmark) keeps the previous self-contained behavior.
        if pipeline is not None:
            self.pipeline = pipeline
            self.sources = sources if sources is not None else pipeline.sources
            self.metrics = pipeline.metrics
        else:
            self.metrics = IngestionMetrics()
            self.sources = sources if sources is not None else SourceRepository(engine)
            self.pipeline = IngestionPipeline(
                engine,
                self.sources,
                settings,
                self.metrics,
                rule_engine=rule_engine,
                action_service=action_service,
            )
        prefix = settings.mqtt_topic_prefix.strip("/")
        subscriptions = [
            f"{prefix}/{subscription}" if prefix else subscription
            for subscription in DEFAULT_SUBSCRIPTIONS
        ]
        self.ingress = MqttIngress(settings, self.pipeline.handle, subscriptions)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        try:
            if self._seed_on_start:
                await seed_registry(self._engine)
            await self.sources.refresh(force=True)
        except Exception:
            # Self-healing: the pipeline refreshes the registry on a TTL, so a
            # temporarily unreachable database only delays authorization.
            logger.exception(
                "registry bootstrap failed; ingestion starts anyway and will retry",
                extra={"component": "ingestion"},
            )
        logger.info(
            "ingestion starting; %d sources registered", self.sources.size,
            extra={"component": "ingestion"},
        )
        self._stop.clear()
        self._task = asyncio.create_task(self.ingress.run(self._stop), name="awareness-mqtt-ingress")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def health(self) -> dict[str, Any]:
        return {
            "connection": self.ingress.status(),
            "metrics": self.metrics.snapshot(),
            "sources_loaded": self.sources.size,
        }
