"""Health endpoints: /health (summary) and /health/components (detail).

Both endpoints answer even when the database is down — with an honest
``unavailable`` status and HTTP 503 so probes and callers are never told a
broken system is fine (P8). The MQTT ingestion component is included when
ingestion runs in this process; a config-disabled component reports
``disabled`` and does not affect the aggregate.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from butler.awareness.health.service import (
    DEGRADED,
    DISABLED,
    HEALTHY,
    UNAVAILABLE,
    ComponentStatus,
    HealthService,
)

router = APIRouter()


def get_health_service(request: Request) -> HealthService:
    return request.app.state.health_service_factory()


def _mqtt_component(request: Request) -> list[ComponentStatus]:
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and not settings.mqtt_enabled:
        return [
            ComponentStatus(
                name="mqtt",
                status=DISABLED,
                detail="disabled via BUTLER_AWARENESS_MQTT_ENABLED=0",
            )
        ]
    ingestion = getattr(request.app.state, "ingestion", None)
    if ingestion is None:
        return []
    health = ingestion.health()
    state = health["connection"]["state"]
    if state == "connected":
        status, detail = HEALTHY, ""
    else:
        last_error = health["connection"].get("last_error")
        status = DEGRADED
        detail = f"MQTT {state}: {last_error or 'not yet connected'}"
    return [ComponentStatus(name="mqtt", status=status, detail=detail, data=health)]


def _freshness_component(request: Request) -> list[ComponentStatus]:
    worker = getattr(request.app.state, "freshness", None)
    if worker is None:
        return []
    data = worker.status()
    if data["state"] == "running" and data["last_error"] is None:
        status, detail = HEALTHY, ""
    else:
        status = DEGRADED
        detail = f"freshness worker {data['state']}: {data['last_error'] or ''}".strip()
    return [ComponentStatus(name="freshness_worker", status=status, detail=detail, data=data)]


async def _outbox_component(request: Request) -> list[ComponentStatus]:
    worker = getattr(request.app.state, "outbox", None)
    if worker is None:
        return []
    data = await worker.status()
    if data["state"] == "running" and data["last_error"] is None:
        status, detail = HEALTHY, ""
    else:
        status = DEGRADED
        detail = f"outbox worker {data['state']}: {data['last_error'] or ''}".strip()
    return [ComponentStatus(name="outbox_worker", status=status, detail=detail, data=data)]


def _reminder_component(request: Request) -> list[ComponentStatus]:
    worker = getattr(request.app.state, "reminder_worker", None)
    if worker is None:
        return []
    data = worker.status()
    if data["state"] == "running" and data["last_error"] is None:
        status, detail = HEALTHY, ""
    else:
        status = DEGRADED
        detail = f"reminder worker {data['state']}: {data['last_error'] or ''}".strip()
    return [ComponentStatus(name="reminder_worker", status=status, detail=detail, data=data)]


def _rules_component(request: Request) -> list[ComponentStatus]:
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        return []
    return [ComponentStatus(name="rules", status=HEALTHY, detail="", data=engine.status())]


async def _extra_components(request: Request) -> list[ComponentStatus]:
    briefing = getattr(request.app.state, "briefing_worker", None)
    briefing_outbox = getattr(request.app.state, "briefing_outbox", None)
    extra = []
    if briefing is not None:
        data = briefing.status()
        delivery = await briefing_outbox.status() if briefing_outbox else {}
        status = DISABLED if not data["enabled"] else (
            HEALTHY if data["state"] == "running" and not data["last_error"] and
            delivery.get("state") == "running" and not delivery.get("last_error") else DEGRADED)
        extra.append(ComponentStatus(name="briefings", status=status,
            detail=data["last_error"] or delivery.get("last_error") or "", data={"moments": data, "outbox": delivery}))
    return (
        _mqtt_component(request)
        + _freshness_component(request)
        + await _outbox_component(request)
        + _reminder_component(request)
        + _rules_component(request)
        + extra
    )


async def _memory_counts(request: Request) -> dict:
    """Long-term memory row counts for operators.

    Counts only -- never statements -- so the read-only debug console can show
    whether memory is being written at all without exposing its content. A
    database failure is reported as an error rather than as zero rows.
    """
    import sqlalchemy as sa

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return {"error": "database engine unavailable"}
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.text(
                        "SELECT status, memory_type, count(*) AS n, max(created_at) AS newest "
                        "FROM memories GROUP BY status, memory_type"
                    )
                )
            ).all()
            provenance = (
                await connection.execute(sa.text("SELECT count(*) FROM memory_provenance"))
            ).scalar_one()
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        return {"error": str(exc)[:200]}
    newest = max((row.newest for row in rows if row.newest is not None), default=None)
    return {
        "total": sum(int(row.n) for row in rows),
        "by_status": [
            {"status": row.status, "memory_type": row.memory_type, "count": int(row.n)}
            for row in rows
        ],
        "provenance_rows": int(provenance),
        "newest_created_at": newest.isoformat(timespec="seconds") if newest else None,
    }


@router.get("/metrics")
async def metrics(request: Request) -> dict:
    """Minimal internal metrics endpoint (FAIL-008): ingestion counters,
    worker/outbox state, disk usage of the data directory, last backup."""
    import shutil
    from datetime import datetime, timezone

    from butler.awareness.backup import latest_backup

    settings = request.app.state.settings
    data: dict = {"as_of": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    ingestion = getattr(request.app.state, "ingestion", None)
    if ingestion is not None:
        data["ingestion"] = ingestion.health()
    freshness = getattr(request.app.state, "freshness", None)
    if freshness is not None:
        data["freshness_worker"] = freshness.status()
    outbox = getattr(request.app.state, "outbox", None)
    if outbox is not None:
        data["outbox_worker"] = await outbox.status()
    reminder_worker = getattr(request.app.state, "reminder_worker", None)
    if reminder_worker is not None:
        data["reminder_worker"] = reminder_worker.status()
    briefing_worker = getattr(request.app.state, "briefing_worker", None)
    if briefing_worker is not None:
        data["briefing_worker"] = briefing_worker.status()
        data["briefing_outbox"] = await request.app.state.briefing_outbox.status()

    try:
        usage = shutil.disk_usage(settings.data_directory if settings.data_directory.exists() else ".")
        data["disk"] = {
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "free_percent": round(usage.free / usage.total * 100, 1),
        }
    except OSError:
        data["disk"] = {"error": "unavailable"}

    data["memory"] = await _memory_counts(request)

    dump = latest_backup(settings)
    data["last_backup"] = (
        {
            "file": dump.name,
            "modified_at": datetime.fromtimestamp(
                dump.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds"),
            "size_bytes": dump.stat().st_size,
        }
        if dump is not None
        else None
    )
    return data


@router.get("/health")
async def health(
    request: Request,
    response: Response,
    service: HealthService = Depends(get_health_service),
) -> dict:
    report = await service.report(extra_components=await _extra_components(request))
    if report["status"] == UNAVAILABLE:
        response.status_code = 503
    return {"status": report["status"], "as_of": report["as_of"]}


@router.get("/health/components")
async def health_components(
    request: Request,
    response: Response,
    service: HealthService = Depends(get_health_service),
) -> dict:
    report = await service.report(extra_components=await _extra_components(request))
    if report["status"] == UNAVAILABLE:
        response.status_code = 503
    return report
