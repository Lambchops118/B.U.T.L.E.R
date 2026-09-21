from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.mcp_client import client as local_mcp_client


class Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeConnection:
    def __init__(
        self,
        *,
        name: str = "fake",
        timeout_seconds: float = 30.0,
        tools=None,
        resources=None,
        templates=None,
        read_results=None,
        start_error: Exception | None = None,
        start_delay: float = 0.0,
        list_tools_error: Exception | None = None,
        call_results=None,
        call_delay: float = 0.0,
    ):
        self.config = local_mcp_client.McpServerConfig(
            name=name,
            transport="stdio",
            command="node",
            timeout_seconds=timeout_seconds,
        )
        self.is_running = True
        self.start_count = 0
        self.stop_count = 0
        self._tools = Obj(tools=tools or [])
        self._resources = Obj(resources=resources or [])
        self._templates = Obj(resourceTemplates=templates or [])
        self._read_results = read_results or {}
        self._start_error = start_error
        self._start_delay = start_delay
        self._list_tools_error = list_tools_error
        self._call_results = list(call_results or [])
        self._call_delay = call_delay
        self.start_cancelled = False
        self.last_tool_call = None

    async def start(self):
        self.start_count += 1
        try:
            if self._start_delay:
                await asyncio.sleep(self._start_delay)
            if self._start_error is not None:
                self.is_running = False
                raise self._start_error
            self.is_running = True
        except asyncio.CancelledError:
            self.start_cancelled = True
            raise

    async def stop(self):
        self.stop_count += 1
        self.is_running = False

    async def list_resources(self):
        return self._resources

    async def list_resource_templates(self):
        return self._templates

    async def read_resource(self, uri: str):
        return self._read_results[uri]

    async def list_tools(self):
        if self._list_tools_error is not None:
            raise self._list_tools_error
        return self._tools

    async def call_tool(self, name: str, arguments):
        if self._call_delay:
            await asyncio.sleep(self._call_delay)
        self.last_tool_call = (name, arguments)
        if self._call_results:
            result = self._call_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return Obj(content=[Obj(type="text", text="ok")], isError=False)


class TestableLocalMcpClient(local_mcp_client.LocalMcpClient):
    def start(self) -> None:
        return None

    def _run_coro(self, coro):
        return asyncio.run(coro)


