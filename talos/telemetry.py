"""Low-overhead structured telemetry for the speech/agent pipeline.

Each process writes its own JSONL file so the main and voice runtimes never
contend for a shared file handle. Events are correlated by ``request_id`` and
contain timings and configuration metadata only — never prompts, transcripts,
tool arguments, responses, credentials, or other user content.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from talos.config import env_bool


_ENABLED = env_bool("TALOS_PIPELINE_TELEMETRY_ENABLED", True)
_LOG_DIR = Path(
    os.getenv(
        "TALOS_PIPELINE_TELEMETRY_DIR",
        str(Path(__file__).resolve().parent / "logs"),
    )
)
_RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
_LOG_PATH = _LOG_DIR / f"pipeline_telemetry_{_RUN_ID}_{os.getpid()}.jsonl"
_LOCK = threading.Lock()


def telemetry_log_path() -> Path:
    """Return this process's telemetry path (whether or not logging is enabled)."""
    return _LOG_PATH


def emit_pipeline_event(
    *,
    request_id: str,
    component: str,
    event: str,
    **fields: Any,
) -> None:
    """Append one bounded JSON event.

    Telemetry must never interrupt the voice path, so filesystem/serialization
    failures are deliberately contained. Callers should pass scalar values or
    small JSON-compatible dictionaries only.
    """
    if not _ENABLED:
        return

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "request_id": str(request_id or "unknown")[:100],
        "component": str(component or "unknown")[:100],
        "event": str(event or "unknown")[:100],
        **_safe_fields(fields),
    }
    try:
        line = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with _LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        # Observability must not become a new failure mode in the hot path.
        return


def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        name = str(key)[:100]
        if value is None or isinstance(value, (str, int, float, bool)):
            safe[name] = value
        elif isinstance(value, dict):
            safe[name] = {
                str(item_key)[:100]: item_value
                for item_key, item_value in list(value.items())[:50]
                if item_value is None
                or isinstance(item_value, (str, int, float, bool))
            }
        elif isinstance(value, (list, tuple)):
            safe[name] = [
                item
                for item in list(value)[:50]
                if item is None or isinstance(item, (str, int, float, bool))
            ]
        else:
            safe[name] = str(value)[:500]
    return safe
