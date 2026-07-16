"""FastAPI application factory for the awareness backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from talos.awareness import __version__
from talos.awareness.api.routes import health as health_routes
from talos.awareness.config import AwarenessSettings, load_settings
from talos.awareness.db.session import build_engine
from talos.awareness.health.service import HealthService
from talos.awareness.logging_utils import configure_logging, get_logger


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
        logger.info(
            "awareness API started; config: %s", settings.summary(), extra={"component": "api"}
        )
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("awareness API stopped", extra={"component": "api"})

    app = FastAPI(title="TALOS Awareness", version=__version__, lifespan=lifespan)
    app.include_router(health_routes.router)
    return app
