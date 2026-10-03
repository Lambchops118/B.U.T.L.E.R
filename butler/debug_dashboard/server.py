"""Small read-only HTTP server for the Butler debug console.

The dashboard deliberately reads existing telemetry and conversation artifacts
instead of joining the agent or audio hot paths. A slow browser, failed probe,
or malformed old log therefore cannot interrupt an interaction.
"""

from __future__ import annotations

import argparse
import csv
import concurrent.futures
import ctypes
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from butler.config import load_environment
from butler.debug_dashboard.insights import (
    read_llm_round_stats,
    read_memory_store_stats,
    read_ollama_placement,
    read_prompt_composition,
)


PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PACKAGE_ROOT / "static"
REPO_ROOT = PACKAGE_ROOT.parents[1]
DEFAULT_LOG_ROOT = REPO_ROOT / "butler" / "logs"
DEFAULT_MEMORY_DB = REPO_ROOT / "db" / "talos_memory.sqlite3"
DEFAULT_JOB_DB = REPO_ROOT / "db" / "talos_jobs.sqlite3"

MAX_TELEMETRY_EVENTS = 500
MAX_BENCHMARK_ROWS = 200
MAX_MESSAGES = 200
MAX_LOG_FILES = 20
MAX_LOG_BYTES_PER_FILE = 2 * 1024 * 1024
MAX_CSV_BYTES_PER_FILE = 8 * 1024 * 1024
MAX_REMOTE_METRICS_BYTES = 256 * 1024
MAX_JOB_ROWS = 200
MAX_TRACE_ROWS = 100


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _bounded_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _json_value(value: str | None) -> Any:
    if value is None or value == "":
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        if any(character in value for character in ".eE"):
            return float(value)
        return int(value)
    except ValueError:
        return value


def _tail_lines(path: Path, *, max_lines: int, max_bytes: int) -> list[str]:
    """Return a bounded tail without loading an arbitrarily large log."""
    if max_lines <= 0:
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - max_bytes)
        handle.seek(start)
        data = handle.read(max_bytes)
    if start:
        first_newline = data.find(b"\n")
        data = b"" if first_newline < 0 else data[first_newline + 1 :]
    return data.decode("utf-8", errors="replace").splitlines()[-max_lines:]


