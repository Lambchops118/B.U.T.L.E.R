"""Opt-in structured capture of payloads crossing the LLM boundary.

The launcher enables this for the main-agent child, consumes the prefixed JSON
records from stdout, and configures a local per-run JSONL transcript. Both sinks
are best effort so diagnostics can never change model-call behavior.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any


LLM_DEBUG_PREFIX = "TALOS_LLM_IO "
_emit_lock = threading.Lock()
_run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")


def _stdout_enabled() -> bool:
    return os.getenv("TALOS_LLM_DEBUG_STDOUT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def llm_debug_log_path() -> Path | None:
    """Return the configured per-process transcript path, if persistence is on."""

    directory = os.getenv("TALOS_LLM_DEBUG_LOG_DIR", "").strip()
    if not directory:
        return None
    return Path(directory) / f"llm_io_{_run_id}_{os.getpid()}.jsonl"


def llm_debug_enabled() -> bool:
    return _stdout_enabled() or llm_debug_log_path() is not None


def json_safe(value: Any) -> Any:
    """Return a lossless-as-practical JSON representation of SDK objects."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return json_safe(model_dump(mode="json"))
        except TypeError:
            return json_safe(model_dump())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return json_safe(to_dict())
    return repr(value)


def emit_llm_io(
    direction: str,
    payload: Any,
    *,
    api: str,
    operation: str = "completion",
) -> None:
    """Emit one machine-readable LLM boundary record when debugging is enabled."""

    if not llm_debug_enabled():
        return
    event = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        "direction": direction,
        "api": api,
        "operation": operation,
        "payload": json_safe(payload),
    }
    try:
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with _emit_lock:
            if _stdout_enabled():
                print(LLM_DEBUG_PREFIX + encoded, flush=True)
            log_path = llm_debug_log_path()
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(encoded + "\n")
    except Exception:
        # Debug capture must never change whether a model request succeeds.
        return
