from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from talos.filesystem_diagnostics import resolve_allowed_root_paths as _resolve_allowed_root_paths
from talos.tool_arguments import parse_tool_arguments


class McpProtocolError(RuntimeError):
    pass


# Lifecycle modes describe *when* a provider is allowed to start, independently
# of its transport. They isolate heavyweight providers (such as KiCad) from the
# generic tool-discovery path so ordinary chat never blocks on a slow backend.
LIFECYCLE_EAGER = "eager"
LIFECYCLE_LAZY = "lazy"
LIFECYCLE_SIDECAR_MANUAL = "sidecar_manual"
LIFECYCLE_SIDECAR_AUTOSTART = "sidecar_autostart"

# Providers in these modes are never started during generic tool discovery.
_DEFERRED_LIFECYCLE_MODES = {
    LIFECYCLE_LAZY,
    LIFECYCLE_SIDECAR_MANUAL,
    LIFECYCLE_SIDECAR_AUTOSTART,
}

DEFAULT_FILESYSTEM_SERVER_NAME = "filesystem"
DEFAULT_FILESYSTEM_DIAGNOSTICS_SERVER_NAME = "filesystem-diagnostics"
DEFAULT_FILESYSTEM_TOOL_PREFIX = "fs_"
DEFAULT_MINECRAFT_FILESYSTEM_SERVER_NAME = "minecraft-filesystem"
DEFAULT_MINECRAFT_SEARCH_SERVER_NAME = "minecraft-search"
DEFAULT_MINECRAFT_FILESYSTEM_TOOL_PREFIX = "minecraft_fs_"
DEFAULT_MINECRAFT_SEARCH_TOOL_PREFIX = "minecraft_"
FILESYSTEM_MUTATING_TOOLS = {
    "create_directory",
    "write_file",
    "edit_file",
    "move_file",
    "delete_file",
    "delete_directory",
}
MINECRAFT_FILESYSTEM_MUTATING_TOOLS = {
    "create_directory",
    "write_file",
    "edit_file",
    "move_file",
    "delete_file",
    "delete_directory",
}
FILESYSTEM_CONFIG_KIND = "filesystem"
FILESYSTEM_DIAGNOSTICS_CONFIG_KIND = "filesystem_diagnostics"
MINECRAFT_FILESYSTEM_CONFIG_KIND = "minecraft_filesystem"
MINECRAFT_DIAGNOSTICS_CONFIG_KIND = "minecraft_diagnostics"


@dataclass
class McpServerConfig:
    name: str
    transport: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    auth_token: str | None = None
    auth_token_env: str | None = None
    tls_verify: bool = True
    tls_ca_bundle: str | None = None
    tool_prefix: str = ""
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    mode: str = ""
    kind: str = ""

    def normalized_transport(self) -> str:
        transport = self.transport.strip().lower().replace("-", "_")
        if transport == "http":
            return "streamable_http"
        return transport

    def lifecycle_mode(self) -> str:
        """Normalize the configured provider mode into a lifecycle policy.

        ``stdio``/``eager``/unset all map to the legacy eager-blocking behavior
        so existing deployments keep working. Everything else is deferred from
        generic tool discovery.
        """

        mode = (self.mode or "").strip().lower().replace("-", "_")
        if mode in ("", "stdio", "eager", "blocking"):
            return LIFECYCLE_EAGER
        if mode in ("lazy", "on_demand", "ondemand"):
            return LIFECYCLE_LAZY
        if mode in ("sidecar_manual", "manual"):
            return LIFECYCLE_SIDECAR_MANUAL
        if mode in ("sidecar_autostart", "autostart", "background", "warm"):
            return LIFECYCLE_SIDECAR_AUTOSTART
        return LIFECYCLE_EAGER

    def is_eager(self) -> bool:
        return self.lifecycle_mode() == LIFECYCLE_EAGER

    def is_deferred(self) -> bool:
        """True when generic tool discovery must not start this provider."""

        return self.lifecycle_mode() in _DEFERRED_LIFECYCLE_MODES

    def is_autostart(self) -> bool:
        return self.lifecycle_mode() == LIFECYCLE_SIDECAR_AUTOSTART

    def exposed_tool_name(self, raw_name: str) -> str:
        return f"{self.tool_prefix}{raw_name}" if self.tool_prefix else raw_name


@dataclass
class McpServerStatus:
    name: str
    transport: str
    status: str = "not_started"
    healthy: bool = False
    failure_count: int = 0
    last_error: str | None = None
    last_transition: float = field(default_factory=time.time)
    next_retry_at: float | None = None
    mode: str = LIFECYCLE_EAGER
    warming_since: float | None = None

    def elapsed_seconds(self) -> float | None:
        """Seconds spent in the current warmup, if the provider is warming."""

        if self.warming_since is None or self.status not in {"warming", "starting", "reconnecting"}:
            return None
        return max(0.0, time.time() - self.warming_since)

    def detail(self) -> str:
        """Compact, model-friendly description of the provider phase."""

        elapsed = self.elapsed_seconds()
        if self.status == "warming":
            if elapsed is not None:
                return f"sidecar is warming, {elapsed:.0f}s elapsed"
            return "sidecar is warming"
        if self.status in {"starting", "reconnecting"}:
            if elapsed is not None:
                return f"provider is starting, {elapsed:.0f}s elapsed"
            return "provider is starting"
        if self.status == "healthy":
            return "ready"
        if self.status == "not_started":
            return "not started"
        if self.status in {"degraded", "failed"} and self.last_error:
            return f"{self.status}: {self.last_error}"
        return self.status

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "mode": self.mode,
            "status": self.status,
            "ready": self.healthy,
            "healthy": self.healthy,
            "failure_count": self.failure_count,
            "last_error": self.last_error,
            "last_transition": self.last_transition,
            "next_retry_at": self.next_retry_at,
            "elapsed_seconds": self.elapsed_seconds(),
            "detail": self.detail(),
        }


