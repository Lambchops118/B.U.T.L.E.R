from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

from butler.config import load_environment

load_environment()

# Non-secret device list (host/name only) lives in settings.env. Example:
# BUTLER_SMART_PLUGS=[{"id":"living_room_lamp","name":"living room lamp","host":"192.168.1.101"}]
_DEVICES_ENV = "BUTLER_SMART_PLUGS"
_DISCOVER_TIMEOUT_SECONDS = 5

# The KS220 switches intermittently (roughly 1 attempt in 4, sometimes in short
# bursts) fail the KLAP handshake with an AuthenticationError ("Server response
# doesn't match our challenge") even with correct credentials, and accept a
# later attempt, so retry a few times before treating it as a real failure.
_AUTH_RETRIES = 4
_AUTH_RETRY_DELAY_SECONDS = 0.5

# "light" devices are what the "all" shortcut controls; "appliance" devices
# (e.g. the coffee pot) are only ever switched by their own id.
_KINDS = ("light", "appliance")


@dataclass(frozen=True)
class PlugConfig:
    id: str
    name: str
    host: str
    kind: str = "light"


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
        kind = str(entry.get("kind", "light")).strip().lower() or "light"
        if kind not in _KINDS:
            raise RuntimeError(
                f"{_DEVICES_ENV} entry {device_id!r} has kind {kind!r}; expected one of {_KINDS}"
            )
        devices[device_id] = PlugConfig(id=device_id, name=name, host=host, kind=kind)
    return devices


def _kasa():
    # Imported lazily so a missing python-kasa disables only these tools rather
    # than failing the import of the whole aggregate MCP server.
    try:
        import kasa
    except ImportError as exc:
        raise RuntimeError(
            "python-kasa is not installed. Install it with: pip install python-kasa==0.7.7"
        ) from exc
    return kasa


def _credentials():
    # Current Tapo (P110M) and Kasa (KS220) firmware both need the TP-Link
    # account login for local control. Passing credentials to a device that
    # doesn't need them is harmless during discovery.
    username = os.getenv("TAPO_USERNAME", "").strip()
    password = os.getenv("TAPO_PASSWORD", "").strip()
    if username and password:
        return _kasa().Credentials(username=username, password=password)
    return None


async def _connect(host: str):
    kasa = _kasa()
    for attempt in range(_AUTH_RETRIES + 1):
        device = None
        try:
            device = await kasa.Discover.discover_single(
                host, credentials=_credentials(), timeout=_DISCOVER_TIMEOUT_SECONDS
            )
            await device.update()
            return device
        except BaseException as exc:
            if device is not None:
                await device.disconnect()
            if isinstance(exc, kasa.exceptions.AuthenticationError) and attempt < _AUTH_RETRIES:
                await asyncio.sleep(_AUTH_RETRY_DELAY_SECONDS)
                continue
            raise


async def _set_power(host: str, on: bool) -> None:
    device = await _connect(host)
    try:
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
    lines = [
        f"{plug.id}: {plug.name} ({plug.host})"
        + ("" if plug.kind == "light" else f" [{plug.kind}, not included in \"all\"]")
        for plug in devices.values()
    ]
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
    """Switch every configured light; appliances such as the coffee pot are left alone."""
    devices = {key: plug for key, plug in _load_devices().items() if plug.kind == "light"}
    if not devices:
        raise RuntimeError(f"No lights configured. Set {_DEVICES_ENV} in settings.env.")

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
    summary = f"Turned {label} {succeeded}/{len(devices)} lights."
    if failures:
        summary += " Failed: " + "; ".join(failures)
    return summary
