"""Model, prompt, and memory insight readers for the debug console.

Everything here is read-only and bounded, like the rest of the dashboard:
recent telemetry tails, the newest launcher LLM I/O transcript, Ollama's
``/api/ps``, and the conversation store opened ``mode=ro``. The LLM I/O file
holds exact prompts; only structure (section sizes, tool names and schema
sizes) leaves this module, never message text.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Wider than the live event window on purpose: per-tool usage and latency
# distributions need days of turns, not the last few minutes.
INSIGHT_LOG_FILES = 40
INSIGHT_BYTES_PER_FILE = 4 * 1024 * 1024
LLM_IO_TAIL_BYTES = 8 * 1024 * 1024
LLM_IO_MAX_REQUESTS = 200
RECENT_TURN_ROWS = 25
# Same tokenizer-free ratio the runtime uses for prompt_tokens_estimated.
CHARS_PER_TOKEN = 3.5

# System messages are classified by their fixed leading text so the console
# can show what the prompt is made of without ever returning the text itself.
_SYSTEM_SECTIONS = (
    ("## Base Soul Document", "system prompt"),
    ("## Memory Context", "memory block"),
    ("## Additional Runtime Context", "extra runtime context"),
    ("Context (read-only)", "situation snapshot"),
    ("The conversation above", "grounding notice"),
    ("Current authoritative date", "time notice"),
)


def _estimate_tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 1)


def _tail_text(path: Path, max_bytes: int) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - max_bytes)
        handle.seek(start)
        data = handle.read(max_bytes)
    if start:
        newline = data.find(b"\n")
        data = b"" if newline < 0 else data[newline + 1 :]
    return data.decode("utf-8", errors="replace").splitlines()


def _newest(log_root: Path, pattern: str, limit: int) -> list[Path]:
    try:
        return sorted(
            log_root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True
        )[:limit]
    except OSError:
        return []


def read_llm_round_stats(log_root: Path) -> dict[str, Any]:
    """Latency split, per-tool call counts, and memory injection from telemetry."""
    files = _newest(log_root, "pipeline_telemetry_*.jsonl", INSIGHT_LOG_FILES)
    rounds: list[dict[str, Any]] = []
    prompts: dict[str, dict[str, Any]] = {}
    tool_calls: dict[str, dict[str, Any]] = {}
    timestamps: list[str] = []
    for path in files:
        try:
            lines = _tail_text(path, INSIGHT_BYTES_PER_FILE)
        except OSError:
            continue
        for line in lines:
            if '"llm_round_completed"' not in line and '"prompt_ready"' not in line and '"tool_call_completed"' not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            name = event.get("event")
            stamp = str(event.get("timestamp") or "")
            if stamp:
                timestamps.append(stamp)
            if name == "llm_round_completed":
                rounds.append(event)
            elif name == "prompt_ready":
                prompts[str(event.get("request_id"))] = event
            elif name == "tool_call_completed" and event.get("tool_name"):
                entry = tool_calls.setdefault(
                    str(event["tool_name"]), {"calls": 0, "failures": 0, "last_called": ""}
                )
                entry["calls"] += 1
                entry["failures"] += int(bool(event.get("failed")))
                entry["last_called"] = max(entry["last_called"], stamp)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in rounds:
        kind = "first round" if event.get("round") == 1 else "follow-up round"
        groups.setdefault((str(event.get("model") or "unknown"), kind), []).append(event)
    latency_rows = []
    for (model, kind), events in sorted(groups.items()):
        ttft = [float(e["llm_ttft_ms"]) for e in events if isinstance(e.get("llm_ttft_ms"), (int, float))]
        request = [float(e["llm_request_ms"]) for e in events if isinstance(e.get("llm_request_ms"), (int, float))]
        tokens = [float(e["provider_prompt_tokens"]) for e in events if isinstance(e.get("provider_prompt_tokens"), (int, float))]
        latency_rows.append({
            "model": model,
            "round_kind": kind,
            "count": len(events),
            "ttft_p50_ms": _percentile(ttft, 0.5),
            "ttft_p95_ms": _percentile(ttft, 0.95),
            "request_p50_ms": _percentile(request, 0.5),
            "prompt_tokens_p50": _percentile(tokens, 0.5),
        })

    by_request: dict[str, list[dict[str, Any]]] = {}
    for event in rounds:
        by_request.setdefault(str(event.get("request_id")), []).append(event)
    recent = []
    for request_id, events in by_request.items():
        events.sort(key=lambda item: int(item.get("round") or 0))
        first = events[0]
        follow = next((e for e in events if (e.get("round") or 0) > 1), None)
        prompt = prompts.get(request_id, {})
        recent.append({
            "timestamp": first.get("timestamp"),
            "request_id": request_id,
            "model": first.get("model"),
            "rounds": len(events),
            "first_ttft_ms": first.get("llm_ttft_ms"),
            "followup_ttft_ms": follow.get("llm_ttft_ms") if follow else None,
            "prompt_tokens": first.get("provider_prompt_tokens"),
            "completion_tokens": sum(
                int(e.get("provider_completion_tokens") or 0) for e in events
            ),
            "tool_count": prompt.get("tool_count"),
            "memory_block_chars": prompt.get("memory_block_chars"),
            "history_message_count": prompt.get("history_message_count"),
        })
    recent.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)

    reported = [p for p in prompts.values() if isinstance(p.get("memory_block_chars"), int)]
    injected = sum(1 for p in reported if p["memory_block_chars"] > 0)
    return {
        "status": "available" if files else "not_available",
        "source": "butler/logs/pipeline_telemetry_*.jsonl",
        "window": {
            "files_scanned": len(files),
            "oldest": min(timestamps) if timestamps else None,
            "newest": max(timestamps) if timestamps else None,
            "turns": len(prompts),
            "llm_rounds": len(rounds),
        },
        "latency_by_model": latency_rows,
        "recent_turns": recent[:RECENT_TURN_ROWS],
        "tool_calls": tool_calls,
        "memory_injection": {
            "prompts_reporting": len(reported),
            "prompts_with_memory": injected,
            "note": (
                "Only prompts logged after the runtime began reporting "
                "memory_block_chars are counted."
            ),
        },
    }


def _classify_messages(messages: list[Any]) -> list[dict[str, Any]]:
    last_user = max(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "user"),
        default=-1,
    )
    sizes: Counter[str] = Counter()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content or "")
        chars = len(text) + len(json.dumps(message.get("tool_calls") or ""))
        role = message.get("role")
        if role == "system":
            label = next(
                (name for prefix, name in _SYSTEM_SECTIONS if text.lstrip().startswith(prefix)),
                "other system",
            )
            # The memory block rides inside the per-request system message.
            if label != "memory block" and "## Memory Context" in text:
                split = text.index("## Memory Context")
                sizes["memory block"] += len(text) - split
                chars = split
        elif index == last_user:
            label = "user turn"
        elif index > last_user:
            label = "tool loop (this turn)"
        else:
            label = "conversation history"
        sizes[label] += chars
    return [{"section": name, "chars": chars} for name, chars in sizes.items()]


def read_prompt_composition(log_root: Path) -> dict[str, Any]:
    """What the newest real (non-warmup) request was made of, by size."""
    files = _newest(log_root, "llm_io_*.jsonl", 3)
    if not files:
        return {
            "status": "not_available",
            "reason": "No butler/logs/llm_io_*.jsonl transcript. The launcher writes one per main-agent run.",
        }
    requests: list[dict[str, Any]] = []
    source = files[0]
    for path in files:
        try:
            lines = _tail_text(path, LLM_IO_TAIL_BYTES)
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(payload, dict) or record.get("operation") == "warmup":
                continue
            if record.get("direction") == "sent" and isinstance(payload.get("messages"), list):
                requests.append({"timestamp": record.get("timestamp"), "payload": payload})
            elif record.get("direction") == "received" and isinstance(payload.get("usage"), dict):
                if requests:
                    requests[-1]["usage"] = payload["usage"]
        if requests:
            source = path
            break
    if not requests:
        return {"status": "not_available", "reason": "No non-warmup request in the newest LLM I/O transcripts."}

    requests = requests[-LLM_IO_MAX_REQUESTS:]
    # A follow-up tool round is a continuation; describe the turn's first request.
    newest = requests[-1]
    for candidate in reversed(requests):
        if candidate["payload"]["messages"] and candidate["payload"]["messages"][-1].get("role") == "user":
            newest = candidate
            break
    payload = newest["payload"]
    tools = payload.get("tools") or []
    tool_rows = []
    for tool in tools:
        function = tool.get("function", tool) if isinstance(tool, dict) else {}
        chars = len(json.dumps(tool))
        tool_rows.append({"name": function.get("name") or "unknown", "chars": chars, "est_tokens": _estimate_tokens(chars)})
    tool_rows.sort(key=lambda row: -row["chars"])

    sections = _classify_messages(payload["messages"])
    tools_chars = len(json.dumps(tools)) if tools else 0
    if tools_chars:
        sections.append({"section": "tool schemas", "chars": tools_chars})
    total = sum(section["chars"] for section in sections) or 1
    for section in sections:
        section["est_tokens"] = _estimate_tokens(section["chars"])
        section["share_percent"] = round(section["chars"] / total * 100, 1)
    sections.sort(key=lambda item: -item["chars"])

    with_memory = sum(
        1
        for request in requests
        if any(
            isinstance(m, dict) and isinstance(m.get("content"), str) and "## Memory Context" in m["content"]
            for m in request["payload"]["messages"]
        )
    )
    return {
        "status": "available",
        "source": source.name,
        "timestamp": newest.get("timestamp"),
        "model": payload.get("model"),
        "message_count": len(payload["messages"]),
        "total_chars": total,
        "est_tokens": _estimate_tokens(total),
        "provider_prompt_tokens": (newest.get("usage") or {}).get("prompt_tokens"),
        "sections": sections,
        "tools": tool_rows,
        "requests_scanned": len(requests),
        "requests_with_memory_block": with_memory,
    }


def read_ollama_placement(base_url: str, *, timeout: float = 0.8) -> dict[str, Any]:
    """Loaded models and how much of each sits in VRAM, from Ollama's /api/ps."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    url = f"{root}/api/ps"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
            payload = json.loads(response.read(256 * 1024))
    except Exception as exc:  # noqa: BLE001 - shown to the operator
        return {"status": "unavailable", "source": url, "models": [], "reason": str(exc)[:200]}
    models = []
    for model in payload.get("models") or []:
        size = int(model.get("size") or 0)
        vram = int(model.get("size_vram") or 0)
        models.append({
            "name": model.get("name") or model.get("model"),
            "size_bytes": size,
            "vram_bytes": vram,
            "gpu_percent": round(vram / size * 100, 1) if size else None,
            "context_length": model.get("context_length"),
            "expires_at": model.get("expires_at"),
            "quantization": (model.get("details") or {}).get("quantization_level"),
            "parameter_size": (model.get("details") or {}).get("parameter_size"),
        })
    return {"status": "available", "source": url, "models": models}


