from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from talos.config import env_bool, load_environment
from talos.memory import get_default_memory_store
from talos.phone.elevenlabs_twilio import ElevenLabsTwilioProvider
from talos.phone.provider import (
    OutboundCallRequest,
    PhoneConfig,
    PhoneProvider,
    is_e164_number,
    normalize_contact_key,
)
from talos.phone.store import PhoneCallRecord, PhoneCallStore, get_default_phone_store


load_environment()

_default_provider: PhoneProvider | None = None


def get_phone_provider(
    *,
    config: PhoneConfig | None = None,
    store: PhoneCallStore | None = None,
) -> PhoneProvider:
    global _default_provider
    if config is not None or store is not None:
        return _build_provider(config=config, store=store)
    if _default_provider is None:
        _default_provider = _build_provider(config=None, store=None)
    return _default_provider


def reset_default_phone_provider() -> None:
    global _default_provider
    _default_provider = None


def place_phone_call(
    contact_or_number: str,
    *,
    purpose: str = "",
    brief_context: str = "",
    message_to_deliver: str = "",
    session_id: str = "default",
    runtime_lane: str = "foreground",
) -> dict[str, Any]:
    config = PhoneConfig.from_env()
    _ensure_phone_enabled(config)
    if runtime_lane != "foreground":
        raise RuntimeError("Phone calls are only allowed from the active foreground user session.")
    if not config.allowed_outbound:
        raise RuntimeError("Outbound phone calls are disabled. Set TALOS_PHONE_ALLOWED_OUTBOUND=1 to enable them.")

    to_number, contact_name = _resolve_target(contact_or_number, config)
    provider = get_phone_provider(config=config)
    normalized_purpose = str(purpose or "").strip()
    normalized_message = str(message_to_deliver or "").strip()
    normalized_context = str(brief_context or "").strip()
    composed_brief = _compose_outbound_call_brief(
        contact_name=contact_name or contact_or_number,
        purpose=normalized_purpose,
        message_to_deliver=normalized_message,
        brief_context=normalized_context,
    )
    record = provider.start_outbound_call(
        OutboundCallRequest(
            session_id=session_id,
            to_number=to_number,
            purpose=normalized_purpose,
            brief_context=composed_brief,
            contact_name=contact_name,
            message_to_deliver=normalized_message,
        )
    )
    _update_call_summary(record.call_id, refresh=False, provider=provider)
    return {
        "success": True,
        "message": "Outbound phone call requested.",
        "call": _serialize_call(record),
    }


def phone_call_status(call_id: str, *, refresh: bool = True) -> dict[str, Any]:
    provider = get_phone_provider()
    if refresh:
        synced = _sync_from_bridge(provider)
        if synced == 0 and not PhoneConfig.from_env().bridge_url:
            _refresh_call_from_provider(provider, str(call_id or "").strip())
    record = provider.get_call(str(call_id or "").strip())
    if record is None:
        raise RuntimeError(f"Unknown phone call '{call_id}'.")
    _update_call_summary(record.call_id, refresh=False, provider=provider)
    refreshed = provider.get_call(record.call_id)
    return {"success": True, "call": _serialize_call(refreshed or record)}


def recent_phone_calls(limit: int = 10, *, refresh: bool = True) -> dict[str, Any]:
    provider = get_phone_provider()
    if refresh:
        _sync_from_bridge(provider)
    records = provider.list_recent_calls(limit=max(1, int(limit)))
    return {
        "success": True,
        "calls": [_serialize_call(record) for record in records],
    }


def summarize_phone_call(call_id: str, *, refresh: bool = True) -> dict[str, Any]:
    provider = get_phone_provider()
    if refresh:
        synced = _sync_from_bridge(provider)
        if synced == 0 and not PhoneConfig.from_env().bridge_url:
            _refresh_call_from_provider(provider, str(call_id or "").strip())
    record = _update_call_summary(str(call_id or "").strip(), refresh=False, provider=provider)
    if record is None:
        raise RuntimeError(f"Unknown phone call '{call_id}'.")
    return {
        "success": True,
        "call_id": record.call_id,
        "summary": record.summary or "",
        "call": _serialize_call(record),
    }


