from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP

from butler.services import smart_plugs


def register(server: FastMCP) -> None:
    """Register smart plug/switch control tools (Tapo P110M, Kasa KS220) on a FastMCP server."""

    known_ids = smart_plugs.device_ids()
    ids_hint = ", ".join(known_ids) if known_ids else "none configured yet"

    @server.tool()
    def list_smart_plugs() -> str:
        """List every configured smart plug/switch, its id, and host."""
        return smart_plugs.list_devices()

    @server.tool(
        description=(
            "Turn a smart plug or switch on or off. device_id must be one of "
            f"the configured ids ({ids_hint}) or the literal \"all\" to control "
            "every light at once (appliances such as the coffee pot are not "
            "included in \"all\"; switch them by id). state must be \"on\" or \"off\"."
        )
    )
    async def set_smart_plug(device_id: str, state: str) -> str:
        normalized_state = str(state or "").strip().lower()
        if normalized_state not in ("on", "off"):
            raise ValueError("state must be 'on' or 'off'")
        on = normalized_state == "on"

        normalized_id = str(device_id or "").strip()
        # FastMCP calls tools inside its running event loop, and the service uses
        # asyncio.run() internally, so hand the blocking call to a worker thread.
        if normalized_id.lower() == "all":
            return await asyncio.to_thread(smart_plugs.set_all_devices_power, on)
        return await asyncio.to_thread(smart_plugs.set_device_power, normalized_id, on)
