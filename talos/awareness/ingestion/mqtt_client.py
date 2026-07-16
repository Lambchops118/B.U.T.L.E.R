"""Resilient MQTT ingress against the existing Mosquitto broker (C2).

Persistent reconnect loop with bounded exponential backoff and jitter,
subscription restoration on every (re)connect, and truthful connection state
for the health endpoint. QoS 1 subscriptions; because the underlying client
acknowledges on receipt, delivery is at-least-once and the pipeline's
database-level idempotency absorbs redeliveries (documented in the README —
we do not claim exactly-once).

Credentials are never logged.
"""

from __future__ import annotations

import asyncio
import random
import ssl
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiomqtt

from talos.awareness.config import AwarenessSettings
from talos.awareness.ingestion.pipeline import InboundMessage
from talos.awareness.logging_utils import get_logger

logger = get_logger("talos.awareness.ingestion.mqtt")

BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0


def backoff_delay(attempt: int, *, base: float = BACKOFF_BASE_SECONDS, cap: float = BACKOFF_CAP_SECONDS) -> float:
    """Bounded exponential backoff with ±50% jitter; always within (0, 1.5*cap]."""
    exponential = min(cap, base * (2 ** max(0, attempt)))
    return exponential * random.uniform(0.5, 1.5)


class MqttIngress:
    def __init__(
        self,
        settings: AwarenessSettings,
        handler: Callable[[InboundMessage], Awaitable[Any]],
        subscriptions: list[str],
    ) -> None:
        self._settings = settings
        self._handler = handler
        self._subscriptions = subscriptions
        self._state = "disconnected"
        self._connected_since: str | None = None
        self._last_error: str | None = None
        self._reconnects = 0

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "broker": f"{self._settings.mqtt_host}:{self._settings.mqtt_port}",
            "tls": self._settings.mqtt_tls,
            "client_id": self._settings.mqtt_client_id,
            "subscriptions": list(self._subscriptions),
            "connected_since": self._connected_since,
            "reconnects": self._reconnects,
            "last_error": self._last_error,
        }

    def _tls_context(self) -> ssl.SSLContext | None:
        if not self._settings.mqtt_tls:
            return None
        context = ssl.create_default_context(
            cafile=str(self._settings.mqtt_ca_path) if self._settings.mqtt_ca_path else None
        )
        if self._settings.mqtt_client_cert and self._settings.mqtt_client_key:
            context.load_cert_chain(
                certfile=str(self._settings.mqtt_client_cert),
                keyfile=str(self._settings.mqtt_client_key),
            )
        return context

    def _client(self) -> aiomqtt.Client:
        return aiomqtt.Client(
            hostname=self._settings.mqtt_host,
            port=self._settings.mqtt_port,
            identifier=self._settings.mqtt_client_id,
            username=self._settings.mqtt_username,
            password=(
                self._settings.mqtt_password.get_secret_value()
                if self._settings.mqtt_password
                else None
            ),
            keepalive=self._settings.mqtt_keepalive,
            tls_context=self._tls_context(),
        )

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0
        while not stop.is_set():
            self._state = "connecting"
            try:
                async with self._client() as client:
                    self._state = "connected"
                    self._connected_since = datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    )
                    self._last_error = None
                    attempt = 0
                    for subscription in self._subscriptions:
                        await client.subscribe(subscription, qos=1)
                    logger.info(
                        "MQTT connected to %s:%s; subscribed to %s",
                        self._settings.mqtt_host,
                        self._settings.mqtt_port,
                        self._subscriptions,
                        extra={"component": "ingestion"},
                    )
                    async for message in client.messages:
                        if stop.is_set():
                            break
                        payload = message.payload
                        if isinstance(payload, str):
                            payload = payload.encode("utf-8")
                        elif payload is None:
                            payload = b""
                        await self._handler(
                            InboundMessage(
                                topic=str(message.topic),
                                payload=bytes(payload),
                                retained=bool(message.retain),
                            )
                        )
            except aiomqtt.MqttError as exc:
                self._state = "disconnected"
                self._connected_since = None
                self._last_error = str(exc)[:300]
                if stop.is_set():
                    break
                self._reconnects += 1
                delay = backoff_delay(attempt)
                attempt += 1
                logger.warning(
                    "MQTT connection lost (%s); reconnecting in %.1fs",
                    self._last_error,
                    delay,
                    extra={"component": "ingestion"},
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        self._state = "stopped"