def ingest_phone_bridge_snapshot(snapshot: dict[str, Any]) -> PhoneCallRecord:
    provider = get_phone_provider()
    if not isinstance(provider, ElevenLabsTwilioProvider):
        raise RuntimeError("Phone bridge sync is only implemented for the ElevenLabs/Twilio provider.")
    record = provider.sync_call_snapshot(snapshot)
    _finalize_call_record(record)
    refreshed = provider.get_call(record.call_id)
    if refreshed is None:
        raise RuntimeError(f"Phone call '{record.call_id}' disappeared after bridge sync.")
    return refreshed


def _build_provider(
    *,
    config: PhoneConfig | None,
    store: PhoneCallStore | None,
) -> PhoneProvider:
    resolved_config = config or PhoneConfig.from_env()
    resolved_store = store or get_default_phone_store()
    if resolved_config.provider_name != "elevenlabs_twilio":
        raise RuntimeError(
            f"Unsupported TALOS_PHONE_PROVIDER '{resolved_config.provider_name}'. "
            "Only 'elevenlabs_twilio' is currently implemented."
        )
    return ElevenLabsTwilioProvider(resolved_config, store=resolved_store)


def _ensure_phone_enabled(config: PhoneConfig) -> None:
    if not config.enabled:
        raise RuntimeError("Phone support is disabled. Set TALOS_PHONE_ENABLED=1 to enable it.")


def _resolve_target(contact_or_number: str, config: PhoneConfig) -> tuple[str, str | None]:
    raw_value = str(contact_or_number or "").strip()
    if not raw_value:
        raise RuntimeError("contact_or_number must not be empty.")

    if is_e164_number(raw_value):
        if raw_value not in set(config.allowlist):
            raise RuntimeError("That number is not in TALOS_PHONE_ALLOWLIST.")
        return raw_value, None

    contact_key = normalize_contact_key(raw_value)
    if contact_key not in config.contacts:
        raise RuntimeError(
            "Unknown phone contact. Use a configured contact name or an E.164 number in TALOS_PHONE_ALLOWLIST."
        )
    return config.contacts[contact_key], raw_value.strip()


