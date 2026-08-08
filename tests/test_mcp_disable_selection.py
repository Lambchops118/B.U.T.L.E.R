"""Tests for switching MCP servers / provider groups off at startup.

Covers the whole path the launcher checkboxes take: the saved selection becomes
``TALOS_MCP_DISABLED_SERVERS`` / ``TALOS_MCP_DISABLED_PROVIDERS`` in the main
agent's environment, which the MCP client turns into a shorter server list and a
``--disable-provider`` argument for the built-in aggregate server.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.launcher.config import LauncherConfig
from talos.launcher.core import _mcp_env
from talos.mcp_client import client as local_mcp_client


_EXPLICIT_SERVERS = json.dumps(
    [
        {"name": "talos-local", "transport": "stdio", "command": "python", "args": ["-m", "talos.mcp_server"]},
        {"name": "extra", "transport": "stdio", "command": "node", "args": ["extra.js"]},
    ]
)


def _load(**env: str) -> list[local_mcp_client.McpServerConfig]:
    base = {
        "TALOS_MCP_DISABLED_SERVERS": "",
        "TALOS_MCP_DISABLED_PROVIDERS": "",
        "TALOS_FILESYSTEM_ROOTS": "",
        "MINECRAFT_SERVER_DIR": "",
        "KICAD_MCP_SERVER_PATH": "",
        "KICAD_MCP_URL": "",
        "TALOS_DISABLE_ALL_TOOLS": "",
    }
    base.update(env)
    with patch.dict(os.environ, base, clear=False):
        return local_mcp_client._load_mcp_server_configs()


class DisabledServerTests(unittest.TestCase):
    def test_nothing_disabled_keeps_every_server(self) -> None:
        configs = _load(TALOS_MCP_SERVERS=_EXPLICIT_SERVERS)
        self.assertEqual([config.name for config in configs], ["talos-local", "extra"])
        self.assertEqual(configs[0].args, ["-m", "talos.mcp_server"])

    def test_named_server_is_dropped(self) -> None:
        configs = _load(TALOS_MCP_SERVERS=_EXPLICIT_SERVERS, TALOS_MCP_DISABLED_SERVERS="extra")
        self.assertEqual([config.name for config in configs], ["talos-local"])

    def test_disable_list_accepts_json_and_is_case_insensitive(self) -> None:
        configs = _load(
            TALOS_MCP_SERVERS=_EXPLICIT_SERVERS,
            TALOS_MCP_DISABLED_SERVERS=json.dumps(["Extra", "talos-local"]),
        )
        self.assertEqual(configs, [])

    def test_default_server_list_can_be_filtered_too(self) -> None:
        configs = _load(TALOS_MCP_SERVERS="", TALOS_MCP_DISABLED_SERVERS="talos-local")
        self.assertEqual(configs, [])


class DisabledProviderTests(unittest.TestCase):
    def test_providers_are_passed_to_the_aggregate_server(self) -> None:
        configs = _load(
            TALOS_MCP_SERVERS=_EXPLICIT_SERVERS,
            TALOS_MCP_DISABLED_PROVIDERS="awareness, home_automation",
        )
        self.assertEqual(
            configs[0].args,
            ["-m", "talos.mcp_server", "--disable-provider", "awareness,home_automation"],
        )
        # Only the aggregate server is rewritten.
        self.assertEqual(configs[1].args, ["extra.js"])

    def test_default_aggregate_config_is_rewritten(self) -> None:
        configs = _load(TALOS_MCP_SERVERS="", TALOS_MCP_DISABLED_PROVIDERS="awareness")
        self.assertEqual(
            configs[0].args, ["-m", "talos.mcp_server", "--disable-provider", "awareness"]
        )


class EveryServerDisabledTests(unittest.TestCase):
    """Turning everything off must leave a working agent, not a broken one.

    The client used to reject an empty server list outright, which took down the
    whole turn (and with it voice output) the first time the launcher made "all
    off" reachable.
    """

    def setUp(self) -> None:
        self.client = local_mcp_client.LocalMcpClient([])
        self.addCleanup(self.client.stop)

    def test_client_accepts_an_empty_server_list(self) -> None:
        self.assertFalse(self.client.enabled())
        self.assertEqual(self.client.list_tools(), [])
        self.assertEqual(self.client.openai_tool_definitions(), [])
        self.assertEqual(self.client.list_server_status(), [])

    def test_no_event_loop_is_started(self) -> None:
        self.client.start()
        self.assertIsNone(self.client._loop)

    def test_resource_listings_are_empty(self) -> None:
        self.assertEqual(self.client.list_resources(), [])
        self.assertEqual(self.client.list_resource_templates(), [])
        self.assertEqual(self.client.retry_server(), [])

    def test_calling_a_tool_explains_why_it_is_gone(self) -> None:
        with self.assertRaises(local_mcp_client.McpProtocolError) as caught:
            self.client.call_tool("get_current_weather", {})
        self.assertIn("switched off", str(caught.exception))


class GlobalToolKillSwitchTests(unittest.TestCase):
    def test_no_servers_are_configured(self) -> None:
        self.assertEqual(_load(TALOS_DISABLE_ALL_TOOLS="1"), [])

    def test_switch_is_off_by_default(self) -> None:
        self.assertTrue(_load(TALOS_MCP_SERVERS=_EXPLICIT_SERVERS))

    def test_explicit_servers_are_ignored_while_on(self) -> None:
        self.assertEqual(
            _load(TALOS_MCP_SERVERS=_EXPLICIT_SERVERS, TALOS_DISABLE_ALL_TOOLS="true"), []
        )


class LauncherEnvTests(unittest.TestCase):
    def test_selection_becomes_environment_variables(self) -> None:
        cfg = LauncherConfig()
        cfg.disabled_mcp_servers = ["kicad", "minecraft-search"]
        cfg.disabled_mcp_providers = ["home_automation"]
        env = _mcp_env({}, cfg)
        self.assertEqual(env["TALOS_MCP_DISABLED_SERVERS"], "kicad,minecraft-search")
        self.assertEqual(env["TALOS_MCP_DISABLED_PROVIDERS"], "home_automation")

    def test_empty_selection_clears_inherited_values(self) -> None:
        env = _mcp_env(
            {"TALOS_MCP_DISABLED_SERVERS": "kicad", "TALOS_MCP_DISABLED_PROVIDERS": "awareness"},
            LauncherConfig(),
        )
        self.assertNotIn("TALOS_MCP_DISABLED_SERVERS", env)
        self.assertNotIn("TALOS_MCP_DISABLED_PROVIDERS", env)


if __name__ == "__main__":
    unittest.main()
