"""Main-agent → awareness signal emission (presence, interaction, agent outcomes).

The awareness backend was, until now, a device backend: it knew a great deal
about pumps and fans and nothing about the person in the room or about the
agent's own work. This module is the missing producer side. It reports three
kinds of fact over the loopback ``/ingest`` endpoint:

- **presence** — a wake word or a typed command is an observation that a
  person is here, from a source of known reliability. It is written as state
  on the ``owner`` entity and decays through the backend's existing freshness
  worker exactly like a sensor reading, so "detected 40 minutes ago" can never
  be mistaken for "here now".
- **interaction** — bounded facts about a conversation (started/ended,
  modality, turn count, duration, routing decision). Deliberately *not* the
  transcript: awareness stores what happened, not what was said. Utterance
  text would drown the situation broker's token budget and turn an event
  store into a chat log.
- **agent outcomes** — tool calls and background jobs that succeeded or
  failed, so "the thing you asked for twenty minutes ago failed" becomes
  something the system can surface on its own.

Three properties this module must never violate, because it sits on the voice
hot path:

1. **It never blocks the caller.** Emission is queued to one small daemon
   thread; the calling thread returns immediately.
2. **It never raises.** Every public function swallows its own errors. A
   failure to record that someone said hello must not stop them being
   answered.
3. **It is bounded.** The queue has a hard cap and drops the *oldest* signal
   when full, counting the drop. An awareness backend that is down or slow
   can never grow memory here without limit, and the drop count is reported
   rather than hidden (see :func:`stats`).

Stdlib only (``urllib`` via :mod:`talos.services.awareness_client`), because
this is imported from the main agent, the router, and the separate voice
worker process, which do not share a virtualenv.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from talos.services import awareness_client

# Topics owned by the ``talos_agent`` source in the awareness registry. That
# source is pinned to the "internal" transport, so these cannot be forged by
# anything publishing to the shared MQTT broker.
PRESENCE_TOPIC = "home/presence/owner/state"
INTERACTION_TOPIC = "home/interaction/owner/event"
AGENT_TOPIC = "home/agent/talos/event"

MAX_QUEUE_DEPTH = 256
# Presence is re-asserted at most this often; every command would otherwise
# write an identical state row on every turn of a rapid exchange.
PRESENCE_MIN_INTERVAL_SECONDS = 30.0

_queue: "queue.Queue[dict[str, Any]] | None" = None
_worker: threading.Thread | None = None
_lock = threading.Lock()
_last_presence_at: float = 0.0

_stats = {"queued": 0, "sent": 0, "failed": 0, "dropped": 0}
_stats_lock = threading.Lock()


def signals_enabled() -> bool:
    """Emission is on by default; set the env var to 0 to run read-only."""
    return os.getenv("TALOS_AWARENESS_SIGNALS_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def stats() -> dict[str, int]:
    """Truthful counters, including signals dropped rather than sent."""
    with _stats_lock:
        return dict(_stats)


def _bump(key: str) -> None:
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_worker() -> "queue.Queue[dict[str, Any]] | None":
    global _queue, _worker
    if not signals_enabled():
        return None
    with _lock:
        if _queue is None:
            _queue = queue.Queue(maxsize=MAX_QUEUE_DEPTH)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(
                target=_drain,
                name="awareness-signals",
                daemon=True,
            )
            _worker.start()
        return _queue


def _drain() -> None:
    while True:
        assert _queue is not None
        body = _queue.get()
        try:
            awareness_client.post_json("/ingest", body)
            _bump("sent")
        except Exception as exc:
            # Truthful degradation: the agent keeps working with no awareness
            # record, and says so in the log rather than pretending it landed.
            _bump("failed")
            print(f"[awareness-signals] could not record signal: {exc}")
        finally:
            _queue.task_done()


def _emit(topic: str, payload: dict[str, Any]) -> None:
    """Queue one signal. Never raises, never blocks."""
    try:
        work_queue = _ensure_worker()
        if work_queue is None:
            return
        body = {"topic": topic, "payload": payload, "transport": "internal"}
        try:
            work_queue.put_nowait(body)
            _bump("queued")
        except queue.Full:
            # Drop the oldest so the newest (most relevant) signal survives a
            # backend outage, and count it. Bounded by construction.
            try:
                work_queue.get_nowait()
                work_queue.task_done()
                _bump("dropped")
                work_queue.put_nowait(body)
                _bump("queued")
            except queue.Empty:
                _bump("dropped")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[awareness-signals] emission failed: {exc}")


def _envelope(payload: dict[str, Any], event_type: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "observed_at": _now_iso(),
    }
    if event_type is not None:
        body["event_type"] = event_type
    body.update(payload)
    return body


# --- presence ---------------------------------------------------------------


def record_presence(
    *,
    modality: str,
    confidence: float = 0.9,
    detail: str = "",
    force: bool = False,
) -> None:
    """Record that a person was just observed.

    ``modality`` is how they were observed ("voice", "text", "wake_word").
    Rate-limited so a rapid exchange does not write a state row per turn;
    pass ``force=True`` for a genuinely distinct detection such as a wake
    word.
    """
    global _last_presence_at
    try:
        now = time.monotonic()
        if not force and (now - _last_presence_at) < PRESENCE_MIN_INTERVAL_SECONDS:
            return
        _last_presence_at = now
        _emit(
            PRESENCE_TOPIC,
            _envelope(
                {
                    "present": True,
                    "modality": modality,
                    "detail": detail or modality,
                    "confidence": max(0.0, min(1.0, float(confidence))),
                }
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[awareness-signals] presence emission failed: {exc}")


# --- interaction ------------------------------------------------------------


def record_interaction_started(
    *,
    session_id: str,
    modality: str,
    source: str = "",
    routing_mode: str = "",
    entity_ids: list[str] | None = None,
) -> None:
    """A person addressed the system. Facts only — never the utterance text."""
    _emit(
        INTERACTION_TOPIC,
        _envelope(
            {
                "session_id": session_id,
                "modality": modality,
                "source": source or modality,
                "routing_mode": routing_mode,
                "entity_ids": entity_ids or [],
            },
            event_type="person.interaction.started",
        ),
    )


def record_interaction_ended(
    *,
    session_id: str,
    modality: str,
    duration_seconds: float,
    ok: bool = True,
    entity_ids: list[str] | None = None,
) -> None:
    """The exchange finished; how long it took and whether it succeeded."""
    _emit(
        INTERACTION_TOPIC,
        _envelope(
            {
                "session_id": session_id,
                "modality": modality,
                "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
                "ok": bool(ok),
                "entity_ids": entity_ids or [],
            },
            event_type="person.interaction.ended",
        ),
    )


# --- agent outcomes ---------------------------------------------------------


def record_agent_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    severity: str = "info",
    entity_ids: list[str] | None = None,
) -> None:
    """One bounded fact about the agent's own work.

    ``event_type`` must be one of the ``agent.*`` types (for example
    ``agent.tool.failed``, ``agent.job.completed``). Payload values should be
    scalars or small dictionaries; result text is never sent, only its shape.
    """
    body = dict(payload or {})
    body["entity_ids"] = entity_ids or []
    body["severity"] = severity
    _emit(AGENT_TOPIC, _envelope(body, event_type=event_type))