def _sync_from_bridge(provider: PhoneProvider) -> int:
    config = PhoneConfig.from_env()
    if not config.bridge_url:
        return 0
    if not isinstance(provider, ElevenLabsTwilioProvider):
        return 0

    store = provider.store
    query = urllib.parse.urlencode(
        {
            "limit": str(config.bridge_sync_limit),
            "updated_after": store.latest_updated_at() or "",
        }
    )
    url = f"{config.bridge_url}/calls"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(url, method="GET")
    if config.bridge_token:
        request.add_header("Authorization", f"Bearer {config.bridge_token}")

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Phone bridge HTTP {exc.code}: {body_text or str(exc)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Phone bridge connection error: {exc.reason}") from exc

    try:
        parsed = json.loads(raw) if raw else {}
    except Exception as exc:
        raise RuntimeError("Phone bridge returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Phone bridge returned a non-object response.")

    synced = 0
    synced_call_ids: list[str] = []
    for item in list(parsed.get("calls") or []):
        if not isinstance(item, dict):
            continue
        record = provider.sync_call_snapshot(item)
        synced_call_ids.append(record.call_id)
        synced += 1
    if synced:
        for call_id in synced_call_ids:
            record = provider.get_call(call_id)
            if record is not None:
                _finalize_call_record(record)
    return synced


def _refresh_call_from_provider(provider: PhoneProvider, call_id: str) -> PhoneCallRecord | None:
    normalized_call_id = str(call_id or "").strip()
    if not normalized_call_id:
        return None
    if not isinstance(provider, ElevenLabsTwilioProvider):
        return None
    try:
        return provider.fetch_call_details(normalized_call_id)
    except Exception:
        return provider.get_call(normalized_call_id)


def _update_call_summary(
    call_id: str,
    *,
    refresh: bool,
    provider: PhoneProvider | None = None,
) -> PhoneCallRecord | None:
    resolved_provider = provider or get_phone_provider()
    if refresh:
        _sync_from_bridge(resolved_provider)
    record = resolved_provider.get_call(call_id)
    if record is None:
        return None
    return _finalize_call_record(record)


def _finalize_call_record(record: PhoneCallRecord) -> PhoneCallRecord:
    summary = _build_call_summary(record)
    provider = get_phone_provider()
    updated = record
    summary_changed = False
    if summary and summary != (record.summary or ""):
        store = provider.store if isinstance(provider, ElevenLabsTwilioProvider) else get_default_phone_store()
        updated = store.update_call(record.call_id, summary=summary)
        summary_changed = True
    if summary_changed:
        _maybe_record_call_memory(updated)
    return updated


def _maybe_record_call_memory(record: PhoneCallRecord) -> None:
    if not env_bool("TALOS_MEMORY_ENABLED", False):
        return
    if not record.summary:
        return
    try:
        memory_store = get_default_memory_store()
        target_session = record.session_id or "phone:inbound"
        memory_store.record_message(
            target_session,
            "system",
            f"Phone call summary: {record.summary}",
            metadata={"type": "phone_call", "call_id": record.call_id},
        )
        memory_store.refresh_session_summary(target_session)
    except Exception as exc:
        print(f"TALOS phone memory write failed: {exc}")


def _serialize_call(record: PhoneCallRecord) -> dict[str, Any]:
    return record.to_dict()


def _build_call_summary(record: PhoneCallRecord) -> str:
    target = record.contact_name or record.remote_number
    status = record.outcome or record.status
    parts = [f"{record.direction.title()} call with {target}."]
    if status:
        parts.append(f"Status: {status}.")
    if record.purpose:
        parts.append(f"Purpose: {_compact_text(record.purpose, limit=140)}.")
    first_user_message = _first_transcript_message(record.transcript or [], role="user")
    if first_user_message:
        parts.append(f"Caller said: {_compact_text(first_user_message, limit=160)}.")
    if record.last_error and record.last_error != status:
        parts.append(f"Error: {_compact_text(record.last_error, limit=140)}.")
    return " ".join(parts).strip()


def _compose_outbound_call_brief(
    *,
    contact_name: str,
    purpose: str,
    message_to_deliver: str,
    brief_context: str,
) -> str:
    lines = [
        "You are TALOS, the user's personal AI assistant, placing an outbound phone call.",
        "When the recipient answers, immediately identify yourself as TALOS.",
        f"Recipient: {str(contact_name or 'the intended contact').strip()}",
    ]
    if purpose:
        lines.append(f"Primary objective: {purpose}")
    if message_to_deliver:
        lines.append(f"Exact message to deliver: {message_to_deliver}")
    if brief_context:
        lines.append(f"Additional context: {brief_context}")
    lines.extend(
        [
            "Deliver the requested report or task result directly and concisely.",
            "Do not invent facts beyond the message and context provided.",
            "After delivering the message, confirm the recipient heard and understood it.",
        ]
    )
    return "\n".join(lines)


def _first_transcript_message(transcript: list[dict[str, Any]], *, role: str) -> str | None:
    for item in transcript:
        if str(item.get("role") or "").strip().lower() != role:
            continue
        message = str(item.get("message") or "").strip()
        if message:
            return message
    return None


def _compact_text(value: str, *, limit: int) -> str:
    compacted = " ".join(str(value or "").split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 3].rsplit(" ", 1)[0] + "..."
