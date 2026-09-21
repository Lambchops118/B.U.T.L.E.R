from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from talos.services import smart_plugs


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
            "every device at once. state must be \"on\" or \"off\"."
        )
    )
    def set_smart_plug(device_id: str, state: str) -> str:
        normalized_state = str(state or "").strip().lower()
        if normalized_state not in ("on", "off"):
            raise ValueError("state must be 'on' or 'off'")
        on = normalized_state == "on"

        normalized_id = str(device_id or "").strip()
        if normalized_id.lower() == "all":
            return smart_plugs.set_all_devices_power(on)
        return smart_plugs.set_device_power(normalized_id, on)
