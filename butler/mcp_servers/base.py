from __future__ import annotations

from typing import Callable

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - import error depends on local environment
    raise RuntimeError(
        "The MCP Python SDK is required for Butler MCP servers. "
        'Install it with: pip install "mcp[cli]"'
    ) from exc


ServerRegistrar = Callable[[FastMCP], None]


def create_server(name: str) -> FastMCP:
    """Create a FastMCP server with settings suitable for local and remote use."""
    return FastMCP(
        name,
        json_response=True,
        stateless_http=True,
    )


def register_all(server: FastMCP, registrars: list[ServerRegistrar]) -> FastMCP:
    for registrar in registrars:
        registrar(server)
    return server
