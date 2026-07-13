from __future__ import annotations

from talos.phone.elevenlabs_twilio import ElevenLabsTwilioProvider
from talos.phone.provider import OutboundCallRequest, PhoneConfig, PhoneProvider
from talos.phone.service import (
    get_phone_provider,
    ingest_phone_bridge_snapshot,
    phone_call_status,
    place_phone_call,
    recent_phone_calls,
    reset_default_phone_provider,
    summarize_phone_call,
)
from talos.phone.store import (
    DEFAULT_PHONE_DB_PATH,
    PhoneCallEvent,
    PhoneCallRecord,
    PhoneCallStore,
    get_default_phone_store,
    reset_default_phone_store,
)

__all__ = [
    "DEFAULT_PHONE_DB_PATH",
    "ElevenLabsTwilioProvider",
    "OutboundCallRequest",
    "PhoneCallEvent",
    "PhoneCallRecord",
    "PhoneCallStore",
    "PhoneConfig",
    "PhoneProvider",
    "get_default_phone_store",
    "get_phone_provider",
    "ingest_phone_bridge_snapshot",
    "phone_call_status",
    "place_phone_call",
    "recent_phone_calls",
    "reset_default_phone_provider",
    "reset_default_phone_store",
    "summarize_phone_call",
]
