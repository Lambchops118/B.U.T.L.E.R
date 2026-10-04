"""Per-request tool access for callers outside the house.

Requests from external frontends (Discord today) can come from anyone in a
server, so they run with ``restricted`` tool access unless the frontend proves
the caller is privileged. A restricted turn sees and may run only the tools on
``BUTLER_RESTRICTED_TOOL_ALLOWLIST`` (empty by default: conversation only).

This is an allowlist on purpose. A denylist of "home control" tools would miss
proxies such as meta-tools or ``execute_tool`` wrappers and any tool added
later; an allowlist fails closed.

Privilege is proven with a dedicated shared secret, not the general text-agent
API token, because local clients (voice worker, GUI) do not send that token:
an external request is ``full`` only when it asks for it *and* carries
``X-Butler-Privileged-Token`` matching ``BUTLER_PRIVILEGED_CALLER_TOKEN``.
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

FULL = "full"
RESTRICTED = "restricted"

PRIVILEGE_HEADER = "X-Butler-Privileged-Token"


def _env_set(name: str, default: str = "") -> frozenset[str]:
    raw = os.getenv(name, default)
    return frozenset(
        token.strip().lower()
        for token in raw.replace(";", ",").split(",")
        if token.strip()
    )


def external_sources() -> frozenset[str]:
    """Request ``source`` values treated as external (restricted by default)."""
    return _env_set("BUTLER_EXTERNAL_SOURCES", "discord")


def restricted_tool_allowlist() -> frozenset[str]:
    return frozenset(
        name
        for name in (
            token.strip()
            for token in os.getenv("BUTLER_RESTRICTED_TOOL_ALLOWLIST", "").split(",")
        )
        if name
    )


def resolve_request_access(
    source: str, requested_access: Any, privilege_token: str | None
) -> str:
    """Decide the tool access for one incoming request.

    Non-external sources keep today's behavior (``full``). External sources are
    ``restricted`` unless they request ``full`` with the correct secret. An
    unset secret means no external request can ever be ``full``.
    """
    if str(source or "").strip().lower() not in external_sources():
        return FULL
    if str(requested_access or "").strip().lower() != FULL:
        return RESTRICTED
    expected = os.getenv("BUTLER_PRIVILEGED_CALLER_TOKEN", "").strip()
    provided = str(privilege_token or "").strip()
    if expected and provided and hmac.compare_digest(provided, expected):
        return FULL
    return RESTRICTED


def normalize(tool_access: str | None) -> str:
    # Anything unrecognized is treated as restricted, never as full.
    return FULL if tool_access in (None, FULL) else RESTRICTED


def is_tool_permitted(name: str, tool_access: str | None) -> bool:
    if normalize(tool_access) == FULL:
        return True
    return name in restricted_tool_allowlist()


def filter_tool_definitions(
    tool_defs: list[dict[str, Any]], tool_access: str | None
) -> list[dict[str, Any]]:
    if normalize(tool_access) == FULL:
        return tool_defs
    return [
        tool for tool in tool_defs if is_tool_permitted(str(tool.get("name") or ""), tool_access)
    ]


def refusal(name: str) -> str:
    """Structured tool result for a call the caller is not allowed to make."""
    return json.dumps(
        {
            "success": False,
            "error": "not_authorized",
            "message": (
                f"Tool '{name}' is not available to this caller. Tell the user "
                "they are not authorized to use it; do not claim it ran."
            ),
        }
    )
