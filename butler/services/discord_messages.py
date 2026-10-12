"""Send Discord messages and control the Discord bot's voice connection.

Messages go through the Discord frontend's Python service; voice join/leave
goes to the Rust bot's localhost control API (``DISCORD_VOICE_CONTROL_URL``),
since only the gateway process can join a voice channel.

The Discord frontend is a separate repository (see butler/launcher/core.py
``_start_discord``). Its Python service exposes ``POST /send-message`` and
``GET /contacts``; this module is the thin client Talos's tool calls. Both
calls carry ``BUTLER_PRIVILEGED_CALLER_TOKEN``, the secret the two sides
already share, and the frontend refuses everything when it is unset.

Results are the frontend's structured outcome dicts, whose ``status`` is one of
sent / failed / ambiguous / not_found / invalid / disabled / unknown. Transport
problems are mapped onto the same shape here: a request that never reached the
service is ``unavailable``; one that may have reached it but returned nothing
is ``unknown`` and must not be retried as if it failed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from butler.config import load_environment

load_environment()

_URL_ENV = "DISCORD_SERVICE_URL"
_DEFAULT_URL = "http://127.0.0.1:8000"
_VOICE_URL_ENV = "DISCORD_VOICE_CONTROL_URL"
_DEFAULT_VOICE_URL = "http://127.0.0.1:8001"
# Joining voice waits on Discord's voice handshake, which can take a while.
_VOICE_TIMEOUT_SECONDS = 30.0
VOICE_ACTIONS = ("join", "leave", "status")
_TOKEN_ENV = "BUTLER_PRIVILEGED_CALLER_TOKEN"
_TIMEOUT_SECONDS = 15.0
DELIVERY_MODES = ("dm", "channel")


def _base_url() -> str:
    return (os.getenv(_URL_ENV) or _DEFAULT_URL).strip().rstrip("/")


def _voice_url() -> str:
    return (os.getenv(_VOICE_URL_ENV) or _DEFAULT_VOICE_URL).strip().rstrip("/")


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    base_url: str | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict[str, Any]:
    base = base_url or _base_url()
    token = os.getenv(_TOKEN_ENV, "").strip()
    if not token:
        return {
            "status": "disabled",
            "error": f"{_TOKEN_ENV} is not set, so Discord control is disabled",
        }
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "X-Butler-Privileged-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        status = "disabled" if exc.code in (401, 503) else "failed"
        return {"status": status, "error": f"Discord service HTTP {exc.code}: {detail}"}
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return {"status": "unknown", "error": "Discord service timed out; the message may have been sent"}
        return {
            "status": "unavailable",
            "error": f"Discord service not reachable at {base}: {reason}",
        }
    except TimeoutError:
        return {"status": "unknown", "error": "Discord service timed out; the message may have been sent"}
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        return {"status": "unknown", "error": "Discord service returned a non-JSON response"}
    if not isinstance(payload, dict):
        return {"status": "unknown", "error": "Discord service returned an unexpected payload"}
    return payload


def send_message(
    message: str,
    recipient: str | None = None,
    delivery: str = "dm",
    channel: str | None = None,
    server: str | None = None,
) -> dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        return {"status": "invalid", "error": "message is empty"}
    mode = str(delivery or "dm").strip().lower()
    if mode not in DELIVERY_MODES:
        return {"status": "invalid", "error": "delivery must be 'dm' or 'channel'"}
    who = str(recipient or "").strip() or None
    where = str(channel or "").strip() or None
    if where:
        # Naming a channel always means posting there, not a DM.
        mode = "channel"
    if mode == "dm" and not who:
        return {"status": "invalid", "error": "a recipient is required for a direct message"}

    body: dict[str, Any] = {
        "message": text,
        "recipient": who,
        "delivery": mode,
        # One key per intended send: the frontend dedupes a replay of it.
        "idempotency_key": uuid.uuid4().hex,
    }
    if where:
        body["channel"] = where
        if str(server or "").strip():
            body["server"] = str(server).strip()
    return _request("POST", "/send-message", body)


def list_contacts() -> dict[str, Any]:
    return _request("GET", "/contacts")


def list_channels() -> dict[str, Any]:
    return _request("GET", "/channels")


def voice(action: str, channel: str | None = None, server: str | None = None) -> dict[str, Any]:
    """Join, leave, or report the bot's Discord voice connection."""
    verb = str(action or "").strip().lower()
    if verb not in VOICE_ACTIONS:
        return {"status": "invalid", "error": "action must be 'join', 'leave', or 'status'"}
    if verb == "status":
        return _request("GET", "/voice/status", base_url=_voice_url())
    body: dict[str, Any] = {}
    if str(server or "").strip():
        body["server"] = str(server).strip()
    if verb == "join":
        if str(channel or "").strip():
            body["channel"] = str(channel).strip()
        return _request(
            "POST", "/voice/join", body, base_url=_voice_url(), timeout=_VOICE_TIMEOUT_SECONDS
        )
    return _request(
        "POST", "/voice/leave", body, base_url=_voice_url(), timeout=_VOICE_TIMEOUT_SECONDS
    )
