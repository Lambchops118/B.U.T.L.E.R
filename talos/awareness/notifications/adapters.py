"""Adapters for the existing delivery channels: spoken voice, GUI banner, log.

- ``voice``: authenticated ``POST /speak`` on the TALOS text server, which
  enqueues a ``voice_cmd`` so the agent phrases the alert in its own voice and
  speaks it aloud. Confirmed means the text server accepted and enqueued it
  (HTTP 200 + ok) — it does NOT mean a human heard it; that limitation is
  documented (INV-14). The awareness backend still detects the condition and
  renders factual title/body deterministically; only the wording is the LLM's.
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


class _TextServerAdapter:
    """Shared POST-to-text-server delivery for the ``gui`` and ``voice``
    channels; both accept the same ``{title, body, severity}`` payload and
    confirm on HTTP 200 + ``{"ok": true}``."""

    name = "text_server"
    endpoint = "/notify"
    _confirm_detail = "enqueued"

    def __init__(self, settings: AwarenessSettings) -> None:
        self._url = settings.notify_url.rstrip("/") + self.endpoint
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
        return DeliveryResult(confirmed=True, detail=self._confirm_detail)


class GuiNotificationAdapter(_TextServerAdapter):
    name = "gui"
    endpoint = "/notify"
    _confirm_detail = "enqueued to GUI"
    confirmation_semantics = (
        "text server accepted and enqueued the banner (not proof a human saw it)"
    )


class VoiceNotificationAdapter(_TextServerAdapter):
    name = "voice"
    endpoint = "/speak"
    _confirm_detail = "enqueued to voice"
    confirmation_semantics = (
        "text server accepted and enqueued the spoken alert (not proof a human heard it)"
    )


def build_adapters(settings: AwarenessSettings) -> dict[str, object]:
    """Existing channels, keyed by name (NOTIFY-001). Insertion order is the
    fallback order the handler uses after the preferred channel, so voice (the
    present, spoken channel) comes first, then the silent GUI banner, then the
    always-available log."""
    adapters: dict[str, object] = {}
    if settings.notify_url and settings.notify_voice_enabled:
        adapters["voice"] = VoiceNotificationAdapter(settings)
    if settings.notify_url:
        adapters["gui"] = GuiNotificationAdapter(settings)
    adapters["log"] = LogNotificationAdapter()
    return adapters