def read_pipeline_events(log_root: Path, *, limit: int) -> dict[str, Any]:
    limit = _bounded_int(limit, 150, minimum=1, maximum=MAX_TELEMETRY_EVENTS)
    files = sorted(
        log_root.glob("pipeline_telemetry_*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:MAX_LOG_FILES]
    events: list[dict[str, Any]] = []
    malformed = 0
    for path in files:
        remaining = limit - len(events)
        if remaining <= 0:
            break
        try:
            lines = _tail_lines(
                path,
                max_lines=remaining,
                max_bytes=MAX_LOG_BYTES_PER_FILE,
            )
        except OSError:
            continue
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(payload, dict):
                payload["_source_file"] = path.name
                events.append(payload)
            else:
                malformed += 1
            if len(events) >= limit:
                break
    events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return {
        "status": "available" if files else "not_available",
        "source": "butler/logs/pipeline_telemetry_*.jsonl",
        "events": events[:limit],
        "files_scanned": len(files),
        "malformed_lines_skipped": malformed,
        "newest_timestamp": events[0].get("timestamp") if events else None,
    }


def _read_csv_tail(path: Path, *, max_rows: int) -> tuple[list[dict[str, str]], str | None]:
    try:
        with path.open("rb") as handle:
            header = handle.readline()
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(len(header), size - MAX_CSV_BYTES_PER_FILE)
            handle.seek(start)
            body = handle.read(MAX_CSV_BYTES_PER_FILE)
    except OSError as exc:
        return [], str(exc)
    if start > len(header):
        first_newline = body.find(b"\n")
        body = b"" if first_newline < 0 else body[first_newline + 1 :]
    text = (header + body).decode("utf-8-sig", errors="replace")
    try:
        rows = deque(csv.DictReader(io.StringIO(text)), maxlen=max_rows)
    except csv.Error as exc:
        return [], str(exc)
    return [dict(row) for row in rows], None


def read_voice_benchmarks(log_root: Path, *, limit: int) -> dict[str, Any]:
    limit = _bounded_int(limit, 50, minimum=1, maximum=MAX_BENCHMARK_ROWS)
    candidates = (
        {log_root / "voice_benchmarks.csv", *log_root.glob("voice_benchmarks_*.csv")}
        if log_root.exists()
        else set()
    )
    files = sorted(
        (path for path in candidates if path.exists()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:MAX_LOG_FILES]
    output: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in files:
        remaining = limit - len(output)
        if remaining <= 0:
            break
        rows, error = _read_csv_tail(path, max_rows=remaining)
        if error:
            errors.append(f"{path.name}: {error}")
            continue
        for row in reversed(rows):
            converted = {key: _json_value(value) for key, value in row.items()}
            converted["_source_file"] = path.name
            output.append(converted)
            if len(output) >= limit:
                break
    return {
        "status": "available" if files else "not_available",
        "source": "butler/logs/voice_benchmarks*.csv",
        "rows": output,
        "files_scanned": len(files),
        "errors": errors[:10],
        "newest_timestamp": _benchmark_timestamp(output[0]) if output else None,
    }


def _benchmark_timestamp(row: dict[str, Any]) -> Any:
    for key in ("ts_callback_started", "ts_command_start", "run_started_at"):
        if row.get(key):
            return row[key]
    return None


def read_conversation_messages(memory_db: Path, *, limit: int) -> dict[str, Any]:
    limit = _bounded_int(limit, 80, minimum=1, maximum=MAX_MESSAGES)
    if not memory_db.exists():
        return {
            "status": "not_available",
            "source": "db/talos_memory.sqlite3",
            "messages": [],
            "reason": "Conversation database does not exist.",
        }
    uri = f"file:{memory_db.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=0.5)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT id, session_id, role, content, created_at, metadata_json
                FROM messages
                WHERE role IN ('user', 'assistant')
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {
            "status": "degraded",
            "source": "db/talos_memory.sqlite3",
            "messages": [],
            "reason": str(exc)[:300],
        }

    messages = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        messages.append(
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"],
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        )
    return {
        "status": "available",
        "source": "db/talos_memory.sqlite3 (read-only)",
        "messages": messages,
        "newest_timestamp": messages[0]["created_at"] if messages else None,
    }


def read_jobs(job_db: Path, *, limit: int) -> dict[str, Any]:
    """Read recent durable jobs without opening the writer-owned store."""
    limit = _bounded_int(limit, 50, minimum=1, maximum=MAX_JOB_ROWS)
    if not job_db.exists():
        return {"status": "not_available", "source": str(job_db), "jobs": []}
    uri = f"file:{job_db.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=0.5)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT job_id, session_id, source, status, created_at, updated_at,
                       started_at, finished_at, requires_user_input, error_message,
                       resource_locks_json
                FROM jobs ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {
            "status": "degraded",
            "source": str(job_db),
            "jobs": [],
            "reason": str(exc)[:300],
        }
    jobs = []
    for row in rows:
        try:
            locks = json.loads(row["resource_locks_json"] or "[]")
        except json.JSONDecodeError:
            locks = []
        jobs.append({
            "job_id": row["job_id"],
            "session_id": row["session_id"],
            "source": row["source"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "requires_user_input": bool(row["requires_user_input"]),
            "error_type": "job_failed" if row["error_message"] else None,
            "resource_locks": locks if isinstance(locks, list) else [],
        })
    return {"status": "available", "source": str(job_db), "jobs": jobs}


_DURATION_FIELDS = (
    "text_stream_total_ms", "agent_stream_total_ms", "llm_request_ms",
    "tool_execution_ms", "tool_ms", "prompt_assembly_ms",
    "awareness_snapshot_ms", "barge_in_decision_ms", "duration_ms",
)


def _duration_from_event(event: dict[str, Any]) -> float | None:
    for key in _DURATION_FIELDS:
        value = event.get(key)
        if isinstance(value, (int, float)):
            return round(float(value), 1)
    return None


def _elapsed_ms(start: Any, end: Any) -> float | None:
    try:
        started = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return round(max(0.0, (finished - started).total_seconds() * 1000.0), 1)
    except (TypeError, ValueError):
        return None


def build_request_traces(events: list[dict[str, Any]], *, limit: int = 100) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        trace_id = str(event.get("trace_id") or event.get("request_id") or "unknown")
        grouped.setdefault(trace_id, []).append(event)
    traces = []
    for trace_id, trace_events in grouped.items():
        ordered = sorted(trace_events, key=lambda item: str(item.get("timestamp") or ""))
        names = [str(item.get("event") or "unknown") for item in ordered]
        failures = [name for name in names if any(marker in name for marker in ("failed", "rejected", "interrupted", "timed_out", "dropped"))]
        failures.extend(
            str(item.get("event") or "failure")
            for item in ordered
            if item.get("failed") is True
        )
        stages = [
            {
                "timestamp": item.get("timestamp"),
                "component": item.get("component"),
                "event": item.get("event"),
                "duration_ms": _duration_from_event(item),
            }
            for item in ordered
        ]
        elapsed_ms = _elapsed_ms(ordered[0].get("timestamp"), ordered[-1].get("timestamp"))
        traces.append({
            "trace_id": trace_id,
            "request_id": ordered[-1].get("request_id"),
            "session_id": next((item.get("session_id") for item in ordered if item.get("session_id")), None),
            "started_at": ordered[0].get("timestamp"),
            "finished_at": ordered[-1].get("timestamp"),
            "outcome": "failed" if failures else "completed",
            "failure_event": failures[-1] if failures else None,
            "components": sorted({str(item.get("component") or "unknown") for item in ordered}),
            "stage_count": len(stages),
            "elapsed_ms": elapsed_ms,
            "stages": stages,
        })
    traces.sort(key=lambda item: str(item.get("finished_at") or ""), reverse=True)
    return traces[: max(1, min(MAX_TRACE_ROWS, int(limit)))]


def build_latency_summary(traces: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(
        float(trace["elapsed_ms"])
        for trace in traces
        if isinstance(trace.get("elapsed_ms"), (int, float))
    )
    if not values:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}

    def percentile(fraction: float) -> float:
        index = max(0, min(len(values) - 1, round((len(values) - 1) * fraction)))
        return round(values[index], 1)

    return {
        "count": len(values),
        "p50_ms": percentile(0.50),
        "p95_ms": percentile(0.95),
        "max_ms": round(values[-1], 1),
    }


def build_tool_reliability(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("event") == "tool_call_completed" and event.get("tool_name"):
            grouped.setdefault(str(event["tool_name"]), []).append(event)
    rows = []
    for name, calls in grouped.items():
        durations = sorted(float(call["tool_ms"]) for call in calls if isinstance(call.get("tool_ms"), (int, float)))
        p95_index = max(0, min(len(durations) - 1, int((len(durations) - 1) * 0.95))) if durations else 0
        failures = sum(bool(call.get("failed")) for call in calls)
        rows.append({
            "tool_name": name,
            "transport": calls[-1].get("transport"),
            "calls": len(calls),
            "failures": failures,
            "success_rate_percent": round((len(calls) - failures) / len(calls) * 100.0, 1),
            "average_ms": round(sum(durations) / len(durations), 1) if durations else None,
            "p95_ms": round(durations[p95_index], 1) if durations else None,
            "max_ms": round(durations[-1], 1) if durations else None,
        })
    return sorted(rows, key=lambda item: (-item["failures"], -(item["p95_ms"] or 0), item["tool_name"]))


def read_json_endpoint(url: str, *, token: str = "", timeout: float = 0.8) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_REMOTE_METRICS_BYTES + 1)
        if len(raw) > MAX_REMOTE_METRICS_BYTES:
            raise ValueError("response exceeded bounded dashboard limit")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("endpoint returned non-object JSON")
        return {"status": "available", "source": url, "data": payload}
    except Exception as exc:
        return {"status": "unavailable", "source": url, "data": {}, "reason": str(exc)[:300]}


def _http_probe(name: str, url: str, *, timeout: float = 0.4) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(4096)
            code = response.status
        return {
            "name": name,
            "status": "healthy" if 200 <= code < 400 else "degraded",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"HTTP {code}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "name": name,
            "status": "unavailable",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": str(exc)[:160],
        }


def _tcp_probe(name: str, host: str, port: int, *, timeout: float = 0.25) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {
            "name": name,
            "status": "healthy",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": f"TCP {host}:{port} accepting connections",
        }
    except OSError as exc:
        return {
            "name": name,
            "status": "unavailable",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": str(exc)[:160],
        }


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

    def integer(self) -> int:
        return (int(self.high) << 32) | int(self.low)


def _cpu_times() -> tuple[int, int] | None:
    if os.name == "nt":
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        if not ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        return idle.integer(), kernel.integer() + user.integer()
    try:
        values = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0].split()[1:]
        ticks = [int(value) for value in values]
    except (OSError, ValueError, IndexError):
        return None
    idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
    return idle, sum(ticks)


def _memory_snapshot() -> dict[str, Any]:
    if os.name == "nt":
        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.length = ctypes.sizeof(_MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return {
                "total_bytes": int(status.total_physical),
                "available_bytes": int(status.available_physical),
                "used_percent": float(status.memory_load),
            }
        return {}
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
        total = values["MemTotal"]
        available = values["MemAvailable"]
        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_percent": round((total - available) / total * 100, 1),
        }
    except (OSError, ValueError, KeyError):
        return {}


def _gpu_snapshot() -> dict[str, Any]:
    configured = os.getenv("BUTLER_NVIDIA_SMI_PATH", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(found) if (found := shutil.which("nvidia-smi")) else None,
    ]
    if os.name == "nt":
        candidates.extend(
            [
                Path(os.getenv("ProgramFiles", r"C:\Program Files"))
                / "NVIDIA Corporation"
                / "NVSMI"
                / "nvidia-smi.exe",
                Path(os.getenv("SystemRoot", r"C:\Windows"))
                / "System32"
                / "nvidia-smi.exe",
            ]
        )
    command = next((str(path) for path in candidates if path and path.is_file()), None)
    if not command:
        return {
            "status": "not_available",
            "gpus": [],
            "reason": "nvidia-smi not found on PATH or in standard Windows locations",
        }
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [
                command,
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "degraded", "gpus": [], "reason": str(exc)[:200]}
    if result.returncode != 0:
        return {
            "status": "degraded",
            "gpus": [],
            "reason": (result.stderr or "nvidia-smi failed")[:200],
        }
    gpus = []
    try:
        for row in csv.reader(result.stdout.splitlines()):
            if len(row) < 7:
                continue
            gpus.append(
                {
                    "index": _json_value(row[0].strip()),
                    "name": row[1].strip(),
                    "utilization_percent": _json_value(row[2].strip()),
                    "memory_used_mib": _json_value(row[3].strip()),
                    "memory_total_mib": _json_value(row[4].strip()),
                    "temperature_c": _json_value(row[5].strip()),
                    "power_w": _json_value(row[6].strip()),
                }
            )
    except csv.Error as exc:
        return {"status": "degraded", "gpus": [], "reason": str(exc)}
    return {"status": "available", "gpus": gpus}


class SystemSampler:
    """Cache moderately expensive host sampling across fast browser polls."""

    def __init__(self, *, cache_seconds: float = 1.0) -> None:
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._sampled_at = 0.0
        self._snapshot: dict[str, Any] = {}
        self._previous_cpu = _cpu_times()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if self._snapshot and now - self._sampled_at < self.cache_seconds:
                return self._snapshot
            current_cpu = _cpu_times()
            cpu_percent = None
            if current_cpu and self._previous_cpu:
                idle_delta = current_cpu[0] - self._previous_cpu[0]
                total_delta = current_cpu[1] - self._previous_cpu[1]
                if total_delta > 0:
                    cpu_percent = round((1.0 - idle_delta / total_delta) * 100, 1)
            self._previous_cpu = current_cpu
            disk = shutil.disk_usage(REPO_ROOT)
            try:
                load_average = list(os.getloadavg())
            except (AttributeError, OSError):
                load_average = None
            self._snapshot = {
                "sampled_at": _iso_now(),
                "cpu": {
                    "logical_count": os.cpu_count(),
                    "utilization_percent": cpu_percent,
                    "load_average": load_average,
                },
                "memory": _memory_snapshot(),
                "disk": {
                    "total_bytes": disk.total,
                    "free_bytes": disk.free,
                    "used_percent": round(disk.used / disk.total * 100, 1),
                },
                "gpu": _gpu_snapshot(),
            }
            self._sampled_at = now
            return self._snapshot


def _empty_host_snapshot(*, status: str, source: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "reason": reason,
        "sampled_at": None,
        "cpu": {
            "logical_count": None,
            "utilization_percent": None,
            "load_average": None,
        },
        "memory": {
            "total_bytes": None,
            "available_bytes": None,
            "used_percent": None,
        },
        "disk": {
            "total_bytes": None,
            "free_bytes": None,
            "used_percent": None,
        },
        "gpu": {"status": status, "gpus": [], "reason": reason},
    }


def _normalize_remote_host(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    candidate: Any = payload.get("host")
    if not isinstance(candidate, dict):
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, dict):
            system_health = snapshot.get("system_health")
            if isinstance(system_health, dict):
                candidate = system_health.get("host")
    if not isinstance(candidate, dict) and any(
        key in payload for key in ("cpu", "memory", "disk", "gpu")
    ):
        candidate = payload
    if not isinstance(candidate, dict):
        raise ValueError("Remote response does not contain a host metrics object.")

    normalized = _empty_host_snapshot(status="available", source=source, reason="")
    for section in ("cpu", "memory", "disk", "gpu"):
        value = candidate.get(section)
        if isinstance(value, dict):
            normalized[section].update(value)
    normalized["status"] = str(candidate.get("status") or "available")
    normalized["source"] = source
    normalized["sampled_at"] = candidate.get("sampled_at")
    normalized["reason"] = str(candidate.get("reason") or "")
    return normalized


def read_remote_host_metrics(url: str, *, token: str = "") -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=0.8) as response:
            raw = response.read(MAX_REMOTE_METRICS_BYTES + 1)
        if len(raw) > MAX_REMOTE_METRICS_BYTES:
            raise ValueError("Remote metrics response exceeds the 256 KiB limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Remote metrics response must be a JSON object.")
        if payload.get("ok") is False:
            raise ValueError(str(payload.get("error") or "Remote metrics endpoint reported failure."))
        return _normalize_remote_host(payload, source=url)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return _empty_host_snapshot(
            status="unavailable",
            source=url,
            reason=str(exc)[:240],
        )


def build_audio_snapshot(
    telemetry: dict[str, Any], benchmarks: dict[str, Any]
) -> dict[str, Any]:
    barge_events = [
        event
        for event in telemetry.get("events", [])
        if event.get("event") == "barge_in_metrics_snapshot"
    ]
    benchmark_rows = benchmarks.get("rows", [])
    series: dict[str, list[dict[str, Any]]] = {}
    for row in reversed(benchmark_rows):
        if row.get("input_rms") is not None:
            series.setdefault("input_rms", []).append(
                {"timestamp": _benchmark_timestamp(row), "value": row["input_rms"]}
            )
    for event in reversed(barge_events):
        timestamp = event.get("timestamp")
        for name, value in (event.get("measurements") or {}).items():
            if name.endswith("_last") and isinstance(value, (int, float)):
                series.setdefault(name[: -len("_last")], []).append(
                    {"timestamp": timestamp, "value": value}
                )
    return {
        "status": "available" if series else "not_available",
        "source": "voice benchmark CSV and barge-in telemetry snapshots",
        "sample_semantics": "Values update after an utterance or barge-in metrics snapshot; raw PCM is never read.",
        "latest_barge_in": barge_events[0] if barge_events else None,
        "series": {name: values[-120:] for name, values in sorted(series.items())},
        "configured_thresholds": {
            "barge_in_floor_rms": _json_value(os.getenv("BUTLER_BARGE_IN_FLOOR_RMS", "550")),
            "barge_in_output_silence_rms": _json_value(
                os.getenv("BUTLER_BARGE_IN_OUTPUT_SILENCE_RMS", "120")
            ),
        },
    }


class DebugSnapshotService:
    def __init__(
        self,
        *,
        log_root: Path = DEFAULT_LOG_ROOT,
        memory_db: Path = DEFAULT_MEMORY_DB,
        job_db: Path = DEFAULT_JOB_DB,
        sampler: SystemSampler | None = None,
        system_metrics_url: str | None = None,
    ) -> None:
        load_environment()
        self.log_root = Path(log_root)
        self.memory_db = Path(memory_db)
        self.job_db = Path(job_db)
        self.sampler = sampler
        self._host_history: deque[dict[str, Any]] = deque(maxlen=3600)
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self.include_content = os.getenv(
            "BUTLER_DEBUG_INCLUDE_CONTENT", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.system_metrics_url = (
            system_metrics_url
            if system_metrics_url is not None
            else os.getenv("BUTLER_DEBUG_SYSTEM_METRICS_URL", "")
        ).strip()
        self.system_metrics_token = os.getenv("BUTLER_DEBUG_SYSTEM_METRICS_TOKEN", "").strip()
        self.awareness_url = os.getenv(
            "BUTLER_AWARENESS_API_URL", "http://127.0.0.1:8600"
        ).rstrip("/")
        self.awareness_token = os.getenv("BUTLER_AWARENESS_API_TOKEN", "").strip()
        self.main_url = os.getenv(
            "BUTLER_TEXT_AGENT_URL", "http://127.0.0.1:8420"
        ).rstrip("/")
        self.main_token = os.getenv(
            "BUTLER_TEXT_AGENT_TOKEN", os.getenv("TEXT_AGENT_API_TOKEN", "")
        ).strip()
        self.ollama_url = os.getenv(
            "BUTLER_LLM_BASE_URL", "http://127.0.0.1:11434/v1"
        ).strip()

    def snapshot(
        self,
        *,
        event_limit: int = 150,
        interaction_limit: int = 50,
    ) -> dict[str, Any]:
        telemetry = self._cached(
            f"telemetry:{event_limit}", 1.5,
            lambda: read_pipeline_events(self.log_root, limit=event_limit),
        )
        benchmarks = self._cached(
            f"benchmarks:{interaction_limit}", 2.0,
            lambda: read_voice_benchmarks(self.log_root, limit=interaction_limit),
        )
        messages = self._cached(
            f"messages:{interaction_limit}", 2.0,
            lambda: read_conversation_messages(self.memory_db, limit=interaction_limit * 2),
        )
        jobs = self._cached(
            f"jobs:{interaction_limit}", 2.0,
            lambda: read_jobs(self.job_db, limit=interaction_limit),
        )
        host = self._cached("host", 1.0, self._host_snapshot)
        self._record_host_history(host)
        services = list(self._cached("services", 2.0, self._service_health))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            metrics_future = executor.submit(
                self._cached,
                "awareness_metrics",
                3.0,
                lambda: read_json_endpoint(
                    f"{self.awareness_url}/metrics", token=self.awareness_token
                ),
            )
            actions_future = executor.submit(
                self._cached,
                f"awareness_actions:{interaction_limit}",
                5.0,
                lambda: read_json_endpoint(
                    f"{self.awareness_url}/actions?limit={min(200, interaction_limit)}",
                    token=self.awareness_token,
                ),
            )
            main_future = executor.submit(
                self._cached,
                "main_runtime",
                2.0,
                lambda: read_json_endpoint(
                    f"{self.main_url}/health", token=self.main_token
                ),
            )
            ollama_future = executor.submit(
                self._cached,
                "ollama_placement",
                3.0,
                lambda: read_ollama_placement(self.ollama_url),
            )
            awareness_metrics = metrics_future.result()
            awareness_actions = actions_future.result()
            main_runtime = main_future.result()
            ollama_placement = ollama_future.result()
        # Wider scans than the live window; refreshed less often.
        round_stats = self._cached(
            "llm_round_stats", 10.0, lambda: read_llm_round_stats(self.log_root)
        )
        composition = self._cached(
            "prompt_composition", 10.0, lambda: read_prompt_composition(self.log_root)
        )
        memory_store = self._cached(
            "memory_store_stats", 5.0, lambda: read_memory_store_stats(self.memory_db)
        )
        visible_benchmarks = self._visible_benchmarks(benchmarks)
        visible_messages = self._visible_messages(messages)
        events = telemetry.get("events", [])
        request_traces = build_request_traces(events, limit=interaction_limit)
        services.append(
            {
                "name": "remote system metrics",
                "status": "healthy" if host.get("status") == "available" else host.get("status", "unavailable"),
                "latency_ms": None,
                "detail": host.get("source") if host.get("status") == "available" else host.get("reason"),
            }
        )
        return {
            "schema_version": 2,
            "generated_at": _iso_now(),
            "refresh_hint_ms": 1000,
            "interaction_io": {
                "conversation": visible_messages,
                "voice_benchmarks": visible_benchmarks,
                "pipeline": telemetry,
                "content_capture": {
                    "enabled": self.include_content,
                    "reason": (
                        "Exact stored conversation and voice benchmark text is visible."
                        if self.include_content
                        else "Content is redacted by default; set BUTLER_DEBUG_INCLUDE_CONTENT=1 only for a private debugging session."
                    ),
                },
                "prompt_capture": {
                    "status": "structure_only",
                    "reason": (
                        "Pipeline telemetry persists prompt sizes and token counts only. Launcher-managed "
                        "runs also write exact prompts to butler/logs/llm_io_*.jsonl (ADR-043)."
                    ),
                    "integration_note": (
                        "This console reads those transcripts for structure only -- section sizes and "
                        "tool schema sizes on the Model & Memory tab -- and never displays their text."
                    ),
                },
            },
            "system_health": {
                "services": services,
                "host": host,
                "history": self._downsampled_host_history(),
                "history_total_samples": len(self._host_history),
            },
            "live_audio": build_audio_snapshot(telemetry, benchmarks),
            "operations": {
                "request_traces": request_traces,
                "latency_summary": build_latency_summary(request_traces),
                "tool_reliability": build_tool_reliability(events),
                "jobs": jobs,
                "awareness_metrics": awareness_metrics,
                "actions": awareness_actions,
                "main_runtime": main_runtime,
            },
            "model_memory": {
                "llm_rounds": round_stats,
                "prompt_composition": composition,
                "ollama": ollama_placement,
                "memory_store": memory_store,
                "awareness_memory": {
                    "status": awareness_metrics.get("status"),
                    "reason": awareness_metrics.get("reason"),
                    "data": (awareness_metrics.get("data") or {}).get("memory"),
                },
            },
            "extensions": [],
        }

    def _cached(self, key: str, ttl_seconds: float, loader: Any) -> Any:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
        value = loader()
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + max(0.0, ttl_seconds), value)
        return value

    @staticmethod
    def _redacted(value: Any) -> str:
        length = len(str(value or ""))
        return f"[redacted; {length} chars]"

    def _visible_benchmarks(self, source: dict[str, Any]) -> dict[str, Any]:
        if self.include_content:
            return source
        result = dict(source)
        result["rows"] = [
            {
                **row,
                "transcript": self._redacted(row.get("transcript")),
                "command": self._redacted(row.get("command")),
                "response_preview": self._redacted(row.get("response_preview")),
            }
            for row in source.get("rows", [])
        ]
        return result

    def _visible_messages(self, source: dict[str, Any]) -> dict[str, Any]:
        if self.include_content:
            return source
        result = dict(source)
        result["messages"] = [
            {**message, "content": self._redacted(message.get("content"))}
            for message in source.get("messages", [])
        ]
        return result

    def _downsampled_host_history(self, max_points: int = 300) -> list[dict[str, Any]]:
        points = list(self._host_history)
        if len(points) <= max_points:
            return points
        scale = (len(points) - 1) / (max_points - 1)
        return [points[round(index * scale)] for index in range(max_points)]

    def _record_host_history(self, host: dict[str, Any]) -> None:
        point: dict[str, Any] = {
            "timestamp": host.get("sampled_at") or _iso_now(),
            "cpu_percent": (host.get("cpu") or {}).get("utilization_percent"),
            "memory_percent": (host.get("memory") or {}).get("used_percent"),
            "disk_percent": (host.get("disk") or {}).get("used_percent"),
            "gpus": [
                {
                    "index": gpu.get("index"),
                    "utilization_percent": gpu.get("utilization_percent"),
                    "memory_used_mib": gpu.get("memory_used_mib"),
                }
                for gpu in (host.get("gpu") or {}).get("gpus", [])
            ],
        }
        if not self._host_history or self._host_history[-1] != point:
            self._host_history.append(point)

    def _host_snapshot(self) -> dict[str, Any]:
        if self.sampler is not None:
            snapshot = dict(self.sampler.snapshot())
            snapshot.setdefault("status", "available")
            snapshot.setdefault("source", "explicit sampler")
            snapshot.setdefault("reason", "")
            return snapshot
        if self.system_metrics_url:
            return read_remote_host_metrics(
                self.system_metrics_url,
                token=self.system_metrics_token,
            )
        return _empty_host_snapshot(
            status="not_configured",
            source="remote system metrics",
            reason=(
                "Remote host metrics are not configured. Set "
                "BUTLER_DEBUG_SYSTEM_METRICS_URL to an endpoint on the Butler system host."
            ),
        )

    def _service_health(self) -> list[dict[str, Any]]:
        text_url = os.getenv("BUTLER_TEXT_AGENT_URL", "http://127.0.0.1:8420").rstrip("/")
        awareness_url = os.getenv(
            "BUTLER_AWARENESS_API_URL", "http://127.0.0.1:8600"
        ).rstrip("/")
        voice_port = _bounded_int(
            os.getenv("BUTLER_VOICE_SPEAK_PORT", "8610"), 8610, minimum=1, maximum=65535
        )
        probes = (
            (_http_probe, ("main text agent", f"{text_url}/health")),
            (_http_probe, ("awareness API", f"{awareness_url}/health/components")),
            (_http_probe, ("Ollama", "http://127.0.0.1:11434/api/version")),
            (_tcp_probe, ("voice worker speak API", "127.0.0.1", voice_port)),
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(probes)) as executor:
            futures = [executor.submit(function, *arguments) for function, arguments in probes]
            results = [future.result() for future in futures]
        return [
            {"name": "debug dashboard", "status": "healthy", "latency_ms": 0, "detail": "local process"},
            *results,
        ]


class DebugHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], snapshot_service: DebugSnapshotService) -> None:
        super().__init__(address, DebugRequestHandler)
        self.snapshot_service = snapshot_service


