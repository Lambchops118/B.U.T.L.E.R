from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from talos.config import env_bool, env_int, load_environment
from talos.phone.store import PhoneCallRecord


E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


@dataclass(frozen=True)
class OutboundCallRequest:
    session_id: str
    to_number: str
    purpose: str = ""
    brief_context: str = ""
    contact_name: str | None = None
    message_to_deliver: str = ""
    caller_identity: str = "TALOS"


@dataclass(frozen=True)
class PhoneConfig:
    enabled: bool
    provider_name: str
    api_key: str
    agent_id: str
    phone_number_id: str
    allowed_outbound: bool
    bridge_url: str
    bridge_token: str
    contacts: dict[str, str]
    allowlist: tuple[str, ...]
    bridge_sync_limit: int
    db_path: str | None = None

    @classmethod
    def from_env(cls) -> "PhoneConfig":
        load_environment()
        return cls(
            enabled=env_bool("TALOS_PHONE_ENABLED", False),
            provider_name=os.getenv("TALOS_PHONE_PROVIDER", "elevenlabs_twilio").strip() or "elevenlabs_twilio",
            api_key=os.getenv("ELEVENLABS_API_KEY", "").strip(),
            agent_id=os.getenv("TALOS_PHONE_AGENT_ID", "").strip(),
            phone_number_id=os.getenv("TALOS_PHONE_NUMBER_ID", "").strip(),
            allowed_outbound=env_bool("TALOS_PHONE_ALLOWED_OUTBOUND", False),
            bridge_url=os.getenv("TALOS_PHONE_BRIDGE_URL", "").strip().rstrip("/"),
            bridge_token=os.getenv("TALOS_PHONE_BRIDGE_TOKEN", "").strip(),
            contacts=_parse_contacts(os.getenv("TALOS_PHONE_CONTACTS", "")),
            allowlist=tuple(_parse_allowlist(os.getenv("TALOS_PHONE_ALLOWLIST", ""))),
            bridge_sync_limit=max(1, env_int("TALOS_PHONE_BRIDGE_SYNC_LIMIT", 25)),
            db_path=os.getenv("TALOS_PHONE_DB_PATH", "").strip() or None,
        )


class PhoneProvider(ABC):
    @abstractmethod
    def start_outbound_call(self, request: OutboundCallRequest) -> PhoneCallRecord:
        raise NotImplementedError

    @abstractmethod
    def get_call(self, call_id: str) -> PhoneCallRecord | None:
        raise NotImplementedError

    @abstractmethod
    def list_recent_calls(self, *, limit: int = 25) -> list[PhoneCallRecord]:
        raise NotImplementedError

    @abstractmethod
    def ingest_call_event(self, payload: dict[str, Any]) -> PhoneCallRecord:
        raise NotImplementedError

    @abstractmethod
    def fetch_transcript(self, call_id: str) -> list[dict[str, Any]] | None:
        raise NotImplementedError


def normalize_contact_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def is_e164_number(value: str) -> bool:
    return bool(E164_PATTERN.match(str(value or "").strip()))


def _parse_contacts(raw_value: str) -> dict[str, str]:
    if not raw_value.strip():
        return {}
    try:
        parsed = json.loads(raw_value)
    except Exception as exc:
        raise RuntimeError("TALOS_PHONE_CONTACTS must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("TALOS_PHONE_CONTACTS must be a JSON object mapping names to E.164 numbers.")
    normalized: dict[str, str] = {}
    for key, value in parsed.items():
        contact_key = normalize_contact_key(str(key))
        number = str(value or "").strip()
        if not contact_key:
            continue
        if not is_e164_number(number):
            raise RuntimeError(f"TALOS_PHONE_CONTACTS entry '{key}' is not a valid E.164 number.")
        normalized[contact_key] = number
    return normalized


def _parse_allowlist(raw_value: str) -> list[str]:
    if not raw_value.strip():
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception as exc:
        raise RuntimeError("TALOS_PHONE_ALLOWLIST must be valid JSON.") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("TALOS_PHONE_ALLOWLIST must be a JSON array of E.164 numbers.")
    normalized: list[str] = []
    for item in parsed:
        number = str(item or "").strip()
        if not number:
            continue
        if not is_e164_number(number):
            raise RuntimeError(f"TALOS_PHONE_ALLOWLIST entry '{number}' is not a valid E.164 number.")
        normalized.append(number)
    return normalized
