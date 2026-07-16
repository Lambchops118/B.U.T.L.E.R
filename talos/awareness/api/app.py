"""FastAPI application factory for the awareness backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

import asyncio

from talos.awareness import __version__
from talos.awareness.alerts.service import AlertService
from talos.awareness.api.routes import alerts as alert_routes
from talos.awareness.api.routes import context as context_routes
from talos.awareness.api.routes import health as health_routes
from talos.awareness.api.routes import memory as memory_routes
from talos.awareness.api.routes import reads as read_routes
from talos.awareness.config import AwarenessSettings, load_settings
from talos.awareness.db.session import build_engine
from talos.awareness.health.service import HealthService
from talos.awareness.logging_utils import configure_logging, get_logger
from talos.awareness.notifications.adapters import build_adapters
from talos.awareness.memory.embeddings import EmbeddingHandler
from talos.awareness.memory.service import MemoryService
from talos.awareness.notifications.handler import NotificationHandler
from talos.awareness.outbox.worker import OutboxWorker
from talos.awareness.rules.engine import RuleEngine
from talos.awareness.rules.policy import load_policy
from talos.awareness.state.freshness import FreshnessWorker


def create_app(settings: AwarenessSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    logger = get_logger("talos.awareness.api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = build_engine(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.health_service_factory = lambda: HealthService(engine)
        app.state.ingestion = None

        policy = load_policy(settings.rules_path)
        alerts = AlertService(settings)
        rule_engine = RuleEngine(policy, alerts)
        app.state.rule_engine = rule_engine
        app.state.alert_service = alerts
        await _register_policy(engine, policy, logger)

        if settings.mqtt_enabled:
            from talos.awareness.ingestion.service import IngestionService

            ingestion = IngestionService(settings, engine, rule_engine=rule_engine)
            try:
                await ingestion.start()
                app.state.ingestion = ingestion
            except Exception:
                # Fail safe: the API (and its truthful health report) must come
                # up even when the registry bootstrap or broker is unreachable.
                logger.exception(
                    "ingestion failed to start; API continues without it",
                    extra={"component": "ingestion"},
                )
        async def offline_alert_hook(connection, transition: dict) -> None:
            if transition.get("kind") == "source_offline":
                await rule_engine.apply_source_offline(
                    connection,
                    source_id=transition["source_id"],
                    source_type=transition.get("source_type", ""),
                    silence_seconds=transition.get("silence_seconds", 0.0),
                )

        freshness = FreshnessWorker(engine, settings, alert_hook=offline_alert_hook)
        freshness_stop = asyncio.Event()
        freshness_task = asyncio.create_task(
            freshness.run(freshness_stop), name="awareness-freshness"
        )
        app.state.freshness = freshness

        adapters = build_adapters(settings)
        memory_service = MemoryService(engine, settings)

        async def episode_handler(payload: dict) -> None:
            from uuid import UUID as _UUID

            await memory_service.create_episode_from_alert(_UUID(payload["alert_id"]))

        outbox = OutboxWorker(
            engine,
            settings,
            {
                "notification": NotificationHandler(engine, adapters),
                "embedding": EmbeddingHandler(engine, settings),
                "memory_episode": episode_handler,
            },
        )
        outbox_stop = asyncio.Event()
        outbox_task = asyncio.create_task(outbox.run(outbox_stop), name="awareness-outbox")
        app.state.outbox = outbox
        logger.info(
            "awareness API started; config: %s", settings.summary(), extra={"component": "api"}
        )
        try:
            yield
        finally:
            for stop_event, task in (
                (freshness_stop, freshness_task),
                (outbox_stop, outbox_task),
            ):
                stop_event.set()
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if app.state.ingestion is not None:
                await app.state.ingestion.stop()
            await engine.dispose()
            logger.info("awareness API stopped", extra={"component": "api"})

    app = FastAPI(title="TALOS Awareness", version=__version__, lifespan=lifespan)
    app.include_router(health_routes.router)
    app.include_router(read_routes.router)
    app.include_router(alert_routes.router)
    app.include_router(context_routes.router)
    app.include_router(memory_routes.router)
    return app


async def _register_policy(engine, policy, logger) -> None:
    """Record the loaded rule-policy version in schema_registry (idempotent)."""
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from talos.awareness.db.models import SchemaRegistryEntry

    try:
        async with engine.begin() as connection:
            await connection.execute(
                pg_insert(SchemaRegistryEntry)
                .values(
                    kind="rule_policy",
                    name="rules",
                    version=policy.version,
                    definition=policy.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(
                    index_elements=["kind", "name", "version"]
                )
            )
    except Exception:
        # Startup must not fail on a registry write; health reports DB state.
        logger.exception(
            "failed to register rule policy version", extra={"component": "rules"}
        )
