from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.mcp_client import client as local_mcp_client
from tests.test_local_mcp_client_resources import FakeConnection, Obj, TestableLocalMcpClient


def _config(name: str, *, mode: str = "", tool_prefix: str = "") -> local_mcp_client.McpServerConfig:
    return local_mcp_client.McpServerConfig(
        name=name,
        transport="stdio",
        command="node",
        tool_prefix=tool_prefix,
        mode=mode,
    )


class ProviderLifecycleModeTests(unittest.TestCase):
    def test_mode_normalization(self) -> None:
        self.assertTrue(_config("a").is_eager())
        self.assertTrue(_config("a", mode="stdio").is_eager())
        self.assertTrue(_config("a", mode="eager").is_eager())
        self.assertTrue(_config("a", mode="lazy").is_deferred())
        self.assertTrue(_config("a", mode="sidecar_manual").is_deferred())
        autostart = _config("a", mode="sidecar_autostart")
        self.assertTrue(autostart.is_deferred())
        self.assertTrue(autostart.is_autostart())
        # Unknown modes fall back to the safe eager-blocking default.
        self.assertTrue(_config("a", mode="bogus").is_eager())


class DiscoveryIsolationTests(unittest.TestCase):
    def _client_with_eager_and_deferred(self, deferred_mode: str):
        configs = [
            _config("butler-local"),
            _config("kicad", mode=deferred_mode, tool_prefix="kicad_"),
        ]
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        eager = FakeConnection(
            name="butler-local",
            tools=[Obj(name="ping", description="Ping", inputSchema={"type": "object"})],
        )
        deferred = FakeConnection(
            name="kicad",
            tools=[Obj(name="open_project", description="Open", inputSchema={"type": "object"})],
        )
        deferred.is_running = False
        client._connections = {"butler-local": eager, "kicad": deferred}
        return client, eager, deferred

    def test_deferred_provider_not_started_during_tool_discovery(self) -> None:
        for mode in ("lazy", "sidecar_manual", "sidecar_autostart"):
            with self.subTest(mode=mode):
                client, _eager, deferred = self._client_with_eager_and_deferred(mode)
                tools = client.list_tools(refresh=True)
                names = [tool["name"] for tool in tools]
                # Eager tools are available; the heavyweight provider was never
                # started and its tools are not exposed while it is cold.
                self.assertIn("ping", names)
                self.assertNotIn("kicad_open_project", names)
                self.assertEqual(deferred.start_count, 0)

    def test_warming_provider_with_open_session_is_not_exposed_or_called(self) -> None:
        # Regression: a heavyweight provider's connection reports is_running=True
        # while its MCP initialize handshake is still in flight. Tool discovery
        # must gate on readiness (marked healthy), not is_running, or it will
        # block on / surface a provider that is not actually ready yet.
        configs = [
            _config("butler-local"),
            _config("kicad", mode="sidecar_autostart", tool_prefix="kicad_"),
        ]
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        eager = FakeConnection(
            name="butler-local",
            tools=[Obj(name="ping", description="Ping", inputSchema={"type": "object"})],
        )
        warming = FakeConnection(
            name="kicad",
            list_tools_error=RuntimeError("kicad should never be listed while warming"),
        )
        warming.is_running = True  # session opened, but handshake not yet done
        client._connections = {"butler-local": eager, "kicad": warming}
        # Provider is mid-handshake: running but not marked healthy.
        client._status["kicad"].status = "warming"
        client._status["kicad"].healthy = False

        tools = client.list_tools(refresh=True)

        self.assertEqual([t["name"] for t in tools], ["ping"])
        # Because it was skipped (never listed), it stays warming rather than
        # being marked degraded by a failed list_tools call.
        statuses = {s["name"]: s for s in client.list_server_status()}
        self.assertEqual(statuses["kicad"]["status"], "warming")

    def test_eager_tools_remain_usable_while_deferred_failed(self) -> None:
        client, _eager, deferred = self._client_with_eager_and_deferred("sidecar_autostart")
        # Simulate a failed sidecar: it is marked failed and stays out of the way.
        asyncio.run(client._mark_server_failed("kicad", RuntimeError("backend crashed")))

        tools = client.list_tools(refresh=True)
        self.assertEqual([t["name"] for t in tools], ["ping"])
        statuses = {s["name"]: s for s in client.list_server_status()}
        self.assertEqual(statuses["butler-local"]["status"], "healthy")
        self.assertIn(statuses["kicad"]["status"], {"degraded", "failed"})
        self.assertIn("backend crashed", statuses["kicad"]["last_error"])