class _ServerConnection:
    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._exit_stack: AsyncExitStack | None = None
        self._session: Any = None

    @property
    def is_running(self) -> bool:
        return self._session is not None

    async def start(self) -> None:
        if self._session is not None:
            return

        try:
            from mcp import ClientSession, StdioServerParameters
        except ImportError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError(
                "The MCP Python SDK is required for TALOS MCP clients. "
                "The current SDK requires Python 3.10+. "
                'Install it in a compatible environment with: pip install "mcp[cli]"'
            ) from exc

        stack = AsyncExitStack()
        try:
            transport = self.config.normalized_transport()
            if transport == "stdio":
                if not self.config.command:
                    raise ValueError(f"stdio MCP server '{self.config.name}' is missing 'command'.")
                from mcp.client.stdio import stdio_client

                stdio_kwargs: dict[str, Any] = {
                    "command": self.config.command,
                    "args": list(self.config.args),
                }
                if self.config.env:
                    stdio_kwargs["env"] = self._merged_env()
                if self.config.cwd:
                    stdio_kwargs["cwd"] = self.config.cwd
                server_params = StdioServerParameters(**stdio_kwargs)
                read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
            elif transport == "streamable_http":
                if not self.config.url:
                    raise ValueError(
                        f"streamable_http MCP server '{self.config.name}' is missing 'url'."
                    )
                try:
                    import httpx
                    from mcp.client.streamable_http import streamable_http_client
                except ImportError as exc:  # pragma: no cover - depends on SDK version/env
                    raise RuntimeError(
                        "Remote MCP over Streamable HTTP requires the MCP SDK's "
                        "'streamable_http_client' transport and httpx."
                    ) from exc

                verify: bool | str = self.config.tls_verify
                if self.config.tls_ca_bundle:
                    verify = self.config.tls_ca_bundle
                http_client = httpx.AsyncClient(
                    headers=self._resolved_headers(),
                    follow_redirects=True,
                    timeout=self.config.timeout_seconds,
                    verify=verify,
                )
                managed_http_client = await stack.enter_async_context(http_client)
                streams = await stack.enter_async_context(
                    streamable_http_client(
                        self.config.url,
                        http_client=managed_http_client,
                    )
                )
                read_stream, write_stream = streams[0], streams[1]
            else:
                raise ValueError(
                    f"Unsupported MCP transport '{self.config.transport}' for server '{self.config.name}'."
                )

            self._session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await self._session.initialize()
            self._exit_stack = stack
        except BaseException:
            await stack.aclose()
            raise

    async def stop(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    async def list_tools(self) -> Any:
        session = self._require_session()
        return await session.list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = self._require_session()
        return await session.call_tool(name, arguments)

    async def list_resources(self) -> Any:
        session = self._require_session()
        method = getattr(session, "list_resources", None)
        if method is None:
            raise RuntimeError(f"MCP server '{self.config.name}' does not support resources/list.")
        return await method()

    async def list_resource_templates(self) -> Any:
        session = self._require_session()
        method = getattr(session, "list_resource_templates", None)
        if method is None:
            raise RuntimeError(
                f"MCP server '{self.config.name}' does not support resources/templates/list."
            )
        return await method()

    async def read_resource(self, uri: str) -> Any:
        session = self._require_session()
        method = getattr(session, "read_resource", None)
        if method is None:
            raise RuntimeError(f"MCP server '{self.config.name}' does not support resources/read.")
        return await method(uri)

    def _require_session(self) -> Any:
        if self._session is None:
            raise RuntimeError(f"MCP server '{self.config.name}' is not running.")
        return self._session

    def _resolved_headers(self) -> dict[str, str]:
        headers = dict(self.config.headers)

        auth_token = self.config.auth_token
        if not auth_token and self.config.auth_token_env:
            auth_token = os.getenv(self.config.auth_token_env, "").strip()
            if not auth_token:
                raise ValueError(
                    f"MCP server '{self.config.name}' expects auth token env "
                    f"'{self.config.auth_token_env}', but it is not set."
                )
        if auth_token:
            headers.setdefault("Authorization", f"Bearer {auth_token}")
        return headers

    def _merged_env(self) -> dict[str, str]:
        merged = dict(os.environ)
        for key, value in self.config.env.items():
            merged[str(key)] = str(value)
        return merged


class LocalMcpClient:
    def __init__(
        self,
        configs: list[McpServerConfig],
        *,
        bridge_timeout_seconds: float | None = None,
        reconnect_attempts: int | None = None,
        reconnect_backoff_seconds: float | None = None,
        failure_threshold: int | None = None,
    ) -> None:
        if not configs:
            raise ValueError("At least one MCP server config is required.")

        self._configs = list(configs)
        self._connections = {config.name: _ServerConnection(config) for config in self._configs}
        self._status = {
            config.name: McpServerStatus(
                name=config.name,
                transport=config.normalized_transport(),
                mode=config.lifecycle_mode(),
            )
            for config in self._configs
        }
        max_config_timeout = max(config.timeout_seconds for config in self._configs)
        self._bridge_timeout_seconds = (
            bridge_timeout_seconds
            if bridge_timeout_seconds is not None
            else float(os.getenv("TALOS_MCP_BRIDGE_TIMEOUT", str(max(max_config_timeout + 5.0, 30.0))))
        )
        self._reconnect_attempts = (
            reconnect_attempts
            if reconnect_attempts is not None
            else max(0, int(os.getenv("TALOS_MCP_RECONNECT_ATTEMPTS", "1")))
        )
        self._reconnect_backoff_seconds = (
            reconnect_backoff_seconds
            if reconnect_backoff_seconds is not None
            else max(0.0, float(os.getenv("TALOS_MCP_RECONNECT_BACKOFF", "1.0")))
        )
        self._failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else max(1, int(os.getenv("TALOS_MCP_FAILURE_THRESHOLD", "3")))
        )

        self._lock = threading.Lock()
        self._tool_cache: list[dict[str, Any]] | None = None
        self._tool_routes: dict[str, tuple[str, str]] = {}
        self._resource_cache: list[dict[str, Any]] | None = None
        self._resource_template_cache: list[dict[str, Any]] | None = None
        self._resource_routes: dict[str, list[str]] = {}
        self._pending_starts: dict[str, asyncio.Task] = {}

        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._loop is not None:
                return

            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=self._run_loop, args=(loop,), daemon=True)
            thread.start()

            self._loop = loop
            self._loop_thread = thread
            try:
                self._run_coro(self._async_start())
            except Exception:
                self._shutdown_loop()
                raise

    def stop(self) -> None:
        with self._lock:
            if self._loop is None:
                return
            try:
                self._run_coro(self._async_stop())
            finally:
                self._shutdown_loop()
                self._tool_cache = None
                self._tool_routes = {}
                self._resource_cache = None
                self._resource_template_cache = None
                self._resource_routes = {}

    def list_tools(self, refresh: bool = False) -> list[dict[str, Any]]:
        self.start()
        if self._tool_cache is not None and not refresh:
            return list(self._tool_cache)

        result = self._run_coro(self._async_list_tools())
        if not isinstance(result, list):
            raise McpProtocolError("MCP server returned an invalid tools/list payload.")

        self._tool_cache = result
        return list(result)

    def openai_tool_definitions(self) -> list[dict[str, Any]]:
        definitions = []
        for tool in self.list_tools():
            description = tool.get("description", "")
            parameters = tool.get("inputSchema", {"type": "object", "properties": {}})
            description, parameters = self._decorate_openai_tool_schema(
                tool["name"], description, parameters
            )
            definitions.append(
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": description,
                    "parameters": parameters,
                }
            )
        return definitions

    def call_tool(self, name: str, arguments: str | dict[str, Any] | None = None) -> str:
        self.start()
        if not self._tool_routes:
            self.list_tools()
        parsed_arguments = self._parse_arguments(arguments)
        return self._run_coro(self._async_call_tool(name, parsed_arguments))

    def list_resources(self, refresh: bool = False) -> list[dict[str, Any]]:
        self.start()
        if self._resource_cache is not None and not refresh:
            return list(self._resource_cache)

        result = self._run_coro(self._async_list_resources())
        if not isinstance(result, list):
            raise McpProtocolError("MCP server returned an invalid resources/list payload.")

        self._resource_cache = result
        return list(result)

    def list_resource_templates(self, refresh: bool = False) -> list[dict[str, Any]]:
        self.start()
        if self._resource_template_cache is not None and not refresh:
            return list(self._resource_template_cache)

        result = self._run_coro(self._async_list_resource_templates())
        if not isinstance(result, list):
            raise McpProtocolError(
                "MCP server returned an invalid resources/templates/list payload."
            )

        self._resource_template_cache = result
        return list(result)

    def read_resource(self, uri: str, server: str | None = None) -> str:
        self.start()
        return self._run_coro(self._async_read_resource(uri, server))

    def list_server_status(self, refresh: bool = False) -> list[dict[str, Any]]:
        self.start()
        return [self._status[config.name].as_dict() for config in self._configs]

    def list_tool_inventory(self, refresh: bool = False) -> dict[str, Any]:
        tools = self.list_tools(refresh=refresh)
        compact_tools: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("name") or "")
            server_name, raw_name = self._tool_routes.get(name, ("", name))
            schema = tool.get("inputSchema")
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            compact_tools.append(
                {
                    "name": name,
                    "server": server_name,
                    "rawName": raw_name,
                    "parameters": list(properties.keys()) if isinstance(properties, dict) else [],
                }
            )
        return {
            "tool_count": len(compact_tools),
            "tools": compact_tools,
            "servers": self.list_server_status(),
        }

    def retry_server(self, server: str | None = None) -> list[dict[str, Any]]:
        self.start()
        self._run_coro(self._async_retry_servers(server))
        return self.list_server_status()

    def start_server(self, server: str, *, background: bool = True) -> list[dict[str, Any]]:
        """Explicitly start (or warm) a named provider without restarting TALOS.

        ``background=True`` (the default) returns immediately while the provider
        warms, so a foreground turn is never frozen waiting on a heavyweight
        backend. ``background=False`` waits for the provider to come up.
        """

        self.start()
        requested = server.strip() if isinstance(server, str) else ""
        if not requested or requested not in self._connections:
            raise KeyError(requested)
        self._run_coro(self._async_start_server(requested, background=background))
        return self.list_server_status()

    async def _async_start(self, *, force_retry: bool = False) -> None:
        for config in self._configs:
            if config.is_deferred():
                # Deferred providers (lazy / sidecar) must not block agent boot.
                # Autostart providers warm in the background; lazy/manual ones
                # wait until a request explicitly needs them.
                if config.is_autostart():
                    self._schedule_background_start(config.name)
                continue
            await self._async_ensure_started(config.name, force_retry=force_retry)

    async def _async_retry_servers(self, server: str | None = None) -> None:
        requested = server.strip() if isinstance(server, str) else ""
        if requested:
            if requested not in self._connections:
                raise KeyError(requested)
            names = [requested]
        else:
            names = [
                config.name
                for config in self._configs
                if not self._status[config.name].healthy
            ]

        for name in names:
            await self._async_ensure_started(name, force_retry=True)
        self._tool_cache = None
        self._resource_cache = None
        self._resource_template_cache = None

    async def _async_stop(self) -> None:
        for connection in reversed(list(self._connections.values())):
            try:
                await asyncio.wait_for(
                    connection.stop(),
                    timeout=max(0.001, connection.config.timeout_seconds),
                )
            except Exception:
                pass
        for config in self._configs:
            self._mark_server_stopped(config.name)

    async def _async_list_tools(self, *, force_retry: bool = False) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        routes: dict[str, tuple[str, str]] = {}

        for config in self._configs:
            connection = self._connections[config.name]
            if config.is_deferred():
                # Generic tool discovery must never start a heavyweight provider
                # or talk to one that is still warming. Its tools only become
                # visible once it is actually ready, so a cold/warming/failed
                # KiCad never delays or breaks ordinary chat.
                if not self._deferred_provider_ready(config.name):
                    continue
            elif not await self._async_ensure_started(config.name, force_retry=force_retry):
                continue

            try:
                result = await self._with_timeout(
                    connection.list_tools(),
                    timeout_seconds=config.timeout_seconds,
                    operation=f"list tools from MCP server '{config.name}'",
                )
                self._mark_server_healthy(config.name)
            except Exception as exc:
                await self._mark_server_failed(config.name, exc)
                continue

            for tool in getattr(result, "tools", []):
                raw_name = getattr(tool, "name")
                if not raw_name:
                    raise McpProtocolError(f"MCP server '{config.name}' returned a tool without a name.")
                if self._should_hide_tool(config, raw_name):
                    continue

                exposed_name = config.exposed_tool_name(raw_name)
                existing = routes.get(exposed_name)
                if existing is not None:
                    raise McpProtocolError(
                        "Duplicate MCP tool name detected for "
                        f"'{exposed_name}' from servers '{existing[0]}' and '{config.name}'. "
                        "Set a unique 'tool_prefix' on one of those servers."
                    )

                input_schema = getattr(tool, "inputSchema", None)
                if input_schema is None and hasattr(tool, "model_dump"):
                    dumped = tool.model_dump()
                    input_schema = dumped.get("inputSchema")

                description = getattr(tool, "description", "") or ""
                description = self._decorate_description(config.name, description)

                routes[exposed_name] = (config.name, raw_name)
                tools.append(
                    {
                        "name": exposed_name,
                        "description": description,
                        "inputSchema": input_schema or {"type": "object", "properties": {}},
                    }
                )

        self._tool_routes = routes
        return tools

    async def _async_call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        route = self._tool_routes.get(name)
        if route is None:
            # A deferred provider may own this tool by prefix even though its
            # tools are not yet exposed. Start it on demand (lazy activation)
            # before giving up so an explicit request can still use it.
            owner = self._deferred_owner_for_tool(name)
            if owner is not None and not self._connection_is_running(self._connections[owner]):
                await self._async_ensure_started(owner, force_retry=True)
            await self._async_list_tools()
            route = self._tool_routes.get(name)
        if route is None:
            raise KeyError(name)

        server_name, raw_name = route
        connection = self._connections[server_name]
        arguments = self._normalize_tool_arguments(name, server_name, arguments)
        result = await self._async_call_with_reconnect(connection, raw_name, arguments)

        is_error = bool(getattr(result, "isError", False))
        text = self._extract_text(result)
        if is_error:
            raise RuntimeError(text)
        return text

    async def _async_list_resources(self) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        routes: dict[str, list[str]] = {}

        for config in self._configs:
            connection = self._connections[config.name]
            if config.is_deferred():
                if not self._deferred_provider_ready(config.name):
                    continue
            elif not await self._async_ensure_started(config.name):
                continue
            try:
                result = await self._with_timeout(
                    connection.list_resources(),
                    timeout_seconds=config.timeout_seconds,
                    operation=f"list resources from MCP server '{config.name}'",
                )
                self._mark_server_healthy(config.name)
            except Exception as exc:
                await self._mark_server_failed(config.name, exc)
                continue

            for resource in getattr(result, "resources", []):
                resource_data = self._asdict(resource)
                uri = self._first_value(resource, resource_data, "uri")
                if not uri:
                    continue

                routes.setdefault(str(uri), []).append(config.name)
                resources.append(
                    {
                        "server": config.name,
                        "uri": str(uri),
                        "name": self._optional_text(resource, resource_data, "name"),
                        "description": self._optional_text(resource, resource_data, "description"),
                        "mimeType": self._optional_text(resource, resource_data, "mimeType"),
                        "size": self._optional_number(resource, resource_data, "size"),
                    }
                )

        self._resource_routes = routes
        return resources

    async def _async_list_resource_templates(self) -> list[dict[str, Any]]:
        templates: list[dict[str, Any]] = []

        for config in self._configs:
            connection = self._connections[config.name]
            if config.is_deferred():
                if not self._deferred_provider_ready(config.name):
                    continue
            elif not await self._async_ensure_started(config.name):
                continue
            try:
                result = await self._with_timeout(
                    connection.list_resource_templates(),
                    timeout_seconds=config.timeout_seconds,
                    operation=f"list resource templates from MCP server '{config.name}'",
                )
                self._mark_server_healthy(config.name)
            except Exception as exc:
                await self._mark_server_failed(config.name, exc)
                continue

            for template in getattr(result, "resourceTemplates", []):
                template_data = self._asdict(template)
                uri_template = self._first_value(template, template_data, "uriTemplate", "uri_template")
                if not uri_template:
                    continue

                templates.append(
                    {
                        "server": config.name,
                        "uriTemplate": str(uri_template),
                        "name": self._optional_text(template, template_data, "name"),
                        "description": self._optional_text(template, template_data, "description"),
                        "mimeType": self._optional_text(template, template_data, "mimeType"),
                    }
                )

        return templates

    async def _async_read_resource(self, uri: str, server: str | None = None) -> str:
        resolved_server = server.strip() if isinstance(server, str) else None
        if not resolved_server:
            if not self._resource_routes:
                await self._async_list_resources()
            routes = self._resource_routes.get(uri)
            if not routes:
                await self._async_list_resources()
                routes = self._resource_routes.get(uri)
            if not routes:
                raise KeyError(uri)

            unique_routes = list(dict.fromkeys(routes))
            if len(unique_routes) > 1:
                raise ValueError(
                    f"Multiple MCP servers expose resource '{uri}'. Specify the server name."
                )
            resolved_server = unique_routes[0]

        connection = self._connections.get(resolved_server)
        if connection is None:
            raise KeyError(resolved_server)

        if not await self._async_ensure_started(resolved_server):
            raise RuntimeError(f"MCP server '{resolved_server}' is unavailable.")

        try:
            result = await self._with_timeout(
                connection.read_resource(uri),
                timeout_seconds=connection.config.timeout_seconds,
                operation=f"read resource from MCP server '{resolved_server}'",
            )
            self._mark_server_healthy(resolved_server)
        except Exception as exc:
            await self._mark_server_failed(resolved_server, exc)
            raise
        return json.dumps(
            {
                "server": resolved_server,
                "uri": uri,
                "contents": self._extract_resource_contents(result),
            }
        )

    async def _async_ensure_started(self, server_name: str, *, force_retry: bool = False) -> bool:
        connection = self._connections[server_name]
        if self._connection_is_running(connection):
            self._mark_server_healthy(server_name)
            return True

        pending = self._pending_starts.get(server_name)
        if pending is not None:
            if pending.done():
                self._pending_starts.pop(server_name, None)
                try:
                    await pending
                    self._mark_server_healthy(server_name)
                    return True
                except asyncio.CancelledError as exc:
                    await self._mark_server_failed(
                        server_name,
                        self._startup_cancelled_error(server_name, exc),
                    )
                    return False
                except Exception as exc:
                    await self._mark_server_failed(server_name, exc)
                    return False
            current = self._status[server_name].status
            if current == "warming":
                # A background sidecar warmup is already running; leave the
                # warming phase (and its elapsed timer) intact.
                transient = "warming"
            elif self._status[server_name].failure_count:
                transient = "reconnecting"
            else:
                transient = "starting"
            self._mark_server_transition(
                server_name,
                transient,
                healthy=False,
                error="MCP server startup is already in progress; not starting a duplicate process.",
            )
            return False

        status = self._status[server_name]
        if status.failure_count and not force_retry:
            return False

        self._mark_server_transition(
            server_name,
            "reconnecting" if status.failure_count else "starting",
            healthy=False,
        )
        task = asyncio.create_task(connection.start())
        self._pending_starts[server_name] = task
        task.add_done_callback(
            lambda completed, name=server_name: self._on_start_task_done(name, completed)
        )
        try:
            done, _pending = await asyncio.wait(
                {task},
                timeout=max(0.001, connection.config.timeout_seconds),
            )
            if not done:
                await self._mark_server_failed(
                    server_name,
                    TimeoutError(
                        f"Timed out while trying to start MCP server '{server_name}' "
                        f"after {connection.config.timeout_seconds:.1f}s. "
                        "Startup is still quarantined in the background; use retry_mcp_server after it finishes or restart TALOS."
                    ),
                    cleanup=False,
                )
                return False
            self._pending_starts.pop(server_name, None)
            await task
            self._mark_server_healthy(server_name)
            return True
        except asyncio.CancelledError as exc:
            if task.done():
                await self._mark_server_failed(
                    server_name,
                    self._startup_cancelled_error(server_name, exc),
                )
                return False
            raise
        except Exception as exc:
            await self._mark_server_failed(server_name, exc)
            return False

    def _on_start_task_done(self, server_name: str, task: asyncio.Task) -> None:
        if self._pending_starts.get(server_name) is task:
            self._pending_starts.pop(server_name, None)
        try:
            task.result()
        except asyncio.CancelledError as exc:
            asyncio.create_task(
                self._mark_server_failed(
                    server_name,
                    self._startup_cancelled_error(server_name, exc),
                )
            )
            return
        except Exception as exc:
            asyncio.create_task(self._mark_server_failed(server_name, exc))
            return

        self._mark_server_healthy(server_name)
        self._tool_cache = None
        self._resource_cache = None
        self._resource_template_cache = None

    def _schedule_background_start(self, server_name: str) -> None:
        """Begin warming a provider in the background without blocking callers.

        Used for ``sidecar_autostart`` providers (and explicit non-blocking
        starts). The provider transitions to ``warming`` immediately and becomes
        ``healthy`` only once its connection is live, at which point its tools
        are refreshed into the catalog.
        """

        connection = self._connections[server_name]
        if self._connection_is_running(connection):
            self._mark_server_healthy(server_name)
            return

        pending = self._pending_starts.get(server_name)
        if pending is not None and not pending.done():
            return

        self._mark_server_transition(server_name, "warming", healthy=False)
        task = asyncio.create_task(self._background_warmup(server_name))
        self._pending_starts[server_name] = task

    async def _background_warmup(self, server_name: str) -> None:
        connection = self._connections[server_name]
        try:
            await connection.start()
        except asyncio.CancelledError as exc:
            await self._mark_server_failed(
                server_name,
                self._startup_cancelled_error(server_name, exc),
            )
            return
        except Exception as exc:
            await self._mark_server_failed(server_name, exc)
            return
        finally:
            if self._pending_starts.get(server_name) is asyncio.current_task():
                self._pending_starts.pop(server_name, None)

        self._mark_server_healthy(server_name)
        # The provider is now ready; drop cached catalogs so its tools and
        # resources are surfaced on the next discovery.
        self._tool_cache = None
        self._resource_cache = None
        self._resource_template_cache = None

    @staticmethod
    def _startup_cancelled_error(server_name: str, exc: BaseException) -> RuntimeError:
        detail = str(exc).strip()
        if detail:
            return RuntimeError(
                f"MCP server '{server_name}' startup was cancelled during initialization: {detail}"
            )
        return RuntimeError(
            f"MCP server '{server_name}' startup was cancelled during initialization."
        )

    def _deferred_owner_for_tool(self, exposed_name: str) -> str | None:
        """Resolve which deferred provider owns a (possibly unexposed) tool.

        Heavyweight providers keep their tools hidden until ready, so the route
        table will not contain them while cold. We fall back to matching the
        configured ``tool_prefix`` so an explicit tool call can still activate a
        lazy provider on demand.
        """

        best: tuple[int, str] | None = None
        for config in self._configs:
            if not config.is_deferred() or not config.tool_prefix:
                continue
            if exposed_name.startswith(config.tool_prefix):
                prefix_len = len(config.tool_prefix)
                if best is None or prefix_len > best[0]:
                    best = (prefix_len, config.name)
        return best[1] if best is not None else None

    async def _async_start_server(self, server_name: str, *, background: bool) -> None:
        if background:
            self._schedule_background_start(server_name)
            return
        await self._async_ensure_started(server_name, force_retry=True)

    async def _async_call_with_reconnect(
        self,
        connection: _ServerConnection,
        raw_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        server_name = connection.config.name
        attempts = self._reconnect_attempts + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            if not await self._async_ensure_started(server_name, force_retry=attempt > 0):
                last_exc = RuntimeError(f"MCP server '{server_name}' is unavailable.")
                break

            try:
                result = await self._with_timeout(
                    connection.call_tool(raw_name, arguments),
                    timeout_seconds=connection.config.timeout_seconds,
                    operation=f"call MCP tool '{raw_name}' on server '{server_name}'",
                )
                self._mark_server_healthy(server_name)
                return result
            except Exception as exc:
                last_exc = exc
                await self._mark_server_failed(server_name, exc)
                if attempt >= attempts - 1:
                    break
                if self._reconnect_backoff_seconds > 0:
                    await asyncio.sleep(self._reconnect_backoff_seconds)

        if last_exc is None:
            last_exc = RuntimeError(f"MCP server '{server_name}' is unavailable.")
        raise last_exc

    async def _with_timeout(self, awaitable: Any, *, timeout_seconds: float, operation: str) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=max(0.001, timeout_seconds))
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Timed out while trying to {operation} after {timeout_seconds:.1f}s.") from exc

    def _connection_is_running(self, connection: Any) -> bool:
        return bool(getattr(connection, "is_running", True))

    def _deferred_provider_ready(self, server_name: str) -> bool:
        """True only when a deferred provider has finished warming and is ready.

        ``connection.is_running`` flips to True while the MCP ``initialize``
        handshake is still in flight (a heavyweight backend can sit there for
        many seconds), so it must not be used to decide tool exposure. Gating on
        the marked-healthy status guarantees foreground tool discovery never
        blocks on, or surfaces tools from, a provider that is still warming.
        """

        connection = self._connections[server_name]
        return self._status[server_name].healthy and self._connection_is_running(connection)

    def _mark_server_transition(
        self,
        server_name: str,
        status: str,
        *,
        healthy: bool,
        error: str | None = None,
    ) -> None:
        item = self._status[server_name]
        item.status = status
        item.healthy = healthy
        item.last_error = error
        item.last_transition = time.time()
        if status in {"warming", "starting", "reconnecting"}:
            if item.warming_since is None:
                item.warming_since = time.time()
        else:
            item.warming_since = None

    def _mark_server_healthy(self, server_name: str) -> None:
        item = self._status[server_name]
        item.status = "healthy"
        item.healthy = True
        item.failure_count = 0
        item.last_error = None
        item.next_retry_at = None
        item.last_transition = time.time()
        item.warming_since = None

    def _mark_server_stopped(self, server_name: str) -> None:
        item = self._status[server_name]
        item.status = "stopped"
        item.healthy = False
        item.next_retry_at = None
        item.last_transition = time.time()
        item.warming_since = None

    async def _mark_server_failed(
        self,
        server_name: str,
        exc: Exception,
        *,
        cleanup: bool = True,
    ) -> None:
        connection = self._connections[server_name]
        if cleanup:
            try:
                await asyncio.wait_for(
                    connection.stop(),
                    timeout=max(0.001, connection.config.timeout_seconds),
                )
            except Exception:
                pass

        item = self._status[server_name]
        item.failure_count += 1
        item.healthy = False
        item.last_error = str(exc)
        item.status = "failed" if item.failure_count >= self._failure_threshold else "degraded"
        backoff = self._reconnect_backoff_seconds * max(1, item.failure_count)
        item.next_retry_at = time.time() + backoff if backoff > 0 else None
        item.last_transition = time.time()
        item.warming_since = None
        self._drop_server_routes(server_name)
        print(f"MCP server '{server_name}' marked {item.status}: {item.last_error}")

    def _drop_server_routes(self, server_name: str) -> None:
        self._tool_cache = None
        self._tool_routes = {
            exposed_name: route
            for exposed_name, route in self._tool_routes.items()
            if route[0] != server_name
        }
        self._resource_cache = None
        self._resource_template_cache = None
        self._resource_routes = {
            uri: [route for route in routes if route != server_name]
            for uri, routes in self._resource_routes.items()
        }
        self._resource_routes = {
            uri: routes for uri, routes in self._resource_routes.items() if routes
        }

    @staticmethod
    def _asdict(item: Any) -> dict[str, Any]:
        if hasattr(item, "model_dump"):
            dumped = item.model_dump()
            if isinstance(dumped, dict):
                return dumped
        if isinstance(item, dict):
            return item
        return {}

    @staticmethod
    def _first_value(item: Any, dumped: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = getattr(item, key, None)
            if value not in (None, ""):
                return value
            value = dumped.get(key)
            if value not in (None, ""):
                return value
        return None

    @classmethod
    def _optional_text(cls, item: Any, dumped: dict[str, Any], *keys: str) -> str | None:
        value = cls._first_value(item, dumped, *keys)
        if value in (None, ""):
            return None
        return str(value)

    @classmethod
    def _optional_number(cls, item: Any, dumped: dict[str, Any], *keys: str) -> int | float | None:
        value = cls._first_value(item, dumped, *keys)
        if isinstance(value, (int, float)):
            return value
        return None

    @classmethod
    def _extract_resource_contents(cls, result: Any) -> list[dict[str, Any]]:
        contents = getattr(result, "contents", None)
        if contents is None and isinstance(result, dict):
            contents = result.get("contents")
        if not isinstance(contents, list):
            return []

        extracted: list[dict[str, Any]] = []
        for item in contents:
            extracted.append(cls._resource_content_to_dict(item))
        return extracted

    @classmethod
    def _resource_content_to_dict(cls, item: Any) -> dict[str, Any]:
        dumped = cls._asdict(item)
        uri = cls._optional_text(item, dumped, "uri")
        mime_type = cls._optional_text(item, dumped, "mimeType", "mime_type")
        text = cls._optional_text(item, dumped, "text")
        if text is not None:
            max_chars = 20000
            return {
                "kind": "text",
                "uri": uri,
                "mimeType": mime_type,
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
            }

        blob = cls._first_value(item, dumped, "blob")
        if isinstance(blob, bytes):
            encoded = base64.b64encode(blob).decode("ascii")
            size_bytes = len(blob)
        elif isinstance(blob, str):
            encoded = blob
            try:
                size_bytes = len(base64.b64decode(blob, validate=False))
            except Exception:
                size_bytes = len(blob)
        else:
            encoded = ""
            size_bytes = 0

        max_blob_chars = 8192
        payload: dict[str, Any] = {
            "kind": "blob",
            "uri": uri,
            "mimeType": mime_type,
            "sizeBytes": size_bytes,
            "truncated": len(encoded) > max_blob_chars,
        }
        if encoded:
            payload["blobBase64"] = encoded[:max_blob_chars]
        return payload

    def _decorate_description(self, server_name: str, description: str) -> str:
        if len(self._configs) == 1:
            return description.strip()
        base_description = description.strip()
        if base_description:
            return f"[Server: {server_name}] {base_description}"
        return f"[Server: {server_name}]"

    @classmethod
    def _decorate_openai_tool_schema(
        cls, name: str, description: str, parameters: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        description_text = str(description or "").strip()
        schema = json.loads(json.dumps(parameters or {"type": "object", "properties": {}}))

        if not name.startswith("kicad_"):
            return description_text, schema

        notes: list[str] = []
        properties = schema.get("properties")
        if isinstance(properties, dict):
            path_note_added = False
            for field_name in cls._PATH_ARGUMENT_FIELDS:
                field = properties.get(field_name)
                if not isinstance(field, dict):
                    continue
                base = str(field.get("description") or "").strip()
                suffix = "Use an absolute filesystem path."
                if suffix not in base:
                    field["description"] = f"{base} {suffix}".strip()
                path_note_added = True
            if path_note_added:
                notes.append(
                    "Use absolute filesystem paths for filename/path/schematicPath/boardPath/projectPath fields."
                )

        if name == "kicad_add_schematic_component":
            notes.append(
                "Use canonical KiCad library symbols; for power rails prefer power:+5V and power:GND instead of inventing Device:V."
            )

        if notes:
            if description_text:
                description_text = f"{description_text} {' '.join(notes)}"
            else:
                description_text = " ".join(notes)
        return description_text, schema

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    @staticmethod
    def _filesystem_server_name() -> str:
        return (
            os.getenv("TALOS_FILESYSTEM_SERVER_NAME", DEFAULT_FILESYSTEM_SERVER_NAME).strip()
            or DEFAULT_FILESYSTEM_SERVER_NAME
        )

    @staticmethod
    def _minecraft_filesystem_server_name() -> str:
        return (
            os.getenv("MINECRAFT_MCP_FILESYSTEM_SERVER_NAME", DEFAULT_MINECRAFT_FILESYSTEM_SERVER_NAME)
            .strip()
            or DEFAULT_MINECRAFT_FILESYSTEM_SERVER_NAME
        )

    @classmethod
    def _should_hide_tool(cls, config: McpServerConfig, raw_name: str) -> bool:
        if (
            _is_general_filesystem_config(config)
            and not _filesystem_writes_allowed()
            and raw_name in FILESYSTEM_MUTATING_TOOLS
        ):
            return True
        if (
            _is_minecraft_filesystem_config(config)
            and not _minecraft_fs_writes_allowed()
            and raw_name in MINECRAFT_FILESYSTEM_MUTATING_TOOLS
        ):
            return True
        return False

    def _run_coro(self, coro):
        if self._loop is None:
            raise RuntimeError("Local MCP loop is not running.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=max(0.001, self._bridge_timeout_seconds))
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                "Timed out waiting for the local MCP event-loop bridge "
                f"after {self._bridge_timeout_seconds:.1f}s."
            ) from exc

    def _shutdown_loop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=2)
        if self._loop is not None:
            self._loop.close()
        self._loop = None
        self._loop_thread = None

    @staticmethod
    def _parse_arguments(arguments: str | dict[str, Any] | None) -> dict[str, Any]:
        return parse_tool_arguments(arguments)

    @staticmethod
    def _extract_text(result: Any) -> str:
        content = getattr(result, "content", [])
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                item_type = getattr(item, "type", None)
                text = getattr(item, "text", None)
                if item_type == "text" and text:
                    chunks.append(str(text))
            if chunks:
                return "\n".join(chunks)

        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return json.dumps(structured)
        return ""

    _PATH_ARGUMENT_FIELDS = {
        "path",
        "filename",
        "schematicPath",
        "boardPath",
        "projectPath",
        "template",
    }
    _MISSING_ROOT_PATH_RE = re.compile(r"^(Users|private|Applications|Volumes)/")

    @classmethod
    def _normalize_tool_arguments(
        cls, name: str, server_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        if not arguments:
            return arguments
        if "kicad" not in server_name.lower() and not name.startswith("kicad_"):
            return arguments
        return cls._normalize_kicad_argument_value(arguments)

    @classmethod
    def _normalize_kicad_argument_value(cls, value: Any, *, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {
                dict_key: cls._normalize_kicad_argument_value(dict_value, key=str(dict_key))
                for dict_key, dict_value in value.items()
            }
        if isinstance(value, list):
            return [cls._normalize_kicad_argument_value(item) for item in value]
        if isinstance(value, str) and key in cls._PATH_ARGUMENT_FIELDS:
            return cls._normalize_path_string(value)
        return value

    @classmethod
    def _normalize_path_string(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return value
        if normalized.startswith("~"):
            return str(Path(normalized).expanduser())
        if cls._MISSING_ROOT_PATH_RE.match(normalized):
            return f"/{normalized}"
        return normalized


def _names_from_env(variable: str) -> set[str]:
    """Parse a disable-list env var into a set of lowercase names.

    Accepts either a JSON array (``["kicad","filesystem"]``) or a plain
    comma-separated list (``kicad, filesystem``) so the value is easy to set by
    hand as well as to write from the launcher.
    """

    raw = os.getenv(variable, "").strip()
    if not raw:
        return set()

    values: list[str] = []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            values = [str(item) for item in parsed]
    if not values:
        values = raw.split(",")
    return {value.strip().lower() for value in values if value.strip()}


def _default_local_server_config() -> McpServerConfig:
    return McpServerConfig(
        name="talos-local",
        transport="stdio",
        command=sys.executable,
        args=["-m", "talos.mcp_server"],
    )


def _apply_disabled_providers(configs: list[McpServerConfig]) -> None:
    """Switch off provider groups inside every local aggregate server config.

    The groups are disabled on the server's command line rather than through the
    environment: the stdio client only forwards the parent environment when a
    server declares its own ``env``, and widening that for the aggregate server
    would change what all of its providers see. Applied here (not where the
    default config is built) so it also covers a ``talos-local`` entry written
    out explicitly in ``TALOS_MCP_SERVERS``.
    """

    disabled = sorted(_names_from_env("TALOS_MCP_DISABLED_PROVIDERS"))
    if not disabled:
        return
    for config in configs:
        if "talos.mcp_server" in [str(arg) for arg in config.args]:
            config.args = [*config.args, "--disable-provider", ",".join(disabled)]


def _filesystem_writes_allowed() -> bool:
    raw = os.getenv("TALOS_FILESYSTEM_ALLOW_WRITES", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _normalized_kind(config: McpServerConfig) -> str:
    return str(config.kind or "").strip().lower()


def _is_general_filesystem_config(config: McpServerConfig) -> bool:
    kind = _normalized_kind(config)
    if kind:
        return kind == FILESYSTEM_CONFIG_KIND
    return config.name == LocalMcpClient._filesystem_server_name()


def _is_minecraft_filesystem_config(config: McpServerConfig) -> bool:
    kind = _normalized_kind(config)
    if kind:
        return kind == MINECRAFT_FILESYSTEM_CONFIG_KIND
    return config.name == LocalMcpClient._minecraft_filesystem_server_name()


def _infer_config_kind(name: str, args: list[str], tool_prefix: str, raw_kind: str | None = None) -> str:
    normalized_kind = str(raw_kind or "").strip().lower()
    if normalized_kind:
        return normalized_kind

    if any("@modelcontextprotocol/server-filesystem" in str(arg) for arg in args):
        if tool_prefix.startswith(DEFAULT_MINECRAFT_FILESYSTEM_TOOL_PREFIX) or "minecraft" in name.lower():
            return MINECRAFT_FILESYSTEM_CONFIG_KIND
        return FILESYSTEM_CONFIG_KIND
    return ""


def _optional_filesystem_server_config() -> McpServerConfig | None:
    roots = _resolve_allowed_root_paths(os.getenv("TALOS_FILESYSTEM_ROOTS", ""))
    if not roots:
        return None

    command = os.getenv("TALOS_FILESYSTEM_COMMAND", "npx").strip() or "npx"
    package = (
        os.getenv(
            "TALOS_FILESYSTEM_PACKAGE",
            "@modelcontextprotocol/server-filesystem",
        ).strip()
        or "@modelcontextprotocol/server-filesystem"
    )
    args = [package, *[str(root) for root in roots]]
    if command == "npx":
        args.insert(0, "-y")

    return McpServerConfig(
        name=os.getenv("TALOS_FILESYSTEM_SERVER_NAME", DEFAULT_FILESYSTEM_SERVER_NAME).strip()
        or DEFAULT_FILESYSTEM_SERVER_NAME,
        transport="stdio",
        command=command,
        args=args,
        tool_prefix=os.getenv("TALOS_FILESYSTEM_TOOL_PREFIX", DEFAULT_FILESYSTEM_TOOL_PREFIX).strip()
        or DEFAULT_FILESYSTEM_TOOL_PREFIX,
        timeout_seconds=float(os.getenv("TALOS_FILESYSTEM_TIMEOUT", "120")),
        mode=os.getenv("TALOS_FILESYSTEM_MODE", "").strip(),
        kind=FILESYSTEM_CONFIG_KIND,
    )


def _optional_filesystem_diagnostics_server_config() -> McpServerConfig | None:
    roots = _resolve_allowed_root_paths(os.getenv("TALOS_FILESYSTEM_ROOTS", ""))
    if not roots:
        return None

    env = {
        "TALOS_FILESYSTEM_ROOTS": json.dumps([str(root) for root in roots]),
    }
    for key in (
        "TALOS_FILESYSTEM_RG_TIMEOUT",
        "TALOS_FILESYSTEM_MAX_TEXT_BYTES",
        "TALOS_FILESYSTEM_DEFAULT_EXCLUDES",
    ):
        value = os.getenv(key, "").strip()
        if value:
            env[key] = value

    return McpServerConfig(
        name=(
            os.getenv(
                "TALOS_FILESYSTEM_DIAGNOSTICS_SERVER_NAME",
                DEFAULT_FILESYSTEM_DIAGNOSTICS_SERVER_NAME,
            ).strip()
            or DEFAULT_FILESYSTEM_DIAGNOSTICS_SERVER_NAME
        ),
        transport="stdio",
        command=sys.executable,
        args=["-m", "talos.mcp_filesystem_diagnostics_server"],
        env=env,
        tool_prefix=os.getenv("TALOS_FILESYSTEM_TOOL_PREFIX", DEFAULT_FILESYSTEM_TOOL_PREFIX).strip()
        or DEFAULT_FILESYSTEM_TOOL_PREFIX,
        timeout_seconds=float(os.getenv("TALOS_FILESYSTEM_DIAGNOSTICS_TIMEOUT", "120")),
        mode=os.getenv("TALOS_FILESYSTEM_MODE", "").strip(),
        kind=FILESYSTEM_DIAGNOSTICS_CONFIG_KIND,
    )


def _minecraft_fs_writes_allowed() -> bool:
    raw = os.getenv("MINECRAFT_MCP_ALLOW_WRITES", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_minecraft_server_root() -> Path | None:
    raw_root = os.getenv("MINECRAFT_SERVER_DIR", "").strip()
    if not raw_root:
        return None

    repo_root = Path(__file__).resolve().parents[2]
    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        root = (repo_root / root).resolve()
    return root


def _optional_minecraft_server_configs() -> list[McpServerConfig]:
    root = _resolve_minecraft_server_root()
    if root is None:
        return []

    mode = os.getenv("MINECRAFT_MCP_MODE", "").strip()
    filesystem_timeout = float(os.getenv("MINECRAFT_MCP_FILESYSTEM_TIMEOUT", "120"))
    search_timeout = float(os.getenv("MINECRAFT_MCP_SEARCH_TIMEOUT", "30"))

    filesystem_command = os.getenv("MINECRAFT_MCP_FILESYSTEM_COMMAND", "npx").strip() or "npx"
    filesystem_package = (
        os.getenv(
            "MINECRAFT_MCP_FILESYSTEM_PACKAGE",
            "@modelcontextprotocol/server-filesystem",
        ).strip()
        or "@modelcontextprotocol/server-filesystem"
    )
    filesystem_args = [filesystem_package, str(root)]
    if filesystem_command == "npx":
        filesystem_args.insert(0, "-y")

    return [
        McpServerConfig(
            name=(
                os.getenv(
                    "MINECRAFT_MCP_FILESYSTEM_SERVER_NAME",
                    DEFAULT_MINECRAFT_FILESYSTEM_SERVER_NAME,
                ).strip()
                or DEFAULT_MINECRAFT_FILESYSTEM_SERVER_NAME
            ),
            transport="stdio",
            command=filesystem_command,
            args=filesystem_args,
            tool_prefix=(
                os.getenv(
                    "MINECRAFT_MCP_FILESYSTEM_TOOL_PREFIX",
                    DEFAULT_MINECRAFT_FILESYSTEM_TOOL_PREFIX,
                ).strip()
                or DEFAULT_MINECRAFT_FILESYSTEM_TOOL_PREFIX
            ),
            timeout_seconds=filesystem_timeout,
            mode=mode,
            kind=MINECRAFT_FILESYSTEM_CONFIG_KIND,
        ),
        McpServerConfig(
            name=(
                os.getenv(
                    "MINECRAFT_MCP_SEARCH_SERVER_NAME",
                    DEFAULT_MINECRAFT_SEARCH_SERVER_NAME,
                ).strip()
                or DEFAULT_MINECRAFT_SEARCH_SERVER_NAME
            ),
            transport="stdio",
            command=os.getenv("MINECRAFT_MCP_SEARCH_COMMAND", "").strip() or sys.executable,
            args=["-m", "talos.mcp_minecraft_diagnostics_server"],
            env={"MINECRAFT_SERVER_DIR": str(root)},
            tool_prefix=(
                os.getenv(
                    "MINECRAFT_MCP_SEARCH_TOOL_PREFIX",
                    DEFAULT_MINECRAFT_SEARCH_TOOL_PREFIX,
                ).strip()
                or DEFAULT_MINECRAFT_SEARCH_TOOL_PREFIX
            ),
            timeout_seconds=search_timeout,
            mode=mode,
            kind=MINECRAFT_DIAGNOSTICS_CONFIG_KIND,
        ),
    ]


def _resolve_kicad_mode() -> str:
    """Determine KiCad's lifecycle mode from env.

    KiCad is heavyweight, so it defaults to ``sidecar_autostart`` (background
    warmup) rather than the blocking ``stdio`` path. ``KICAD_MCP_MODE`` selects
    the mode directly; the convenience knob ``KICAD_MCP_AUTOSTART`` can override
    it (``background`` -> autostart, ``lazy`` -> lazy, ``false`` -> lazy).
    """

    mode = os.getenv("KICAD_MCP_MODE", "").strip().lower()
    if not mode:
        mode = LIFECYCLE_SIDECAR_AUTOSTART

    autostart = os.getenv("KICAD_MCP_AUTOSTART", "").strip().lower()
    if autostart in {"background", "warm", "true", "1", "on"}:
        mode = LIFECYCLE_SIDECAR_AUTOSTART
    elif autostart in {"lazy", "on_demand", "ondemand"}:
        mode = LIFECYCLE_LAZY
    elif autostart in {"false", "0", "off", "no"} and mode == LIFECYCLE_SIDECAR_AUTOSTART:
        # Explicitly disable background warmup but keep the provider available
        # on demand instead of forcing the blocking stdio path.
        mode = LIFECYCLE_LAZY
    return mode


def _optional_kicad_server_config() -> McpServerConfig | None:
    mode = _resolve_kicad_mode()
    url = os.getenv("KICAD_MCP_URL", "").strip()

    # A manually managed sidecar connects to an already-running HTTP endpoint and
    # does not need a local server path or command.
    if McpServerConfig(name="kicad", transport="stdio", mode=mode).lifecycle_mode() == LIFECYCLE_SIDECAR_MANUAL and url:
        return McpServerConfig(
            name=os.getenv("KICAD_MCP_SERVER_NAME", "kicad").strip() or "kicad",
            transport="streamable_http",
            url=url,
            tool_prefix=os.getenv("KICAD_MCP_TOOL_PREFIX", "kicad_").strip(),
            timeout_seconds=float(os.getenv("KICAD_MCP_TIMEOUT", "600")),
            mode=mode,
        )

    raw_path = os.getenv("KICAD_MCP_SERVER_PATH", "").strip()
    if not raw_path:
        return None

    repo_root = Path(__file__).resolve().parents[2]
    server_path = Path(raw_path).expanduser()
    if not server_path.is_absolute():
        server_path = (repo_root / server_path).resolve()

    if server_path.is_dir():
        entrypoint = server_path / "dist" / "index.js"
        cwd = server_path
    else:
        entrypoint = server_path
        cwd = (
            server_path.parent.parent
            if server_path.parent.name == "dist" and server_path.parent.parent.exists()
            else server_path.parent
        )

    env: dict[str, str] = {
        "NODE_ENV": os.getenv("KICAD_MCP_NODE_ENV", "production").strip() or "production",
        "LOG_LEVEL": os.getenv("KICAD_MCP_LOG_LEVEL", "info").strip() or "info",
        "KICAD_AUTO_LAUNCH": os.getenv("KICAD_AUTO_LAUNCH", "false").strip() or "false",
    }
    for source_key, target_key in (
        ("KICAD_PYTHONPATH", "PYTHONPATH"),
        ("KICAD_PYTHON", "KICAD_PYTHON"),
        ("KICAD_BACKEND", "KICAD_BACKEND"),
        ("KICAD_READY_TIMEOUT_MS", "KICAD_READY_TIMEOUT_MS"),
        ("KICAD_WARMUP_TIMEOUT_MS", "KICAD_WARMUP_TIMEOUT_MS"),
        ("KICAD_MCP_DEV", "KICAD_MCP_DEV"),
    ):
        value = os.getenv(source_key, "").strip()
        if value:
            env[target_key] = value

    return McpServerConfig(
        name=os.getenv("KICAD_MCP_SERVER_NAME", "kicad").strip() or "kicad",
        transport="stdio",
        command=os.getenv("KICAD_MCP_COMMAND", "node").strip() or "node",
        args=[str(entrypoint)],
        cwd=str(cwd),
        env=env,
        tool_prefix=os.getenv("KICAD_MCP_TOOL_PREFIX", "kicad_").strip(),
        timeout_seconds=float(os.getenv("KICAD_MCP_TIMEOUT", "600")),
        mode=mode,
    )


def _load_mcp_server_configs() -> list[McpServerConfig]:
    """Configured MCP servers, minus any named in ``TALOS_MCP_DISABLED_SERVERS``.

    The disable list is a per-run switch (the launcher sets it from its
    checkboxes); the underlying configuration in ``settings.env`` is untouched, so
    re-enabling a server is just a matter of dropping it from the list.
    """

    configs = _collect_mcp_server_configs()
    disabled = _names_from_env("TALOS_MCP_DISABLED_SERVERS")
    if disabled:
        configs = [config for config in configs if config.name.strip().lower() not in disabled]
    _apply_disabled_providers(configs)
    return configs


def _collect_mcp_server_configs() -> list[McpServerConfig]:
    raw_config = os.getenv("TALOS_MCP_SERVERS", "").strip()
    if not raw_config:
        configs = [_default_local_server_config()]
        filesystem_config = _optional_filesystem_server_config()
        if filesystem_config is not None:
            configs.append(filesystem_config)
        filesystem_diagnostics_config = _optional_filesystem_diagnostics_server_config()
        if filesystem_diagnostics_config is not None:
            configs.append(filesystem_diagnostics_config)
        kicad_config = _optional_kicad_server_config()
        if kicad_config is not None:
            configs.append(kicad_config)
        configs.extend(_optional_minecraft_server_configs())
        return configs

    try:
        parsed = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "TALOS_MCP_SERVERS must be valid JSON. "
            "Expected a JSON array of MCP server config objects."
        ) from exc

    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError("TALOS_MCP_SERVERS must decode to a JSON array or object.")

    configs: list[McpServerConfig] = []
    seen_names: set[str] = set()
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"TALOS_MCP_SERVERS entry {index} must be an object.")

        name = str(item.get("name") or "").strip()
        transport = str(item.get("transport") or "").strip()
        if not name:
            raise ValueError(f"TALOS_MCP_SERVERS entry {index} is missing 'name'.")
        if not transport:
            raise ValueError(f"TALOS_MCP_SERVERS entry {index} is missing 'transport'.")
        if name in seen_names:
            raise ValueError(f"TALOS_MCP_SERVERS contains duplicate server name '{name}'.")
        seen_names.add(name)

        args = item.get("args", [])
        if args is None:
            args = []
        if not isinstance(args, list):
            raise ValueError(f"TALOS_MCP_SERVERS entry '{name}' has non-list 'args'.")

        headers = item.get("headers", {})
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            raise ValueError(f"TALOS_MCP_SERVERS entry '{name}' has non-object 'headers'.")

        env = item.get("env", {})
        if env is None:
            env = {}
        if not isinstance(env, dict):
            raise ValueError(f"TALOS_MCP_SERVERS entry '{name}' has non-object 'env'.")

        timeout_seconds = item.get("timeout_seconds", 30.0)
        tls_verify = _optional_bool(item.get("tls_verify"))
        if tls_verify is None:
            tls_verify = True
        configs.append(
            McpServerConfig(
                name=name,
                transport=transport,
                command=_optional_str(item.get("command")),
                args=[str(arg) for arg in args],
                url=_optional_str(item.get("url")),
                headers={str(key): str(value) for key, value in headers.items()},
                auth_token=_optional_str(item.get("auth_token")),
                auth_token_env=_optional_str(item.get("auth_token_env")),
                tls_verify=tls_verify,
                tls_ca_bundle=_optional_str(item.get("tls_ca_bundle")),
                tool_prefix=str(item.get("tool_prefix") or ""),
                cwd=_optional_str(item.get("cwd")),
                env={str(key): str(value) for key, value in env.items()},
                timeout_seconds=float(timeout_seconds),
                mode=str(item.get("mode") or ""),
                kind=_infer_config_kind(
                    name=name,
                    args=[str(arg) for arg in args],
                    tool_prefix=str(item.get("tool_prefix") or ""),
                    raw_kind=_optional_str(item.get("kind")),
                ),
            )
        )

    filesystem_config = _optional_filesystem_server_config()
    if filesystem_config is not None and filesystem_config.name not in seen_names:
        configs.append(filesystem_config)
        seen_names.add(filesystem_config.name)

    filesystem_diagnostics_config = _optional_filesystem_diagnostics_server_config()
    if (
        filesystem_diagnostics_config is not None
        and filesystem_diagnostics_config.name not in seen_names
    ):
        configs.append(filesystem_diagnostics_config)
        seen_names.add(filesystem_diagnostics_config.name)

    kicad_config = _optional_kicad_server_config()
    if kicad_config is not None and kicad_config.name not in seen_names:
        configs.append(kicad_config)
        seen_names.add(kicad_config.name)

    for config in _optional_minecraft_server_configs():
        if config.name in seen_names:
            continue
        configs.append(config)
        seen_names.add(config.name)

    return configs


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Expected boolean-like value, got {value!r}.")


_shared_client: LocalMcpClient | None = None
_shared_lock = threading.Lock()


def get_local_mcp_client() -> LocalMcpClient:
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = LocalMcpClient(_load_mcp_server_configs())
        return _shared_client


def shutdown_local_mcp_client() -> None:
    global _shared_client
    with _shared_lock:
        if _shared_client is not None:
            _shared_client.stop()
            _shared_client = None