def read_memory_store_stats(memory_db: Path) -> dict[str, Any]:
    """Row counts of the conversation/fact store; never its content."""
    if not memory_db.exists():
        return {"status": "not_available", "source": memory_db.name, "reason": "Memory database does not exist."}
    try:
        created = datetime.fromtimestamp(memory_db.stat().st_ctime, tz=timezone.utc)
    except OSError:
        created = None
    try:
        connection = sqlite3.connect(f"file:{memory_db.as_posix()}?mode=ro", uri=True, timeout=0.5)
        try:
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("sessions", "messages", "facts", "summaries")
            }
            facts_by_scope = dict(
                connection.execute("SELECT scope, count(*) FROM facts GROUP BY scope").fetchall()
            )
            newest_fact = connection.execute("SELECT max(updated_at) FROM facts").fetchone()[0]
            oldest_message = connection.execute("SELECT min(created_at) FROM messages").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"status": "degraded", "source": memory_db.name, "reason": str(exc)[:200]}
    return {
        "status": "available",
        "source": f"{memory_db.name} (read-only)",
        "file_created_at": created.isoformat(timespec="seconds") if created else None,
        "counts": counts,
        "facts_by_scope": facts_by_scope,
        "newest_fact_at": newest_fact,
        "oldest_message_at": oldest_message,
    }
