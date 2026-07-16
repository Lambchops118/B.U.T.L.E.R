from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from talos.services import home_automation as actions


def register(server: FastMCP) -> None:
    """Register the existing home automation tools on a FastMCP server."""

    @server.tool()
    def get_current_datetime() -> str:
        """Get the current local date, time, year, and timezone for TALOS."""
        return actions.get_current_datetime()

    @server.tool()
    def get_current_weather(location: str = "") -> str:
        """Get the current weather, temperature, humidity, UV index, and related details.

        Leave location blank to use the configured home location. Only pass a
        location when the user explicitly names a different city, zip code, or
        place.
        """
        return actions.get_current_weather(location)

    @server.tool()
    def water_plants(pot_number: int, idempotency_key: str = "") -> str:
        """Request the registered pump action for pot 1 or 2. This returns an
        audited lifecycle status, not an immediate claim of physical success.
        Reuse idempotency_key when retrying the same user intent."""
        return actions.water_plants(
            pot_number, idempotency_key=idempotency_key or None
        )

    @server.tool()
    def turn_on_lights(room: str) -> str:
        """Turn on the lights in a specific room."""
        return actions.turn_on_lights(room)

    @server.tool()
    def toggle_fan(status: int, idempotency_key: str = "") -> str:
        """Request the registered fan action (1=on, 0=off). This returns an
        audited lifecycle status; reuse idempotency_key for a retry of the
        same intent."""
        return actions.toggle_fan(status, idempotency_key=idempotency_key or None)
