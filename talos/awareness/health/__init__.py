"""Component health checks for the awareness subsystem."""

from talos.awareness.health.service import (
    DEGRADED,
    HEALTHY,
    UNAVAILABLE,
    ComponentStatus,
    HealthService,
    aggregate_status,
)

__all__ = [
    "DEGRADED",
    "HEALTHY",
    "UNAVAILABLE",
    "ComponentStatus",
    "HealthService",
    "aggregate_status",
]
