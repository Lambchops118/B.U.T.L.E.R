from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.launcher import dashboard
from butler.launcher.dashboard import DASHBOARD_URL, DashboardProcess


class _FakePopen:
    def __init__(self, exit_code=None):
        self.returncode = exit_code
        self.stdout = iter(())
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class LauncherDashboardTests(unittest.TestCase):
    def test_reuses_a_console_that_is_already_listening(self) -> None:
        process = DashboardProcess(log=lambda *_: None)
        with mock.patch.object(dashboard, "_port_open", return_value=True), \
                mock.patch.object(dashboard.subprocess, "Popen") as popen:
            self.assertEqual(process.ensure_running(), DASHBOARD_URL)
        popen.assert_not_called()
        process.stop()  # nothing it started, nothing to stop

    def test_starts_console_and_waits_for_its_port(self) -> None:
        process = DashboardProcess(log=lambda *_: None)
        fake = _FakePopen()
        with mock.patch.object(dashboard, "_port_open", side_effect=[False, False, True]), \
                mock.patch.object(dashboard.subprocess, "Popen", return_value=fake) as popen, \
                mock.patch.object(dashboard.time, "sleep"):
            self.assertEqual(process.ensure_running(), DASHBOARD_URL)
        args = popen.call_args[0][0]
        self.assertEqual(args[1:3], ["-m", "butler.debug_dashboard"])
        self.assertIn("127.0.0.1", args)
        process.stop()
        self.assertTrue(fake.terminated)

    def test_reports_a_console_that_exits_during_startup(self) -> None:
        process = DashboardProcess(log=lambda *_: None)
        with mock.patch.object(dashboard, "_port_open", return_value=False), \
                mock.patch.object(dashboard.subprocess, "Popen", return_value=_FakePopen(exit_code=1)):
            with self.assertRaisesRegex(RuntimeError, "exited with code 1"):
                process.ensure_running()


if __name__ == "__main__":
    unittest.main()
