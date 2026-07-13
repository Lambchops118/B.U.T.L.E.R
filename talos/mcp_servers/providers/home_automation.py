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
    def water_plants(pot_number: int) -> str:
        """Send a signal to the pump circuit to water either pot 1 or pot 2."""
        return actions.water_plants(pot_number)

    @server.tool()
    def turn_on_lights(room: str) -> str:
        """Turn on the lights in a specific room."""
        return actions.turn_on_lights(room)

    @server.tool()
    def toggle_fan(status: int) -> str:
        """Toggle the fan on (1) or off (0)."""
        return actions.toggle_fan(status)
