from __future__ import annotations

import os
import sys
from typing import Iterable

from .base import create_server, register_all
from .providers import (
    register_awareness_tools,
    register_home_automation_tools,
    register_sleep_mode_tools,
    register_smart_plug_tools,
)


# Provider groups exposed by the aggregate server, keyed by the stable name the
# launcher (and BUTLER_MCP_DISABLED_PROVIDERS) uses to switch them off.
PROVIDERS: tuple[tuple[str, object], ...] = (
    ("home_automation", register_home_automation_tools),
    ("awareness", register_awareness_tools),
    ("sleep_mode", register_sleep_mode_tools),
    ("smart_plugs", register_smart_plug_tools),
)


def _env_disabled_providers() -> set[str]:
    raw = os.getenv("BUTLER_MCP_DISABLED_PROVIDERS", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def create_aggregate_server(disabled_providers: Iterable[str] | None = None):
    """
    Aggregate server used by the current local Butler agent runtime.

    This is the compatibility entrypoint for the existing local subprocess client.
    It exposes the stable, currently-supported tool surface while the provider
    layout allows new domains to be added as separate modules or promoted to their
    own standalone MCP servers later.

    ``disabled_providers`` (plus anything in ``BUTLER_MCP_DISABLED_PROVIDERS``)
    names provider groups to leave unregistered, so the agent boots without those
    tools at all. Unknown names are ignored.
    """

    disabled = {str(name).strip().lower() for name in (disabled_providers or ()) if str(name).strip()}
    disabled |= _env_disabled_providers()

    server = create_server("butler-local-mcp")
    registrars = []
    for key, registrar in PROVIDERS:
        if key in disabled:
            # stdout is the MCP protocol channel; diagnostics go to stderr.
            print(f"butler-local: provider '{key}' disabled", file=sys.stderr)
            continue
        registrars.append(registrar)
    return register_all(server, registrars)
