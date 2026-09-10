"""Physical display power: the half of sleep mode that lives on the TV.

Sleep mode had always dimmed the rendered panel while leaving the screen it is
shown on fully lit, so the user had to ask for the screen separately. These
tests pin the two mechanisms the scheduler already proved in production -- adb
standby to go dark, an MQTT ``tv_display/wake_status`` publish to come back --
to the sleep flag, and pin the failure behavior: a display that cannot be
reached never turns a good night into an error.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.services import display_power


class DisplayPowerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict("os.environ", {"TALOS_DISPLAY_POWER_ENABLED": "1"}))

    def test_sleep_goes_dark_and_wake_illuminates(self) -> None:
        with patch.object(display_power, "_go_dark") as dark, \
             patch.object(display_power, "_illuminate") as lit:
            display_power.apply(True, block=True)
            display_power.apply(False, block=True)
        self.assertEqual((dark.call_count, lit.call_count), (1, 1))
        self.assertEqual(display_power.last_result()["action"], "illuminate")
        self.assertTrue(display_power.last_result()["ok"])

    def test_wake_publishes_the_topic_the_pi_listens_on(self) -> None:
        """The Pi's control_display.py subscribes to this topic and maps 1 -> power on."""
        with patch.object(display_power, "_publish") as publish:
            display_power.apply(False, block=True)
        publish.assert_called_once_with("1")
        self.assertEqual(display_power.TOPIC, "tv_display/wake_status")

    def test_an_unreachable_display_is_recorded_not_raised(self) -> None:
        with patch.object(display_power, "_go_dark", side_effect=OSError("no route")):
            display_power.apply(True, block=True)
        result = display_power.last_result()
        self.assertEqual(result["action"], "dim")
        self.assertFalse(result["ok"])
        self.assertIn("no route", result["detail"])

    def test_disabled_configuration_touches_no_hardware(self) -> None:
        with patch.dict("os.environ", {"TALOS_DISPLAY_POWER_ENABLED": "0"}), \
             patch.object(display_power, "_go_dark") as dark:
            display_power.apply(True, block=True)
        dark.assert_not_called()
        self.assertIn("disabled", display_power.last_result()["detail"])

    def test_dispatch_does_not_block_the_caller(self) -> None:
        """The spoken good night must not wait on an adb round trip."""
        with patch.object(display_power, "_go_dark") as dark:
            display_power.apply(True)
            for thread in [t for t in __import__("threading").enumerate()
                           if t.name.startswith("display-")]:
                thread.join(timeout=5)
        dark.assert_called_once()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
