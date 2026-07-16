"""Adapters for the two existing channels (ADR-015): GUI banner and log.

- ``gui``: authenticated ``POST /notify`` on the TALOS text server, which
  enqueues a deterministic display message for the pygame GUI. Confirmed
  means the text server accepted and enqueued it (HTTP 200 + ok) — it does
  NOT mean a human saw the screen; that limitation is documented.
- ``log``: structured warning in the awareness log. Confirmed means the log
  record was emitted. This is the always-available fallback channel.
"""

from __future__ import annotations

import httpx

from talos.awareness.config import AwarenessSettings
from talos.awareness.logging_utils import get_logger
from talos.awareness.notifications.base import DeliveryResult, NotificationContent

logger = get_logger("talos.awareness.notifications")


class LogNotificationAdapter:
    name = "log"
    confirmation_semantics = "structured log record emitted"

    async def send(self, content: NotificationContent) -> DeliveryResult:
        logger.warning(
            "NOTIFICATION %s — %s",  # title already carries the severity tag
            content.title,
            content.body,
            extra={"component": "notifications"},
        )
        return DeliveryResult(confirmed=True, detail="logged")


class GuiNotificationAdapter:
    name = "gui"
    confirmation_semantics = (
        "text server accepted and enqueued the banner (not proof a human saw it)"
    )

    def __init__(self, settings: AwarenessSettings) -> None:
        self._url = settings.notify_url.rstrip("/") + "/notify"
        self._token = (
            settings.notify_token.get_secret_value() if settings.notify_token else ""
        )
        self._timeout = settings.notify_timeout_seconds

    async def send(self, content: NotificationContent) -> DeliveryResult:
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._url,
                    json={
                        "title": content.title,
                        "body": content.body,
                        "severity": content.severity,
                    },
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            return DeliveryResult(confirmed=False, detail=f"http error: {exc}")
        if response.status_code != 200:
            return DeliveryResult(
                confirmed=False, detail=f"status {response.status_code}"
            )
        try:
            ok = bool(response.json().get("ok"))
        except ValueError:
            ok = False
        if not ok:
            return DeliveryResult(confirmed=False, detail="server did not confirm")
        return DeliveryResult(confirmed=True, detail="enqueued to GUI")


def build_adapters(settings: AwarenessSettings) -> dict[str, object]:
    """Only existing channels, keyed by name (NOTIFY-001)."""
    adapters: dict[str, object] = {"log": LogNotificationAdapter()}
    if settings.notify_url:
        adapters["gui"] = GuiNotificationAdapter(settings)
    return adapters
