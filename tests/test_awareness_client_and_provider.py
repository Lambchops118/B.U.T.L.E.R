"""Main-agent side of Phase 5: awareness client, router fallback, MCP tools."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.services import awareness_client


class _StubAwarenessHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if self.path.startswith("/situation"):
            body = {
                "as_of": "2026-07-16T12:00:00+00:00",
                "text": "ALERT[critical] Overflow detected: pot_1 (open, x1)",
            }
            payload = json.dumps(body).encode()
            self.send_response(200)
        elif self.path.startswith("/state/"):
            payload = json.dumps({"entity_id": "fan", "properties": []}).encode()
            self.send_response(200)
        else:
            payload = json.dumps({"detail": "not found"}).encode()
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:  # keep test output quiet
        pass


class _StubServer:
    def __enter__(self) -> str:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubAwarenessHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


class AwarenessClientTest(unittest.TestCase):
    def setUp(self) -> None:
        awareness_client._cache.clear()

    def test_situation_fetch_and_router_fallback(self) -> None:
        with _StubServer() as base_url:
            with mock.patch.dict(
                "os.environ", {"TALOS_AWARENESS_API_URL": base_url}
            ):
                text = awareness_client.fetch_situation_text()
                self.assertIn("Situation as of 2026-07-16T12:00:00+00:00", text)
                self.assertIn("ALERT[critical]", text)
                self.assertEqual(
                    awareness_client.snapshot_with_fallback("legacy"), text
                )

    def test_unreachable_backend_falls_back_truthfully(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "TALOS_AWARENESS_API_URL": "http://127.0.0.1:1",  # nothing listens
                "TALOS_AWARENESS_CLIENT_TIMEOUT": "0.2",
            },
        ):
            self.assertIsNone(awareness_client.fetch_situation_text())
            self.assertEqual(
                awareness_client.snapshot_with_fallback("legacy snapshot"),
                "legacy snapshot",
            )
            with self.assertRaises(RuntimeError):
                awareness_client.get_json("/state/fan")

    def test_disabled_flag_skips_fetch(self) -> None:
        with mock.patch.dict(
            "os.environ", {"TALOS_AWARENESS_SITUATION_ENABLED": "0"}
        ):
            self.assertIsNone(awareness_client.fetch_situation_text())

    def test_http_error_carries_detail(self) -> None:
        with _StubServer() as base_url:
            with mock.patch.dict(
                "os.environ", {"TALOS_AWARENESS_API_URL": base_url}
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    awareness_client.get_json("/bogus")
                self.assertIn("404", str(ctx.exception))


class AwarenessProviderTest(unittest.TestCase):
    def test_provider_registers_read_tools(self) -> None:
        import asyncio

        from mcp.server.fastmcp import FastMCP

        from talos.mcp_servers.providers.awareness import register

        server = FastMCP("test-awareness")
        register(server)
        tools = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertEqual(
            tools,
            {
                "get_current_state",
                "get_recent_events",
                "get_sensor_history",
                "get_active_alerts",
                "get_system_health",
                "get_event_provenance",
                "get_awareness_capabilities",
                "search_memory",
                "request_device_action",
                "get_action_status",
            },
        )

    def test_tool_returns_bounded_error_when_backend_down(self) -> None:
        import asyncio

        from mcp.server.fastmcp import FastMCP

        from talos.mcp_servers.providers.awareness import register

        server = FastMCP("test-awareness")
        register(server)
        with mock.patch.dict(
            "os.environ",
            {
                "TALOS_AWARENESS_API_URL": "http://127.0.0.1:1",
                "TALOS_AWARENESS_CLIENT_TIMEOUT": "0.2",
            },
        ):
            result = asyncio.run(server.call_tool("get_current_state", {"entity_id": "fan"}))
        text = result[0][0].text if isinstance(result, tuple) else result[0].text
        payload = json.loads(text)
        self.assertIn("unreachable", payload["error"])


if __name__ == "__main__":
    unittest.main()
