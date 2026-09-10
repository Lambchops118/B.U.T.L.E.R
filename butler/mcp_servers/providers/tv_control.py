from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register(server: FastMCP) -> None:
    """
    Register TV-control tools on a FastMCP server.

    This module is intentionally a scaffold right now. Future TV-related MCP tools
    should live here so they can be exposed either through the aggregate Butler MCP
    server or through a dedicated TV-control MCP server entrypoint.
    """

    # Example future shape:
    #
    # import tv_control
    #
    # @server.tool()
    # def tv_switch_to_hdmi2() -> str:
    #     """Switch the configured TV to HDMI 2."""
    #     tv_control.switch_to_hdmi2()
    #     return "TV switched to HDMI 2."
    #
    # Keep the module present now so the server layout is explicit even before the
    # first TV-control tool is published.
    return None
