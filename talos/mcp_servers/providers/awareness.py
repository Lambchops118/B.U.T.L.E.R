"""Awareness read tools (C13, Phase 5): narrow, bounded, structured.

Thin HTTP calls to the awareness backend — no SQL, files, or MQTT. Routing
guidance lives in the docstrings so the model picks the structured source:
current facts → get_current_state; "when did X last happen" → recent events;
numeric periods → sensor history aggregates; trust/why → provenance/health.
Results carry freshness/confidence/source fields and truncation flags; a
partial result is never presented as complete history.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP

from talos.services import awareness_client

_MAX_HOURS = 24 * 31  # mirror the backend's 31-day range bound


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _window(hours: float) -> tuple[str, str]:
    hours = min(max(hours, 0.01), _MAX_HOURS)
    end = datetime.now(timezone.utc) + timedelta(minutes=5)
    start = end - timedelta(hours=hours, minutes=5)
    return start.isoformat(), end.isoformat()


def _call(path: str, params: dict | None = None) -> str:
    try:
        return _dumps(awareness_client.get_json(path, params))
    except RuntimeError as exc:
        return _dumps({"error": str(exc)})


def register(server: FastMCP) -> None:
    """Register the awareness subsystem's read tools on a FastMCP server."""

    @server.tool()
    def get_current_state(entity_id: str) -> str:
        """Get the CURRENT state of a device/entity (e.g. is the pump on, the
        latest temperature). Use this for present-tense facts — never guess
        and never use memory search. Each property includes status
        (current/stale/offline/conflicting), age, confidence, and source.
        Known entities include: fan, quad_pump, plant_pot_1, plant_pot_2,
        sim_greenhouse."""
        return _call(f"/state/{entity_id}")

    @server.tool()
    def get_recent_events(
        hours: float = 1.0,
        entity_id: str = "",
        event_type: str = "",
        limit: int = 20,
    ) -> str:
        """List recent events, newest first — use for "when did X last
        happen" or "what happened while I was away". Bounded: 'truncated'
        true means more events exist than the limit returned."""
        start, end = _window(hours)
        return _call(
            "/events",
            {
                "start": start,
                "end": end,
                "entity_id": entity_id,
                "event_type": event_type,
                "limit": max(1, min(limit, 100)),
            },
        )

    @server.tool()
    def get_sensor_history(
        entity_id: str,
        measurement: str,
        hours: float = 24.0,
        aggregation: str = "",
        max_points: int = 50,
    ) -> str:
        """Numeric sensor history — use for averages, minimums, maximums, or
        trends over a period ("average temperature yesterday"). aggregation
        may be '' (raw points), '1m', '1h', or '1d'; aggregates return
        min/max/avg/count/stddev per bucket."""
        start, end = _window(hours)
        return _call(
            f"/telemetry/{entity_id}/{measurement}",
            {
                "start": start,
                "end": end,
                "aggregation": aggregation or None,
                "max_points": max(1, min(max_points, 500)),
            },
        )

    @server.tool()
    def get_active_alerts() -> str:
        """List open/acknowledged alerts (incidents) with severity,
        occurrence counts, and timestamps. Use when asked about problems,
        warnings, or anything needing attention."""
        return _call("/alerts", {"limit": 50})

    @server.tool()
    def get_system_health() -> str:
        """Awareness backend component health: database, MQTT connection,
        freshness/outbox workers, rule policy. Use to check whether sensing
        infrastructure itself is working or degraded."""
        return _call("/health/components")

    @server.tool()
    def get_event_provenance(event_id: str) -> str:
        """Full provenance for one event id: who reported it, on which topic,
        clock trust, sequence/boot, correlation, and any linked alerts. Use
        for "why/how do we know this" and root-cause questions."""
        return _call(f"/provenance/{event_id}")

    @server.tool()
    def get_awareness_capabilities() -> str:
        """What the awareness subsystem can and cannot do right now
        (available / degraded / not_yet_implemented)."""
        return _call("/capabilities")
