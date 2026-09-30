"""Low-overhead structured telemetry for the speech/agent pipeline.

Each process writes its own JSONL file so the main and voice runtimes never
contend for a shared file handle. Events are correlated by ``request_id`` and
contain timings and configuration metadata only — never prompts, transcripts,
tool arguments, responses, credentials, or other user content.
"""

from __future__ import annotations

import json
import os
import hashlib
import platform
import socket
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from butler.config import env_bool


_ENABLED = env_bool("BUTLER_PIPELINE_TELEMETRY_ENABLED", True)
_LOG_DIR = Path(
    os.getenv(
        "BUTLER_PIPELINE_TELEMETRY_DIR",
        str(Path(__file__).resolve().parent / "logs"),
    )
)
_RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
_LOG_PATH = _LOG_DIR / f"pipeline_telemetry_{_RUN_ID}_{os.getpid()}.jsonl"
_LOCK = threading.Lock()
_BOOT_ID = os.getenv("BUTLER_BOOT_ID", uuid.uuid4().hex[:16])
_HOST_ID = os.getenv("BUTLER_HOST_ID", socket.gethostname())[:100]
_PROCESS_NAME = os.getenv("BUTLER_PROCESS_NAME", Path(sys.argv[0]).stem or "python")[:100]
_BUILD_SHA = os.getenv("BUTLER_BUILD_SHA", "unknown")[:100]
_RUNTIME_VERSION = f"python-{platform.python_version()}"

# Only operational, non-secret settings participate.  The digest is enough to
# correlate a regression with configuration drift without copying addresses,
# credentials, prompts, or user content into every telemetry event.
_CONFIG_FINGERPRINT_KEYS = (
    "BUTLER_LLM_BACKEND",
    "BUTLER_LLM_MODEL",
    "BUTLER_STT_BACKEND",
    "BUTLER_STT_MODEL",
    "BUTLER_MICROPHONE_PROFILE",
    "BUTLER_BARGE_IN_BACKEND",
    "BUTLER_BARGE_IN_ENABLED",
    "BUTLER_ASR_QUEUE_CAPACITY",
)


def _config_fingerprint() -> str:
    selected = {key: os.getenv(key, "") for key in _CONFIG_FINGERPRINT_KEYS}
    encoded = json.dumps(selected, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


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
        "schema_version": 2,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "trace_id": str(fields.pop("trace_id", request_id) or request_id or "unknown")[:100],
        "request_id": str(request_id or "unknown")[:100],
        "component": str(component or "unknown")[:100],
        "event": str(event or "unknown")[:100],
        "host_id": _HOST_ID,
        "process": _PROCESS_NAME,
        "pid": os.getpid(),
        "boot_id": _BOOT_ID,
        "build_sha": _BUILD_SHA,
        "runtime_version": _RUNTIME_VERSION,
        # Settings files are loaded after imports in some entry points, so this
        # must be evaluated at emission time rather than module import time.
        "config_fingerprint": _config_fingerprint(),
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
