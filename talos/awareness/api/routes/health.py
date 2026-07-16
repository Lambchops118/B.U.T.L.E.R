"""Health endpoints: /health (summary) and /health/components (detail).

Both endpoints answer even when the database is down — with an honest
``unavailable`` status and HTTP 503 so probes and callers are never told a
broken system is fine (P8).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from talos.awareness.health.service import UNAVAILABLE, HealthService

router = APIRouter()


def get_health_service(request: Request) -> HealthService:
    return request.app.state.health_service_factory()


@router.get("/health")
async def health(
    response: Response, service: HealthService = Depends(get_health_service)
) -> dict:
    report = await service.report()
    if report["status"] == UNAVAILABLE:
        response.status_code = 503
    return {"status": report["status"], "as_of": report["as_of"]}


@router.get("/health/components")
async def health_components(
    response: Response, service: HealthService = Depends(get_health_service)
) -> dict:
    report = await service.report()
    if report["status"] == UNAVAILABLE:
        response.status_code = 503
    return report
