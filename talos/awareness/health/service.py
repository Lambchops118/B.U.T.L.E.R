"""Truthful component health reporting (C16, Phase 1 scope).

Phase 1 components: database connectivity, required PostgreSQL extensions,
and schema migration currency. Later phases add MQTT, Ollama, workers,
notification adapters, and disk usage. A component that cannot be verified is
reported as such — never assumed healthy (P8).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.db.migrate import expected_head_revision

REQUIRED_EXTENSIONS = ("timescaledb", "vector")

HEALTHY = "healthy"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ComponentStatus:
    name: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail, "data": self.data}


def aggregate_status(components: list[ComponentStatus]) -> str:
    database = next((c for c in components if c.name == "database"), None)
    if database is not None and database.status == UNAVAILABLE:
        return UNAVAILABLE
    if any(component.status != HEALTHY for component in components):
        return DEGRADED
    return HEALTHY


def _error_detail(exc: Exception, limit: int = 300) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    return text if len(text) <= limit else text[: limit - 3] + "..."


class HealthService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def database(self) -> ComponentStatus:
        started = time.monotonic()
        try:
            async with self._engine.connect() as connection:
                await connection.execute(sa.text("SELECT 1"))
                version_row = await connection.execute(sa.text("SHOW server_version"))
                server_version = str(version_row.scalar_one())
        except Exception as exc:
            return ComponentStatus(
                name="database",
                status=UNAVAILABLE,
                detail=_error_detail(exc),
                data={"url": self._engine.url.render_as_string(hide_password=True)},
            )
        latency_ms = round((time.monotonic() - started) * 1000, 1)
        return ComponentStatus(
            name="database",
            status=HEALTHY,
            data={"server_version": server_version, "latency_ms": latency_ms},
        )

    async def extensions(self) -> ComponentStatus:
        try:
            async with self._engine.connect() as connection:
                rows = await connection.execute(
                    sa.text(
                        "SELECT extname, extversion FROM pg_extension "
                        "WHERE extname = ANY(:names)"
                    ),
                    {"names": list(REQUIRED_EXTENSIONS)},
                )
                installed = {row.extname: row.extversion for row in rows}
        except Exception as exc:
            return ComponentStatus(
                name="extensions", status=UNAVAILABLE, detail=_error_detail(exc)
            )

        missing = [name for name in REQUIRED_EXTENSIONS if name not in installed]
        if missing:
            return ComponentStatus(
                name="extensions",
                status=DEGRADED,
                detail=(
                    f"missing extensions: {', '.join(missing)} — run "
                    "'python -m talos.awareness migrate' (extensions are created by the "
                    "initial migration; the timescale/timescaledb-ha image ships both)"
                ),
                data={"installed": installed, "required": list(REQUIRED_EXTENSIONS)},
            )
        return ComponentStatus(name="extensions", status=HEALTHY, data={"installed": installed})

    async def migrations(self) -> ComponentStatus:
        head = expected_head_revision()
        try:
            async with self._engine.connect() as connection:
                has_table = await connection.execute(
                    sa.text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
                )
                if not has_table.scalar_one():
                    return ComponentStatus(
                        name="migrations",
                        status=DEGRADED,
                        detail=(
                            "database schema is uninitialized — run "
                            "'python -m talos.awareness migrate'"
                        ),
                        data={"current": None, "head": head},
                    )
                current_row = await connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                )
                current = current_row.scalar_one_or_none()
        except Exception as exc:
            return ComponentStatus(
                name="migrations", status=UNAVAILABLE, detail=_error_detail(exc)
            )

        if head is None:
            return ComponentStatus(
                name="migrations",
                status=DEGRADED,
                detail="no migration revisions found in the repository",
                data={"current": current, "head": None},
            )
        if current != head:
            return ComponentStatus(
                name="migrations",
                status=DEGRADED,
                detail=(
                    f"schema revision {current!r} != expected head {head!r} — run "
                    "'python -m talos.awareness migrate'"
                ),
                data={"current": current, "head": head},
            )
        return ComponentStatus(name="migrations", status=HEALTHY, data={"current": current, "head": head})

    async def report(self) -> dict[str, Any]:
        database = await self.database()
        if database.status == UNAVAILABLE:
            skipped = "not checked: database unreachable"
            components = [
                database,
                ComponentStatus(name="extensions", status=UNAVAILABLE, detail=skipped),
                ComponentStatus(name="migrations", status=UNAVAILABLE, detail=skipped),
            ]
        else:
            components = [database, await self.extensions(), await self.migrations()]

        return {
            "status": aggregate_status(components),
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "components": {component.name: component.to_dict() for component in components},
        }