class BackgroundWarmupTests(unittest.TestCase):
    def _client(self, *, start_delay: float = 0.0, start_error: Exception | None = None):
        configs = [
            _config("butler-local"),
            _config("kicad", mode="sidecar_autostart", tool_prefix="kicad_"),
        ]
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        eager = FakeConnection(
            name="butler-local",
            tools=[Obj(name="ping", description="Ping", inputSchema={"type": "object"})],
        )
        kicad = FakeConnection(
            name="kicad",
            tools=[Obj(name="open_project", description="Open", inputSchema={"type": "object"})],
            start_delay=start_delay,
            start_error=start_error,
        )
        kicad.is_running = False
        client._connections = {"butler-local": eager, "kicad": kicad}
        return client, eager, kicad

    def test_autostart_transitions_not_started_to_warming_to_ready(self) -> None:
        async def run_case():
            client, _eager, kicad = self._client(start_delay=0.05)

            self.assertEqual(client._status["kicad"].status, "not_started")

            await client._async_start()

            warming = {s["name"]: s for s in client.list_server_status()}["kicad"]
            self.assertEqual(warming["status"], "warming")
            self.assertFalse(warming["ready"])
            self.assertIn("warming", warming["detail"])

            # Tools are not exposed while warming.
            tools = await client._async_list_tools()
            self.assertNotIn("kicad_open_project", [t["name"] for t in tools])

            await client._pending_starts["kicad"]

            ready = {s["name"]: s for s in client.list_server_status()}["kicad"]
            self.assertEqual(ready["status"], "healthy")
            self.assertTrue(ready["ready"])
            self.assertEqual(kicad.start_count, 1)

            # Once ready, the provider's tools are surfaced.
            tools_after = await client._async_list_tools()
            self.assertIn("kicad_open_project", [t["name"] for t in tools_after])

        asyncio.run(run_case())

    def test_background_warmup_does_not_block_foreground_discovery(self) -> None:
        async def run_case():
            client, _eager, _kicad = self._client(start_delay=0.3)
            await client._async_start()

            started = time.monotonic()
            tools = await client._async_list_tools()
            elapsed = time.monotonic() - started

            # Foreground tool discovery returns immediately with the eager tool,
            # without waiting for the slow KiCad warmup to finish.
            self.assertIn("ping", [t["name"] for t in tools])
            self.assertNotIn("kicad_open_project", [t["name"] for t in tools])
            self.assertLess(elapsed, 0.2)

            client._pending_starts["kicad"].cancel()

        asyncio.run(run_case())

    def test_failed_sidecar_warmup_reports_clear_status(self) -> None:
        async def run_case():
            client, _eager, _kicad = self._client(start_error=RuntimeError("pcbnew missing"))
            await client._async_start()
            pending = client._pending_starts.get("kicad")
            if pending is not None:
                await pending
            await asyncio.sleep(0)

            status = {s["name"]: s for s in client.list_server_status()}["kicad"]
            self.assertIn(status["status"], {"degraded", "failed"})
            self.assertFalse(status["ready"])
            self.assertIn("pcbnew missing", status["last_error"])
            self.assertIn("pcbnew missing", status["detail"])

        asyncio.run(run_case())