class LocalMcpClientResourceTests(unittest.TestCase):
    def test_start_keeps_healthy_servers_when_one_fails(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(name="healthy", transport="stdio", command="node"),
            local_mcp_client.McpServerConfig(name="broken", transport="stdio", command="node"),
        ]
        healthy = FakeConnection(name="healthy")
        broken = FakeConnection(
            name="broken",
            start_error=RuntimeError("cannot connect"),
        )
        healthy.is_running = False
        broken.is_running = False

        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        client._connections = {"healthy": healthy, "broken": broken}

        asyncio.run(client._async_start(force_retry=True))

        statuses = {item["name"]: item for item in client.list_server_status()}
        self.assertTrue(healthy.is_running)
        self.assertEqual(statuses["healthy"]["status"], "healthy")
        self.assertEqual(statuses["broken"]["status"], "degraded")
        self.assertIn("cannot connect", statuses["broken"]["last_error"])

    def test_list_tools_skips_failed_server_and_preserves_healthy_tools(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(name="healthy", transport="stdio", command="node"),
            local_mcp_client.McpServerConfig(name="broken", transport="stdio", command="node"),
        ]
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        client._connections = {
            "healthy": FakeConnection(
                name="healthy",
                tools=[Obj(name="ping", description="Ping", inputSchema={"type": "object"})],
            ),
            "broken": FakeConnection(
                name="broken",
                list_tools_error=RuntimeError("tools/list failed"),
            ),
        }

        tools = client.list_tools()

        self.assertEqual([tool["name"] for tool in tools], ["ping"])
        statuses = {item["name"]: item for item in client.list_server_status()}
        self.assertEqual(statuses["healthy"]["status"], "healthy")
        self.assertEqual(statuses["broken"]["status"], "degraded")
        self.assertIn("tools/list failed", statuses["broken"]["last_error"])

    def test_duplicate_tool_names_still_raise_clear_protocol_error(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(name="first", transport="stdio", command="node"),
            local_mcp_client.McpServerConfig(name="second", transport="stdio", command="node"),
        ]
        client = TestableLocalMcpClient(configs)
        client._connections = {
            "first": FakeConnection(name="first", tools=[Obj(name="same", description="")]),
            "second": FakeConnection(name="second", tools=[Obj(name="same", description="")]),
        }

        with self.assertRaisesRegex(local_mcp_client.McpProtocolError, "Duplicate MCP tool name"):
            client.list_tools()

    def test_hung_tool_call_fails_with_configured_timeout(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(
                name="slow",
                transport="stdio",
                command="node",
                timeout_seconds=0.01,
            )
        ]
        client = TestableLocalMcpClient(configs, reconnect_attempts=0, reconnect_backoff_seconds=0)
        connection = FakeConnection(name="slow", timeout_seconds=0.01, call_delay=0.05)
        client._connections = {"slow": connection}
        client._tool_routes = {"slow_tool": ("slow", "slow_tool")}

        with self.assertRaisesRegex(TimeoutError, "Timed out"):
            client.call_tool("slow_tool", {})

        status = client.list_server_status()[0]
        self.assertEqual(status["status"], "degraded")

    def test_tool_call_reconnects_once_after_session_failure(self) -> None:
        configs = [local_mcp_client.McpServerConfig(name="flaky", transport="stdio", command="node")]
        recovered_result = Obj(content=[Obj(type="text", text="recovered")], isError=False)
        connection = FakeConnection(
            name="flaky",
            call_results=[RuntimeError("session died"), recovered_result],
        )
        client = TestableLocalMcpClient(configs, reconnect_attempts=1, reconnect_backoff_seconds=0)
        client._connections = {"flaky": connection}
        client._tool_routes = {"flaky_tool": ("flaky", "flaky_tool")}

        result = client.call_tool("flaky_tool", {})

        self.assertEqual(result, "recovered")
        self.assertGreaterEqual(connection.start_count, 1)
        status = client.list_server_status()[0]
        self.assertEqual(status["status"], "healthy")

    def test_start_timeout_quarantines_without_cancelling_start_task(self) -> None:
        async def run_case() -> tuple[bool, list[dict[str, object]], list[dict[str, object]]]:
            configs = [
                local_mcp_client.McpServerConfig(
                    name="slow-start",
                    transport="stdio",
                    command="node",
                    timeout_seconds=0.01,
                )
            ]
            client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
            connection = FakeConnection(
                name="slow-start",
                timeout_seconds=0.01,
                start_delay=0.05,
            )
            connection.is_running = False
            client._connections = {"slow-start": connection}

            started = await client._async_ensure_started("slow-start")
            self.assertFalse(started)
            timed_out_status = client.list_server_status()
            await asyncio.sleep(0.06)
            completed_status = client.list_server_status()
            return connection.start_cancelled, timed_out_status, completed_status

        start_cancelled, timed_out_status, completed_status = asyncio.run(run_case())

        self.assertFalse(start_cancelled)
        self.assertEqual(timed_out_status[0]["status"], "degraded")
        self.assertIn("quarantined", str(timed_out_status[0]["last_error"]))
        self.assertEqual(completed_status[0]["status"], "healthy")

    def test_start_cancellation_marks_server_degraded_instead_of_raising(self) -> None:
        async def run_case() -> list[dict[str, object]]:
            configs = [
                local_mcp_client.McpServerConfig(
                    name="cancelled-start",
                    transport="stdio",
                    command="node",
                )
            ]
            client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
            connection = FakeConnection(
                name="cancelled-start",
                start_error=asyncio.CancelledError(),
            )
            connection.is_running = False
            client._connections = {"cancelled-start": connection}

            started = await client._async_ensure_started("cancelled-start")
            self.assertFalse(started)
            await asyncio.sleep(0)
            return client.list_server_status()

        status = asyncio.run(run_case())

        self.assertEqual(status[0]["status"], "degraded")
        self.assertIn("startup was cancelled", str(status[0]["last_error"]))

    def test_status_and_tool_refresh_do_not_retry_degraded_server(self) -> None:
        configs = [local_mcp_client.McpServerConfig(name="broken", transport="stdio", command="node")]
        connection = FakeConnection(name="broken", start_error=RuntimeError("still down"))
        connection.is_running = False
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        client._connections = {"broken": connection}

        asyncio.run(client._async_start(force_retry=True))
        self.assertEqual(connection.start_count, 1)

        client.list_server_status(refresh=True)
        client.list_tools(refresh=True)

        self.assertEqual(connection.start_count, 1)

    def test_retry_server_is_the_explicit_retry_path(self) -> None:
        configs = [local_mcp_client.McpServerConfig(name="broken", transport="stdio", command="node")]
        connection = FakeConnection(name="broken", start_error=RuntimeError("still down"))
        connection.is_running = False
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        client._connections = {"broken": connection}

        asyncio.run(client._async_start(force_retry=True))
        self.assertEqual(connection.start_count, 1)

        client.retry_server("broken")

        self.assertEqual(connection.start_count, 2)

    def test_list_tool_inventory_includes_tools_and_server_health(self) -> None:
        configs = [local_mcp_client.McpServerConfig(name="healthy", transport="stdio", command="node")]
        client = TestableLocalMcpClient(configs)
        client._connections = {
            "healthy": FakeConnection(
                name="healthy",
                tools=[
                    Obj(
                        name="ping",
                        description="Ping",
                        inputSchema={
                            "type": "object",
                            "properties": {"target": {"type": "string"}},
                        },
                    )
                ],
            )
        }

        inventory = client.list_tool_inventory(refresh=True)

        self.assertEqual(inventory["tools"][0]["name"], "ping")
        self.assertEqual(inventory["tools"][0]["server"], "healthy")
        self.assertEqual(inventory["tools"][0]["rawName"], "ping")
        self.assertEqual(inventory["tools"][0]["parameters"], ["target"])
        self.assertNotIn("inputSchema", inventory["tools"][0])
        self.assertEqual(inventory["tool_count"], 1)
        self.assertEqual(inventory["servers"][0]["status"], "healthy")

    def test_list_resources_and_templates_include_server_metadata(self) -> None:
        configs = [local_mcp_client.McpServerConfig(name="kicad", transport="stdio", command="node")]
        client = TestableLocalMcpClient(configs)
        client._connections = {
            "kicad": FakeConnection(
                resources=[
                    Obj(
                        uri="kicad://project/current/info",
                        name="Project Info",
                        description="Current KiCad project metadata",
                        mimeType="application/json",
                        size=128,
                    )
                ],
                templates=[
                    Obj(
                        uriTemplate="kicad://project/{project_id}/snapshot",
                        name="Project Snapshot",
                        description="Read a named snapshot",
                        mimeType="application/json",
                    )
                ],
            )
        }

        resources = client.list_resources()
        templates = client.list_resource_templates()

        self.assertEqual(resources[0]["server"], "kicad")
        self.assertEqual(resources[0]["uri"], "kicad://project/current/info")
        self.assertEqual(resources[0]["mimeType"], "application/json")
        self.assertEqual(resources[0]["size"], 128)
        self.assertEqual(templates[0]["server"], "kicad")
        self.assertEqual(templates[0]["uriTemplate"], "kicad://project/{project_id}/snapshot")

    def test_read_resource_requires_server_when_uri_is_duplicated(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(name="kicad-a", transport="stdio", command="node"),
            local_mcp_client.McpServerConfig(name="kicad-b", transport="stdio", command="node"),
        ]
        shared_uri = "kicad://project/current/info"
        read_result = Obj(contents=[Obj(uri=shared_uri, mimeType="application/json", text='{"name":"demo"}')])
        client = TestableLocalMcpClient(configs)
        client._connections = {
            "kicad-a": FakeConnection(
                resources=[Obj(uri=shared_uri, name="Project Info")],
                read_results={shared_uri: read_result},
            ),
            "kicad-b": FakeConnection(
                resources=[Obj(uri=shared_uri, name="Project Info")],
                read_results={shared_uri: read_result},
            ),
        }

        client.list_resources()
        with self.assertRaises(ValueError):
            client.read_resource(shared_uri)

        payload = json.loads(client.read_resource(shared_uri, server="kicad-a"))
        self.assertEqual(payload["server"], "kicad-a")
        self.assertEqual(payload["contents"][0]["kind"], "text")
        self.assertEqual(payload["contents"][0]["text"], '{"name":"demo"}')

    def test_load_mcp_server_configs_appends_optional_kicad_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "BUTLER_MCP_SERVERS": "",
                "KICAD_MCP_SERVER_PATH": tmpdir,
                "KICAD_PYTHONPATH": "/Applications/KiCad/site-packages",
                "KICAD_MCP_TOOL_PREFIX": "kicad_",
            }
            with patch.dict(os.environ, env, clear=False):
                configs = local_mcp_client._load_mcp_server_configs()

        self.assertEqual([config.name for config in configs], ["butler-local", "kicad"])
        self.assertEqual(configs[1].command, "node")
        self.assertTrue(configs[1].args[0].endswith("dist/index.js"))
        self.assertEqual(configs[1].env["PYTHONPATH"], "/Applications/KiCad/site-packages")
        self.assertEqual(configs[1].tool_prefix, "kicad_")

    def test_load_mcp_server_configs_parses_remote_tls_options(self) -> None:
        raw = json.dumps(
            [
                {
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": "https://example.com/mcp",
                    "tls_verify": False,
                    "tls_ca_bundle": "/tmp/custom-ca.pem",
                }
            ]
        )
        with patch.dict(os.environ, {"BUTLER_MCP_SERVERS": raw}, clear=False):
            configs = local_mcp_client._load_mcp_server_configs()

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0].name, "remote")
        self.assertFalse(configs[0].tls_verify)
        self.assertEqual(configs[0].tls_ca_bundle, "/tmp/custom-ca.pem")

    def test_load_mcp_server_configs_appends_optional_general_filesystem_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "BUTLER_MCP_SERVERS": "",
                "BUTLER_FILESYSTEM_ROOTS": json.dumps([tmpdir]),
            }
            with patch.dict(os.environ, env, clear=False):
                configs = local_mcp_client._load_mcp_server_configs()

        self.assertEqual(
            [config.name for config in configs],
            ["butler-local", "filesystem", "filesystem-diagnostics"],
        )
        self.assertEqual(configs[1].command, "npx")
        self.assertEqual(
            configs[1].args,
            ["-y", "@modelcontextprotocol/server-filesystem", str(Path(tmpdir).resolve())],
        )
        self.assertEqual(configs[1].tool_prefix, "fs_")
        self.assertEqual(configs[2].command, sys.executable)
        self.assertEqual(configs[2].args, ["-m", "butler.mcp_filesystem_diagnostics_server"])

    def test_load_mcp_server_configs_accepts_multiple_general_filesystem_roots(self) -> None:
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            env = {
                "BUTLER_MCP_SERVERS": "",
                "BUTLER_FILESYSTEM_ROOTS": json.dumps([root_a, root_b]),
            }
            with patch.dict(os.environ, env, clear=False):
                config = local_mcp_client._optional_filesystem_server_config()

        self.assertIsNotNone(config)
        self.assertEqual(
            config.args,
            [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                str(Path(root_a).resolve()),
                str(Path(root_b).resolve()),
            ],
        )

    def test_load_mcp_server_configs_adds_filesystem_diagnostics_with_resolved_roots(self) -> None:
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            env = {
                "BUTLER_MCP_SERVERS": "",
                "BUTLER_FILESYSTEM_ROOTS": json.dumps([root_a, root_b]),
            }
            with patch.dict(os.environ, env, clear=False):
                configs = local_mcp_client._load_mcp_server_configs()

        diagnostics = configs[2]
        self.assertEqual(diagnostics.name, "filesystem-diagnostics")
        self.assertEqual(
            json.loads(diagnostics.env["BUTLER_FILESYSTEM_ROOTS"]),
            [str(Path(root_a).resolve()), str(Path(root_b).resolve())],
        )

    def test_load_mcp_server_configs_appends_optional_minecraft_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "BUTLER_MCP_SERVERS": "",
                "MINECRAFT_SERVER_DIR": tmpdir,
            }
            with patch.dict(os.environ, env, clear=False):
                configs = local_mcp_client._load_mcp_server_configs()

        self.assertEqual(
            [config.name for config in configs],
            ["butler-local", "minecraft-filesystem", "minecraft-search"],
        )
        self.assertEqual(configs[1].command, "npx")
        self.assertEqual(
            configs[1].args,
            ["-y", "@modelcontextprotocol/server-filesystem", tmpdir],
        )
        self.assertEqual(configs[2].command, sys.executable)
        self.assertEqual(configs[2].env["MINECRAFT_SERVER_DIR"], tmpdir)

    def test_general_filesystem_write_tools_hidden_by_default(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(
                name=local_mcp_client.DEFAULT_FILESYSTEM_SERVER_NAME,
                transport="stdio",
                command="node",
                tool_prefix=local_mcp_client.DEFAULT_FILESYSTEM_TOOL_PREFIX,
                kind=local_mcp_client.FILESYSTEM_CONFIG_KIND,
            )
        ]
        client = TestableLocalMcpClient(configs)
        client._connections = {
            configs[0].name: FakeConnection(
                name=configs[0].name,
                tools=[
                    Obj(name="list_directory", description="List"),
                    Obj(name="read_text_file", description="Read"),
                    Obj(name="write_file", description="Write"),
                    Obj(name="edit_file", description="Edit"),
                ],
            )
        }

        with patch.dict(os.environ, {"BUTLER_FILESYSTEM_ALLOW_WRITES": "0"}, clear=False):
            tools = client.list_tools()

        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "fs_list_directory",
                "fs_read_text_file",
            ],
        )

    def test_minecraft_filesystem_write_tools_hidden_by_default(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(
                name=local_mcp_client.DEFAULT_MINECRAFT_FILESYSTEM_SERVER_NAME,
                transport="stdio",
                command="node",
                tool_prefix=local_mcp_client.DEFAULT_MINECRAFT_FILESYSTEM_TOOL_PREFIX,
                kind=local_mcp_client.MINECRAFT_FILESYSTEM_CONFIG_KIND,
            )
        ]
        client = TestableLocalMcpClient(configs)
        client._connections = {
            configs[0].name: FakeConnection(
                name=configs[0].name,
                tools=[
                    Obj(name="list_directory", description="List"),
                    Obj(name="read_text_file", description="Read"),
                    Obj(name="write_file", description="Write"),
                    Obj(name="edit_file", description="Edit"),
                ],
            )
        }

        with patch.dict(os.environ, {"MINECRAFT_MCP_ALLOW_WRITES": "0"}, clear=False):
            tools = client.list_tools()

        self.assertEqual(
            [tool["name"] for tool in tools],
            [
                "minecraft_fs_list_directory",
                "minecraft_fs_read_text_file",
            ],
        )

    def test_custom_named_filesystem_server_still_hides_writes_by_kind(self) -> None:
        configs = [
            local_mcp_client.McpServerConfig(
                name="project-files",
                transport="stdio",
                command="node",
                tool_prefix="fs_",
                kind=local_mcp_client.FILESYSTEM_CONFIG_KIND,
            )
        ]
        client = TestableLocalMcpClient(configs)
        client._connections = {
            configs[0].name: FakeConnection(
                name=configs[0].name,
                tools=[
                    Obj(name="list_directory", description="List"),
                    Obj(name="write_file", description="Write"),
                ],
            )
        }

        with patch.dict(os.environ, {"BUTLER_FILESYSTEM_ALLOW_WRITES": "0"}, clear=False):
            tools = client.list_tools()

        self.assertEqual([tool["name"] for tool in tools], ["fs_list_directory"])

    def test_kicad_tool_call_normalizes_missing_root_paths(self) -> None:
        configs = [local_mcp_client.McpServerConfig(name="kicad", transport="stdio", command="node")]
        client = TestableLocalMcpClient(configs)
        connection = FakeConnection(name="kicad")
        client._connections = {"kicad": connection}
        client._tool_routes = {"kicad_open_project": ("kicad", "open_project")}

        result = client.call_tool(
            "kicad_open_project",
            {"filename": "Users/jacksal1/Desktop/KiCADProjects/demo.kicad_pro"},
        )

        self.assertEqual(result, "ok")
        self.assertEqual(
            connection.last_tool_call,
            ("open_project", {"filename": "/Users/jacksal1/Desktop/KiCADProjects/demo.kicad_pro"}),
        )

    def test_non_kicad_tool_call_does_not_rewrite_path_arguments(self) -> None:
        configs = [local_mcp_client.McpServerConfig(name="other", transport="stdio", command="node")]
        client = TestableLocalMcpClient(configs)
        connection = FakeConnection(name="other")
        client._connections = {"other": connection}
        client._tool_routes = {"open_project": ("other", "open_project")}

        result = client.call_tool("open_project", {"filename": "Users/jacksal1/Desktop/demo.txt"})

        self.assertEqual(result, "ok")
        self.assertEqual(
            connection.last_tool_call,
            ("open_project", {"filename": "Users/jacksal1/Desktop/demo.txt"}),
        )


if __name__ == "__main__":
    unittest.main()