class DebugRequestHandler(BaseHTTPRequestHandler):
    server: DebugHTTPServer
    server_version = "ButlerDebugDashboard/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._serve_file(STATIC_ROOT / "index.html", "text/html; charset=utf-8")
            return
        static_files = {
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "application/javascript; charset=utf-8"),
        }
        if path in static_files:
            filename, content_type = static_files[path]
            self._serve_file(STATIC_ROOT / filename, content_type)
            return
        if path == "/api/health":
            self._write_json(HTTPStatus.OK, {"ok": True, "status": "healthy"})
            return
        if path == "/api/snapshot":
            query = parse_qs(parsed.query)
            event_limit = _bounded_int(
                (query.get("event_limit") or [150])[0],
                150,
                minimum=1,
                maximum=MAX_TELEMETRY_EVENTS,
            )
            interaction_limit = _bounded_int(
                (query.get("interaction_limit") or [50])[0],
                50,
                minimum=1,
                maximum=MAX_BENCHMARK_ROWS,
            )
            self._write_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "snapshot": self.server.snapshot_service.snapshot(
                        event_limit=event_limit,
                        interaction_limit=interaction_limit,
                    ),
                },
            )
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.path.startswith("/api/snapshot"):
            return
        print(f"[debug-dashboard] {self.address_string()} - {fmt % args}")

    def _serve_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except OSError as exc:
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": str(exc)})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    snapshot_service: DebugSnapshotService | None = None,
    allow_remote: bool = False,
) -> int:
    if host not in {"127.0.0.1", "::1", "localhost"} and not allow_remote:
        raise ValueError(
            "Refusing a non-loopback bind because the dashboard exposes private transcripts. "
            "Pass --allow-remote only on a trusted network."
        )
    server = DebugHTTPServer((host, port), snapshot_service or DebugSnapshotService())
    print(f"Butler debug dashboard serving on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Butler debug dashboard.")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Butler debug dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    parser.add_argument("--port", default=8787, type=int, help="Port to bind.")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow a non-loopback bind. The dashboard has no authentication and exposes transcripts.",
    )
    args = parser.parse_args(argv)
    return run_server(args.host, args.port, allow_remote=args.allow_remote)
