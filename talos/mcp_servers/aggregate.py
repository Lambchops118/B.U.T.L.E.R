from __future__ import annotations

from .base import create_server, register_all
from .providers import (
    register_awareness_tools,
    register_home_automation_tools,
    register_kitchen_recipe_screen_tools,
)


def create_aggregate_server():
    """
    Aggregate server used by the current local TALOS agent runtime.

    This is the compatibility entrypoint for the existing local subprocess client.
    It exposes the stable, currently-supported tool surface while the provider
    layout allows new domains to be added as separate modules or promoted to their
    own standalone MCP servers later.
    """

    server = create_server("talos-local-mcp")
    return register_all(
        server,
        [
            register_home_automation_tools,
            register_kitchen_recipe_screen_tools,
            register_awareness_tools,
        ],
    )
