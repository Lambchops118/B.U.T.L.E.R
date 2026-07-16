"""Health endpoints: /health (summary) and /health/components (detail).

Both endpoints answer even when the database is down — with an honest
``unavailable`` status and HTTP 503 so probes and callers are never told a
broken system is fine (P8). The MQTT ingestion component is included when
ingestion runs in this process; a config-disabled component reports
``disabled`` and does not affect the aggregate.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from talos.awareness.health.service import (
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
                detail="disabled via TALOS_AWARENESS_MQTT_ENABLED=0",
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


def _rules_component(request: Request) -> list[ComponentStatus]:
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        return []
    return [ComponentStatus(name="rules", status=HEALTHY, detail="", data=engine.status())]


async def _extra_components(request: Request) -> list[ComponentStatus]:
    return (
        _mqtt_component(request)
        + _freshness_component(request)
        + await _outbox_component(request)
        + _rules_component(request)
    )


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
