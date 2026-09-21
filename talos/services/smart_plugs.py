from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

from kasa import Credentials, Discover

from talos.config import load_environment

load_environment()

# Non-secret device list (host/name only) lives in settings.env. Example:
# TALOS_SMART_PLUGS=[{"id":"living_room_lamp","name":"living room lamp","host":"192.168.1.101"}]
_DEVICES_ENV = "TALOS_SMART_PLUGS"
_DISCOVER_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class PlugConfig:
    id: str
    name: str
    host: str


def _load_devices() -> dict[str, PlugConfig]:
    raw = os.getenv(_DEVICES_ENV, "").strip()
    if not raw:
        return {}
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{_DEVICES_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise RuntimeError(f"{_DEVICES_ENV} must be a JSON array of device objects")

    devices: dict[str, PlugConfig] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError(f"{_DEVICES_ENV} entries must be objects")
        device_id = str(entry.get("id", "")).strip()
        host = str(entry.get("host", "")).strip()
        if not device_id or not host:
            raise RuntimeError(f"{_DEVICES_ENV} entries require 'id' and 'host'")
        name = str(entry.get("name", "")).strip() or device_id
        devices[device_id] = PlugConfig(id=device_id, name=name, host=host)
    return devices


def _credentials() -> Credentials | None:
    # Only the Tapo (P110M) devices need an account login for local control;
    # the legacy Kasa protocol the KS220s speak needs none. Passing credentials
    # to a device that doesn't need them is harmless during discovery.
    username = os.getenv("TAPO_USERNAME", "").strip()
    password = os.getenv("TAPO_PASSWORD", "").strip()
    if username and password:
        return Credentials(username=username, password=password)
    return None


async def _set_power(host: str, on: bool) -> None:
    device = await Discover.discover_single(
        host, credentials=_credentials(), timeout=_DISCOVER_TIMEOUT_SECONDS
    )
    try:
        await device.update()
        if on:
            await device.turn_on()
        else:
            await device.turn_off()
    finally:
        await device.disconnect()


def device_ids() -> list[str]:
    return sorted(_load_devices())


def list_devices() -> str:
    devices = _load_devices()
    if not devices:
        return f"No smart plugs configured. Set {_DEVICES_ENV} in settings.env."
    lines = [f"{plug.id}: {plug.name} ({plug.host})" for plug in devices.values()]
    return "\n".join(lines)


def set_device_power(device_id: str, on: bool) -> str:
    devices = _load_devices()
    plug = devices.get(device_id)
    if plug is None:
        raise ValueError(
            f"Unknown device_id {device_id!r}. Known devices: {sorted(devices) or 'none configured'}"
        )
    try:
        asyncio.run(_set_power(plug.host, on))
    except Exception as exc:
        raise RuntimeError(f"Failed to turn {plug.name} {'on' if on else 'off'}: {exc}") from exc
    return f"{plug.name} turned {'on' if on else 'off'}."


def set_all_devices_power(on: bool) -> str:
    devices = _load_devices()
    if not devices:
        raise RuntimeError(f"No smart plugs configured. Set {_DEVICES_ENV} in settings.env.")

    async def _run_all() -> list[BaseException | None]:
        return await asyncio.gather(
            *(_set_power(plug.host, on) for plug in devices.values()),
            return_exceptions=True,
        )

    results = asyncio.run(_run_all())
    label = "on" if on else "off"
    failures = [
        f"{plug.name}: {error}"
        for plug, error in zip(devices.values(), results)
        if isinstance(error, BaseException)
    ]
    succeeded = len(devices) - len(failures)
    summary = f"Turned {label} {succeeded}/{len(devices)} devices."
    if failures:
        summary += " Failed: " + "; ".join(failures)
    return summary
