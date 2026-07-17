"""Main-agent client for the awareness backend's read API (Phase 5).

Thin bounded HTTP reads against the awareness process (default
``http://127.0.0.1:8600``). Every call has a short timeout and degrades
truthfully: failures return an explicit error string / ``None`` — never
fabricated data — and the caller decides how to fall back (the router keeps
its legacy in-memory snapshot as the fallback).

The situation fetch is cached briefly so command bursts do not hammer the
backend from the synchronous router loop.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8600"
_SITUATION_CACHE_SECONDS = 5.0

_cache: dict[str, tuple[float, str]] = {}


def _base_url() -> str:
    return os.getenv("TALOS_AWARENESS_API_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.getenv("TALOS_AWARENESS_CLIENT_TIMEOUT", "1.5"))
    except ValueError:
        return 1.5


def _api_token() -> str:
    return os.getenv("TALOS_AWARENESS_API_TOKEN", "").strip()


def situation_enabled() -> bool:
    return os.getenv("TALOS_AWARENESS_SITUATION_ENABLED", "1").strip() not in {
        "0",
        "false",
        "no",
    }


def _auth_headers() -> dict[str, str]:
    token = os.getenv("TALOS_AWARENESS_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def get_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bounded GET; raises RuntimeError with a clear, short message."""
    url = _base_url() + path
    if params:
        filtered = {k: v for k, v in params.items() if v not in (None, "")}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)
    request = urllib.request.Request(url, headers=_auth_headers())
    try:
        with urllib.request.urlopen(request, timeout=_timeout()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        raise RuntimeError(
            f"awareness API {exc.code} for {path}: {detail or exc.reason}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"awareness backend unreachable ({path}): {exc}") from exc


def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Bounded POST; raises RuntimeError with a clear, short message."""
    url = _base_url() + path
    headers = {"Content-Type": "application/json"}
    token = _api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        raise RuntimeError(
            f"awareness API {exc.code} for {path}: {detail or exc.reason}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"awareness backend unreachable ({path}): {exc}") from exc


def fetch_situation_text(budget_tokens: int | None = None) -> str | None:
    """Rendered situation snapshot, or None when unavailable/disabled."""
    if not situation_enabled():
        return None
    cache_key = f"situation:{budget_tokens}"
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _SITUATION_CACHE_SECONDS:
        return cached[1]
    try:
        payload = get_json(
            "/situation",
            {"budget_tokens": budget_tokens} if budget_tokens else None,
        )
    except RuntimeError:
        return None
    text = str(payload.get("text", "")).strip()
    if not text:
        return None
    rendered = f"Situation as of {payload.get('as_of', 'unknown')}:\n{text}"
    _cache[cache_key] = (now, rendered)
    return rendered


def snapshot_with_fallback(legacy_snapshot: str) -> str:
    """Awareness situation when available, else the legacy router snapshot."""
    situation = fetch_situation_text()
    return situation if situation is not None else legacy_snapshot
