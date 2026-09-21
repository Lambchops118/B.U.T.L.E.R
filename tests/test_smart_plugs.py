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

    def test_missing_python_kasa_fails_only_when_a_device_is_controlled(self) -> None:
        # The provider must stay importable without python-kasa so a missing
        # dependency does not take down the whole aggregate MCP server.
        with mock.patch.dict("sys.modules", {"kasa": None}):
            with self.assertRaisesRegex(RuntimeError, "python-kasa is not installed"):
                smart_plugs._kasa()


if __name__ == "__main__":
    unittest.main()
