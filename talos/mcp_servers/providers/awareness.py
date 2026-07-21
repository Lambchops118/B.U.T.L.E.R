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


def _post(path: str, body: dict) -> dict:
    try:
        return awareness_client.post_json(path, body)
    except RuntimeError as exc:
        return {"error": str(exc)}


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
    def search_memory(
        query: str,
        limit: int = 10,
        memory_type: str = "",
        scope: str = "",
    ) -> str:
        """Search validated long-term memory (facts, preferences, past
        incidents) by meaning and keywords. Use for "what do you remember
        about X", past episodes, and user preferences — NOT for current
        device state or numeric history (use the state/history tools for
        exact questions). Results carry component scores, validity, and
        provenance-backed statements; superseded and deleted memories are
        excluded."""
        return _call(
            "/memory/search",
            {
                "query": query,
                "limit": max(1, min(limit, 25)),
                "memory_type": memory_type or None,
                "scope": scope or None,
            },
        )

    @server.tool()
    def request_device_action(
        action: str,
        parameters: str = "{}",
        correlation_id: str = "",
        idempotency_key: str = "",
    ) -> str:
        """Request a REGISTERED physical device action through the validated
        action service (never raw MQTT). `parameters` is a JSON object string,
        e.g. '{"pot_pin": 17}'. Supported actions: water_plants (pot_pin 17
        or 19), toggle_fan (state 0/1), sim_command (setting; requires
        confirmation). The response includes the action_request_id and
        status — 'awaiting_confirmation' means the user must approve before
        anything is dispatched; check progress with get_action_status.
        Supply and reuse `idempotency_key` when retrying the same user intent.
        Dispatch, acknowledgement, timeout, and completion are tracked
        truthfully: silence is never success."""
        try:
            parsed = json.loads(parameters or "{}")
        except json.JSONDecodeError as exc:
            return _dumps({"error": f"parameters must be a JSON object: {exc}"})
        if not isinstance(parsed, dict):
            return _dumps({"error": "parameters must be a JSON object"})
        try:
            return _dumps(
                awareness_client.post_json(
                    "/actions/request",
                    {
                        "action": action,
                        "parameters": parsed,
                        "actor": "llm",
                        "correlation_id": correlation_id or None,
                        "idempotency_key": idempotency_key or None,
                    },
                )
            )
        except RuntimeError as exc:
            return _dumps({"error": str(exc)})

    @server.tool()
    def get_action_status(action_request_id: str) -> str:
        """Check the status and full transition audit of one action request
        (requested/approved/dispatched/acknowledged/completed/failed/
        timed_out/cancelled)."""
        return _call(f"/actions/{action_request_id}")

    @server.tool()
    def set_reminder(text: str, due_at: str, entity_id: str = "") -> str:
        """Set a time-based reminder the system will SPEAK OUT LOUD on its own
        when it comes due — use whenever the user says "remind me…", "set a
        reminder", "at 7pm tell me…", or "in 20 minutes…". YOU convert the
        user's natural-language time into `due_at` as an absolute ISO 8601
        timestamp WITH a timezone offset (e.g. '2026-07-20T19:00:00-04:00' for
        7:00pm local); the current date/time is in your context. `text` is what
        to say (write it as the reminder content, e.g. "take the laundry out").
        The reminder is stored durably and fired by a deterministic clock, not
        by you. Returns the reminder_id and stored due_at. If the time is in the
        past or missing an offset the call is rejected — ask the user to
        clarify rather than guessing."""
        return _dumps(
            _post(
                "/reminders",
                {
                    "text": text,
                    "due_at": due_at,
                    "entity_id": entity_id or None,
                },
            )
        )

    @server.tool()
    def list_reminders(status: str = "scheduled", limit: int = 50) -> str:
        """List reminders, soonest due first. `status` filters by
        scheduled/fired/cancelled/expired (default scheduled = still pending).
        Use for "what reminders do I have" or before cancelling one."""
        return _call(
            "/reminders",
            {"status": status or None, "limit": max(1, min(limit, 200))},
        )

    @server.tool()
    def cancel_reminder(reminder_id: str) -> str:
        """Cancel a still-pending reminder by its reminder_id (from
        set_reminder or list_reminders). Only 'scheduled' reminders can be
        cancelled; returns an error if it already fired or does not exist."""
        return _dumps(_post(f"/reminders/{reminder_id}/cancel", {}))

    @server.tool()
    def get_awareness_capabilities() -> str:
        """What the awareness subsystem can and cannot do right now
        (available / degraded / not_yet_implemented)."""
        return _call("/capabilities")