class ExplicitStartTests(unittest.TestCase):
    def test_start_server_blocking_brings_up_lazy_provider(self) -> None:
        configs = [_config("kicad", mode="lazy", tool_prefix="kicad_")]
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        connection = FakeConnection(name="kicad", tools=[Obj(name="ping", description="")])
        connection.is_running = False
        client._connections = {"kicad": connection}

        statuses = client.start_server("kicad", background=False)

        status = {s["name"]: s for s in statuses}["kicad"]
        self.assertEqual(status["status"], "healthy")
        self.assertTrue(status["ready"])
        self.assertEqual(connection.start_count, 1)

    def test_start_server_rejects_unknown_provider(self) -> None:
        configs = [_config("kicad", mode="lazy")]
        client = TestableLocalMcpClient(configs)
        client._connections = {"kicad": FakeConnection(name="kicad")}
        with self.assertRaises(KeyError):
            client.start_server("nope")

    def test_lazy_provider_started_on_demand_by_tool_call(self) -> None:
        configs = [_config("kicad", mode="lazy", tool_prefix="kicad_")]
        client = TestableLocalMcpClient(configs, reconnect_backoff_seconds=0)
        connection = FakeConnection(
            name="kicad",
            tools=[Obj(name="ping", description="Ping", inputSchema={"type": "object"})],
        )
        connection.is_running = False
        client._connections = {"kicad": connection}

        # No routes are pre-populated; the cold provider owns kicad_ping by prefix.
        result = client.call_tool("kicad_ping", {})

        self.assertEqual(result, "ok")
        self.assertGreaterEqual(connection.start_count, 1)
        self.assertEqual(connection.last_tool_call, ("ping", {}))


class KicadConfigModeTests(unittest.TestCase):
    def test_kicad_defaults_to_sidecar_autostart(self) -> None:
        env = {
            "BUTLER_MCP_SERVERS": "",
            "KICAD_MCP_SERVER_PATH": "/tmp/kicad-mcp",
            "KICAD_MCP_MODE": "",
            "KICAD_MCP_AUTOSTART": "",
            "KICAD_MCP_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            config = local_mcp_client._optional_kicad_server_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.lifecycle_mode(), local_mcp_client.LIFECYCLE_SIDECAR_AUTOSTART)
        self.assertTrue(config.is_deferred())

    def test_kicad_mode_stdio_preserves_eager_path(self) -> None:
        env = {
            "BUTLER_MCP_SERVERS": "",
            "KICAD_MCP_SERVER_PATH": "/tmp/kicad-mcp",
            "KICAD_MCP_MODE": "stdio",
            "KICAD_MCP_AUTOSTART": "",
            "KICAD_MCP_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            config = local_mcp_client._optional_kicad_server_config()
        self.assertIsNotNone(config)
        self.assertTrue(config.is_eager())

    def test_kicad_autostart_false_demotes_to_lazy(self) -> None:
        env = {
            "BUTLER_MCP_SERVERS": "",
            "KICAD_MCP_SERVER_PATH": "/tmp/kicad-mcp",
            "KICAD_MCP_MODE": "",
            "KICAD_MCP_AUTOSTART": "false",
            "KICAD_MCP_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            config = local_mcp_client._optional_kicad_server_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.lifecycle_mode(), local_mcp_client.LIFECYCLE_LAZY)

    def test_kicad_sidecar_manual_uses_http_url_without_server_path(self) -> None:
        env = {
            "BUTLER_MCP_SERVERS": "",
            "KICAD_MCP_SERVER_PATH": "",
            "KICAD_MCP_MODE": "sidecar_manual",
            "KICAD_MCP_AUTOSTART": "",
            "KICAD_MCP_URL": "http://127.0.0.1:8765/mcp",
        }
        with patch.dict(os.environ, env, clear=False):
            config = local_mcp_client._optional_kicad_server_config()
        self.assertIsNotNone(config)
        self.assertEqual(config.normalized_transport(), "streamable_http")
        self.assertEqual(config.url, "http://127.0.0.1:8765/mcp")
        self.assertEqual(config.lifecycle_mode(), local_mcp_client.LIFECYCLE_SIDECAR_MANUAL)


if __name__ == "__main__":
    unittest.main()
