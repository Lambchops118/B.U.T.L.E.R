from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.services import smart_plugs

_DEVICES_JSON = (
    '[{"id":"living_room_lamp","name":"living room lamp","host":"10.0.0.1"},'
    '{"id":"office_switch","name":"office switch","host":"10.0.0.2"}]'
)


class SmartPlugsTests(unittest.TestCase):
    def test_load_devices_parses_configured_json(self) -> None:
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": _DEVICES_JSON}):
            devices = smart_plugs._load_devices()

        self.assertEqual(set(devices), {"living_room_lamp", "office_switch"})
        self.assertEqual(devices["living_room_lamp"].host, "10.0.0.1")

    def test_load_devices_empty_when_unset(self) -> None:
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": ""}):
            self.assertEqual(smart_plugs._load_devices(), {})

    def test_load_devices_rejects_invalid_json(self) -> None:
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": "not json"}):
            with self.assertRaises(RuntimeError):
                smart_plugs._load_devices()

    def test_set_device_power_rejects_unknown_id(self) -> None:
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": _DEVICES_JSON}):
            with self.assertRaises(ValueError):
                smart_plugs.set_device_power("no_such_device", True)

    def test_set_device_power_dispatches_to_configured_host(self) -> None:
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": _DEVICES_JSON}), mock.patch(
            "butler.services.smart_plugs.asyncio.run"
        ) as run_mock:
            result = smart_plugs.set_device_power("office_switch", True)

        run_mock.assert_called_once()
        self.assertIn("office switch", result)
        self.assertIn("on", result)

    def test_set_all_devices_power_reports_partial_failure(self) -> None:
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": _DEVICES_JSON}), mock.patch(
            "butler.services.smart_plugs.asyncio.run",
            return_value=[None, RuntimeError("timeout")],
        ):
            result = smart_plugs.set_all_devices_power(False)

        self.assertIn("1/2", result)
        self.assertIn("office switch: timeout", result)

    def test_set_all_skips_appliances(self) -> None:
        devices_json = (
            '[{"id":"lamp","host":"10.0.0.1"},'
            '{"id":"ceiling","host":"10.0.0.2","kind":"light"},'
            '{"id":"coffee-pot","host":"10.0.0.3","kind":"appliance"}]'
        )
        switched: list[str] = []

        async def fake_set_power(host: str, on: bool) -> None:
            switched.append(host)

        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": devices_json}), mock.patch.object(
            smart_plugs, "_set_power", fake_set_power
        ):
            result = smart_plugs.set_all_devices_power(False)
            listing = smart_plugs.list_devices()

        self.assertEqual(sorted(switched), ["10.0.0.1", "10.0.0.2"])
        self.assertIn("2/2 lights", result)
        self.assertIn("coffee-pot", listing)
        self.assertIn("appliance", listing)

    def test_load_devices_rejects_unknown_kind(self) -> None:
        bad = '[{"id":"x","host":"10.0.0.1","kind":"toaster"}]'
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": bad}):
            with self.assertRaises(RuntimeError):
                smart_plugs._load_devices()

    def test_connect_retries_a_first_attempt_authentication_error(self) -> None:
        class AuthError(Exception):
            pass

        attempts: list[int] = []

        class FakeDevice:
            disconnected = 0

            async def update(self) -> None:
                pass

            async def disconnect(self) -> None:
                FakeDevice.disconnected += 1

        async def discover_single(host, credentials=None, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise AuthError("Server response doesn't match our challenge")
            return FakeDevice()

        fake_kasa = mock.Mock()
        fake_kasa.exceptions.AuthenticationError = AuthError
        fake_kasa.Discover.discover_single = discover_single

        import asyncio

        with mock.patch.object(smart_plugs, "_kasa", return_value=fake_kasa), mock.patch.object(
            smart_plugs, "_AUTH_RETRY_DELAY_SECONDS", 0
        ):
            device = asyncio.run(smart_plugs._connect("10.0.0.2"))

        self.assertIsInstance(device, FakeDevice)
        self.assertEqual(len(attempts), 2)

    def test_connect_gives_up_after_repeated_authentication_errors(self) -> None:
        class AuthError(Exception):
            pass

        calls: list[int] = []

        async def discover_single(host, credentials=None, timeout=None):
            calls.append(1)
            raise AuthError("bad credentials")

        fake_kasa = mock.Mock()
        fake_kasa.exceptions.AuthenticationError = AuthError
        fake_kasa.Discover.discover_single = discover_single

        import asyncio

        with mock.patch.object(smart_plugs, "_kasa", return_value=fake_kasa), mock.patch.object(
            smart_plugs, "_AUTH_RETRY_DELAY_SECONDS", 0
        ):
            with self.assertRaises(AuthError):
                asyncio.run(smart_plugs._connect("10.0.0.2"))

        self.assertEqual(len(calls), smart_plugs._AUTH_RETRIES + 1)

    def test_missing_python_kasa_fails_only_when_a_device_is_controlled(self) -> None:
        # The provider must stay importable without python-kasa so a missing
        # dependency does not take down the whole aggregate MCP server.
        with mock.patch.dict("sys.modules", {"kasa": None}):
            with self.assertRaisesRegex(RuntimeError, "python-kasa is not installed"):
                smart_plugs._kasa()



class SmartPlugToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_runs_inside_a_running_event_loop(self) -> None:
        # Regression: the tools were sync and the service calls asyncio.run(),
        # which raised "cannot be called from a running event loop" under FastMCP.
        from mcp.server.fastmcp import FastMCP

        from butler.mcp_servers.providers import smart_plugs as provider

        switched: list[str] = []

        async def fake_set_power(host: str, on: bool) -> None:
            switched.append(f"{host}:{on}")

        server = FastMCP("test")
        with mock.patch.dict("os.environ", {"BUTLER_SMART_PLUGS": _DEVICES_JSON}), mock.patch.object(
            smart_plugs, "_set_power", fake_set_power
        ):
            provider.register(server)
            await server.call_tool("set_smart_plug", {"device_id": "office_switch", "state": "off"})
            await server.call_tool("set_smart_plug", {"device_id": "all", "state": "on"})

        self.assertEqual(
            sorted(switched), ["10.0.0.1:True", "10.0.0.2:False", "10.0.0.2:True"]
        )


if __name__ == "__main__":
    unittest.main()
