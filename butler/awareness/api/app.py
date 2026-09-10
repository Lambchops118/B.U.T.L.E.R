"""FastAPI application factory for the awareness backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

import asyncio

from butler.awareness import __version__
from butler.awareness.alerts.service import AlertService
from butler.awareness.actions.registry import load_registry
from butler.awareness.actions.service import ActionService
from butler.awareness.api.routes import actions as action_routes
from butler.awareness.api.routes import alerts as alert_routes
from butler.awareness.api.routes import context as context_routes
from butler.awareness.api.routes import health as health_routes
from butler.awareness.api.routes import ingest as ingest_routes
from butler.awareness.api.routes import memory as memory_routes
from butler.awareness.api.routes import reads as read_routes
from butler.awareness.api.routes import reminders as reminder_routes
from butler.awareness.api.routes import briefing as briefing_routes
from butler.awareness.briefing.worker import BriefingHandler, BriefingWorker
from butler.awareness.config import AwarenessSettings, load_settings
from butler.awareness.db.session import build_engine
from butler.awareness.health.service import HealthService
from butler.awareness.logging_utils import configure_logging, get_logger
from butler.awareness.notifications.adapters import build_adapters
from butler.awareness.memory.embeddings import EmbeddingHandler
from butler.awareness.memory.service import MemoryService
from butler.awareness.notifications.handler import NotificationHandler
from butler.awareness.outbox.worker import OutboxWorker
from butler.awareness.reminders.worker import ReminderWorker
from butler.awareness.rules.engine import RuleEngine
from butler.awareness.rules.policy import load_policy
from butler.awareness.state.freshness import FreshnessWorker


def create_app(settings: AwarenessSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    logger = get_logger("butler.awareness.api")

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

        action_registry = load_registry()
        action_service = ActionService(engine, settings, action_registry)
        app.state.action_service = action_service

        # One pipeline and one registry snapshot serve both ingress paths: the
        # MQTT broker and the internal /ingest endpoint. Building it here
        # rather than inside IngestionService means internal ingestion still
        # works with the broker disabled (BUTLER_AWARENESS_MQTT_ENABLED=0), and
        # that both paths share the same metrics and sequence state.
        from butler.awareness.ingestion.pipeline import IngestionPipeline
        from butler.awareness.registry.bootstrap import seed_registry
        from butler.awareness.registry.sources import SourceRepository

        sources = SourceRepository(engine)
        pipeline = IngestionPipeline(
            engine,
            sources,
            settings,
            rule_engine=rule_engine,
            action_service=action_service,
        )
        app.state.ingest_pipeline = pipeline
        try:
            await seed_registry(engine)
            await sources.refresh(force=True)
        except Exception:
            # Self-healing: the pipeline refreshes the registry on a TTL, so a
            # temporarily unreachable database only delays authorization.
            logger.exception(
                "registry bootstrap failed; API continues and will retry",
                extra={"component": "ingestion"},
            )

        if settings.mqtt_enabled:
            from butler.awareness.ingestion.service import IngestionService

            ingestion = IngestionService(
                settings,
                engine,
                rule_engine=rule_engine,
                action_service=action_service,
                pipeline=pipeline,
                sources=sources,
                seed_on_start=False,
            )
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

        async def publish_command(topic: str, body: bytes) -> None:
            from butler.awareness.ingestion.mqtt_client import build_mqtt_client

            async with build_mqtt_client(
                settings, identifier=f"{settings.mqtt_client_id}-actions"
            ) as client:
                await client.publish(topic, body, qos=1)

        outbox = OutboxWorker(
            engine,
            settings,
            {
                "notification": NotificationHandler(engine, adapters),
                "embedding": EmbeddingHandler(engine, settings),
                "memory_episode": episode_handler,
                "action_dispatch": action_service.dispatch_handler(publish_command),
                "action_timeout": action_service.timeout_handler,
            },
            exclude_work_types=("briefing",),
        )
        outbox_stop = asyncio.Event()
        outbox_task = asyncio.create_task(outbox.run(outbox_stop), name="awareness-outbox")
        app.state.outbox = outbox

        # Separate claim lane: a model timeout cannot hold up critical notifications.
        briefing_outbox = OutboxWorker(
            engine, settings.model_copy(update={"outbox_batch_size": 1}),
            {"briefing": BriefingHandler(engine, settings, adapters)},
            worker_id="awareness-briefing-1", work_types=("briefing",),
        )
        briefing_outbox_stop = asyncio.Event()
        briefing_outbox_task = asyncio.create_task(briefing_outbox.run(briefing_outbox_stop), name="awareness-briefing-outbox")
        app.state.briefing_outbox = briefing_outbox
        briefing_worker = BriefingWorker(engine, settings)
        briefing_stop = asyncio.Event()
        briefing_task = asyncio.create_task(briefing_worker.run(briefing_stop), name="awareness-briefing-moments")
        app.state.briefing_worker = briefing_worker

        reminder_worker = ReminderWorker(engine, settings, alerts)
        reminder_stop = asyncio.Event()
        reminder_task = asyncio.create_task(
            reminder_worker.run(reminder_stop), name="awareness-reminders"
        )
        app.state.reminder_worker = reminder_worker
        logger.info(
            "awareness API started; config: %s", settings.summary(), extra={"component": "api"}
        )
        try:
            yield
        finally:
            for stop_event, task in (
                (freshness_stop, freshness_task),
                (outbox_stop, outbox_task),
                (reminder_stop, reminder_task),
                (briefing_stop, briefing_task),
                (briefing_outbox_stop, briefing_outbox_task),
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

    app = FastAPI(title="Butler Awareness", version=__version__, lifespan=lifespan)
    app.include_router(health_routes.router)
    app.include_router(ingest_routes.router)
    app.include_router(read_routes.router)
    app.include_router(alert_routes.router)
    app.include_router(context_routes.router)
    app.include_router(memory_routes.router)
    app.include_router(action_routes.router)
    app.include_router(reminder_routes.router)
    app.include_router(briefing_routes.router)
    return app


async def _register_policy(engine, policy, logger) -> None:
    """Record the loaded rule-policy version in schema_registry (idempotent)."""
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from butler.awareness.db.models import SchemaRegistryEntry

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
