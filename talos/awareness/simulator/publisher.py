"""Simulated device traffic for ingestion testing (Phase 2 acceptance).

Emits the registered ``sim_device`` source's topics (``home/sim/greenhouse/…``)
plus deliberate failure cases: duplicates, delayed/out-of-order sequences,
gaps, reboots, malformed and oversized payloads, unauthorized topics, source
spoofing, and retained messages.

The default broker is the local TEST Mosquitto (127.0.0.1:1885). Pointing at
the production Raspberry Pi broker requires an explicit ``--host``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

BASE_TOPIC = "home/sim/greenhouse"


@dataclass(frozen=True)
class SimMessage:
    topic: str
    payload: bytes
    retain: bool = False
    delay_before: float = 0.0
    note: str = ""


def _now_iso(offset_seconds: float = 0.0) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return moment.isoformat(timespec="milliseconds")


def _body(**fields: Any) -> bytes:
    return json.dumps(fields, ensure_ascii=True).encode("utf-8")


@dataclass
class SimulatedDevice:
    """Keeps boot/sequence continuity so scenarios compose coherently."""

    boot_id: str = field(default_factory=lambda: f"boot-{uuid4().hex[:8]}")
    sequence: int = 0

    def _next(self) -> int:
        self.sequence += 1
        return self.sequence

    def _system_fields(self, *, observed_offset: float = 0.0) -> dict[str, Any]:
        return {
            "event_id": str(uuid4()),
            "observed_at": _now_iso(observed_offset),
            "sequence": self._next(),
            "boot_id": self.boot_id,
        }

    # --- normal traffic ---------------------------------------------------

    def heartbeat(self) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/heartbeat",
                payload=_body(**self._system_fields()),
                note="heartbeat",
            )
        ]

    def temperature(self, value: float = 72.4) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/telemetry/temperature",
                payload=_body(value=value, unit="F", **self._system_fields()),
                note="temperature telemetry",
            )
        ]

    def moisture(self, value: float = 0.41) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/telemetry/moisture",
                payload=_body(value=value, **self._system_fields()),
                note="moisture telemetry",
            )
        ]

    def pump_state(self, running: bool) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/state",
                payload=_body(
                    payload={"pump": "on" if running else "off"}, **self._system_fields()
                ),
                note=f"pump state {'on' if running else 'off'}",
            )
        ]

    def overflow(self) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/event",
                payload=_body(
                    event_type="plant.overflow.detected",
                    severity="critical",
                    payload={"overflow": True, "zone": 1},
                    **self._system_fields(),
                ),
                note="overflow event",
            )
        ]

    # --- distributed-failure scenarios -------------------------------------

    def duplicate(self) -> list[SimMessage]:
        fields = self._system_fields()
        payload = _body(value=70.1, **fields)
        message = SimMessage(
            topic=f"{BASE_TOPIC}/telemetry/temperature",
            payload=payload,
            note="duplicate delivery (same event_id + sequence, twice)",
        )
        return [message, SimMessage(message.topic, message.payload, note="duplicate copy")]

    def delayed(self, delay_seconds: float = 600.0) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/telemetry/temperature",
                payload=_body(
                    value=69.9, **self._system_fields(observed_offset=-delay_seconds)
                ),
                note=f"delayed observation ({delay_seconds:.0f}s old)",
            )
        ]

    def out_of_order(self) -> list[SimMessage]:
        newer = self._system_fields()
        older = {
            "event_id": str(uuid4()),
            "observed_at": _now_iso(-30),
            "sequence": max(1, newer["sequence"] - 2),
            "boot_id": self.boot_id,
        }
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/telemetry/temperature",
                payload=_body(value=71.0, **newer),
                note="newer message first",
            ),
            SimMessage(
                topic=f"{BASE_TOPIC}/telemetry/temperature",
                payload=_body(value=70.5, **older),
                note="older message second (out of order)",
            ),
        ]

    def sequence_gap(self, gap: int = 5) -> list[SimMessage]:
        self.sequence += gap
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/heartbeat",
                payload=_body(**self._system_fields()),
                note=f"sequence gap of {gap}",
            )
        ]

    def reboot(self) -> list[SimMessage]:
        self.boot_id = f"boot-{uuid4().hex[:8]}"
        self.sequence = 0
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/event",
                payload=_body(
                    event_type="sim.device.rebooted",
                    payload={"reason": "simulated"},
                    **self._system_fields(),
                ),
                note="reboot (new boot_id, sequence reset)",
            )
        ]

    def malformed(self) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/event",
                payload=b"{this is not json",
                note="malformed JSON",
            )
        ]

    def unauthorized(self) -> list[SimMessage]:
        return [
            SimMessage(
                topic="home/rogue/intruder/event",
                payload=_body(event_type="rogue.event", payload={}),
                note="unauthorized topic (no registered source)",
            )
        ]

    def spoofed_source(self) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/event",
                payload=_body(
                    event_type="sim.spoof.attempt",
                    source_id="fan_pico",
                    payload={},
                    **self._system_fields(),
                ),
                note="payload claims another source_id (spoof)",
            )
        ]

    def oversized(self, size_bytes: int = 128 * 1024) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/event",
                payload=_body(
                    event_type="sim.oversized",
                    payload={"blob": "x" * size_bytes},
                    **self._system_fields(),
                ),
                note=f"oversized payload (~{size_bytes} bytes)",
            )
        ]

    def retained_state(self) -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/state",
                payload=_body(
                    payload={"pump": "off", "note": "retained last-known state"},
                    **self._system_fields(),
                ),
                retain=True,
                note="retained state message",
            )
        ]

    def command_ack(self, command_id: str = "cmd-demo") -> list[SimMessage]:
        return [
            SimMessage(
                topic=f"{BASE_TOPIC}/event",
                payload=_body(
                    event_type="sim.command.acknowledged",
                    correlation_id=command_id,
                    payload={"command_id": command_id, "result": "ok"},
                    **self._system_fields(),
                ),
                note="command acknowledgement",
            )
        ]


_SCENARIOS = {
    "normal": lambda device: device.heartbeat() + device.temperature() + device.moisture(),
    "overflow": lambda device: device.pump_state(True) + device.overflow() + device.pump_state(False),
    "duplicate": lambda device: device.duplicate(),
    "delayed": lambda device: device.delayed(),
    "out_of_order": lambda device: device.out_of_order(),
    "sequence_gap": lambda device: device.sequence_gap(),
    "reboot": lambda device: device.reboot() + device.heartbeat(),
    "malformed": lambda device: device.malformed(),
    "unauthorized": lambda device: device.unauthorized(),
    "spoofed_source": lambda device: device.spoofed_source(),
    "oversized": lambda device: device.oversized(),
    "retained": lambda device: device.retained_state(),
    "command_ack": lambda device: device.command_ack(),
}

SCENARIO_NAMES = sorted(_SCENARIOS) + ["suite"]


def build_scenario(name: str, device: SimulatedDevice | None = None) -> list[SimMessage]:
    device = device or SimulatedDevice()
    if name == "suite":
        messages: list[SimMessage] = []
        for scenario_name in sorted(_SCENARIOS):
            messages.extend(_SCENARIOS[scenario_name](device))
        return messages
    if name not in _SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIO_NAMES}")
    return _SCENARIOS[name](device)


async def publish_messages(
    messages: list[SimMessage],
    *,
    host: str,
    port: int,
    client_id: str = "talos-awareness-sim",
    quiet: bool = False,
) -> int:
    import aiomqtt

    async with aiomqtt.Client(hostname=host, port=port, identifier=client_id) as client:
        for message in messages:
            if message.delay_before > 0:
                await asyncio.sleep(message.delay_before)
            await client.publish(
                message.topic, message.payload, qos=1, retain=message.retain
            )
            if not quiet:
                print(f"published [{message.note or message.topic}] -> {message.topic}")
    return len(messages)
