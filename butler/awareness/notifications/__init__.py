"""Proactive notification delivery through existing channels (C9, Phase 4)."""

from butler.awareness.notifications.adapters import (
    GuiNotificationAdapter,
    LogNotificationAdapter,
    build_adapters,
)
from butler.awareness.notifications.base import DeliveryResult, NotificationAdapter, NotificationContent
from butler.awareness.notifications.handler import NotificationHandler

__all__ = [
    "DeliveryResult",
    "NotificationAdapter",
    "NotificationContent",
    "NotificationHandler",
    "GuiNotificationAdapter",
    "LogNotificationAdapter",
    "build_adapters",
]
