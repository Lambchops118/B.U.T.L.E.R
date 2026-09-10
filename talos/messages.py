import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional


MessageType = Literal["voice_cmd", "text_cmd", "announcement", "status", "event", "ui"]

@dataclass
class Message:
    type: MessageType
    payload: Any
    needs_llm: bool = False
    ts: float = field(default_factory=time.time)

@dataclass
class StatusPayload:
    key: str
    value: Any
    freshness: float

@dataclass
class AnnouncementPayload:
    title: str
    text: str

@dataclass
class VoicePayload:
    command: str
    benchmark: Optional[Any] = None

@dataclass
class TextPayload:
    command: str
    session_id: str
    source: str = "text"
    reply_queue: Optional[Any] = None
    requested_mode: str = "auto"
    # Extra context for this turn only, from the ingress that accepted it (e.g.
    # a note that sleep mode was just toggled). Appended to whatever context the
    # router assembles.
    extra_context: Optional[str] = None

@dataclass
class EventPayload:
    name: str
    data: dict
