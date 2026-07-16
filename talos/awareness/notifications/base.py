"""Typed notification adapter interface (C9, Phase 4).

One interface around existing channels only. Each adapter defines what
"confirmed" means for its channel and must never report delivery without
that confirmation (INV-14). Adapters perform network I/O and therefore run
only in outbox workers, never inside database transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NotificationContent:
    title: str
    body: str
    severity: str


@dataclass(frozen=True)
class DeliveryResult:
    confirmed: bool
    detail: str = ""
    provider_message_id: str | None = None


class NotificationAdapter(Protocol):
    name: str
    confirmation_semantics: str  # honest, channel-specific meaning of "confirmed"

    async def send(self, content: NotificationContent) -> DeliveryResult: ...
