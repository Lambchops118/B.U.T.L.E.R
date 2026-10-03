import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from butler.launcher import config
from butler.launcher.config import LauncherConfig, venv_python, venv_setup_hint
from butler.launcher.core import Supervisor


class VenvResolutionTests(unittest.TestCase):
    def test_missing_dedicated_venv_does_not_fall_back_to_launcher_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / ".venv" / "Scripts"
            legacy.mkdir(parents=True)
            (legacy / "python.exe").touch()
            with mock.patch.object(config, "REPO_ROOT", root):
                py = venv_python("main")

        self.assertIn(".venv-runtime", py.parts)
        self.assertNotIn(".venv", py.parts)

    def test_existing_dedicated_venv_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / ".venv-display" / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").touch()
            with mock.patch.object(config, "REPO_ROOT", root):
                py = venv_python("display")

        self.assertEqual(py, scripts / "python.exe")

    def test_setup_hint_names_the_matching_requirements_file(self) -> None:
        self.assertIn(".venv-runtime", venv_setup_hint("voice"))
        self.assertIn("requirements-runtime-py312.txt", venv_setup_hint("voice"))
        self.assertIn("requirements-display-py312.txt", venv_setup_hint("display"))
        self.assertEqual(venv_setup_hint("discord-svc"), "")


class SupervisorStartupFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages: list[str] = []
        self.supervisor = Supervisor(
            LauncherConfig(), log=lambda _source, message: self.messages.append(message)
        )

    def test_missing_interpreter_is_reported_and_skipped(self) -> None:
        missing = Path(tempfile.gettempdir()) / "no-such-venv" / "python.exe"
        with mock.patch("butler.launcher.core.venv_python", return_value=missing), mock.patch.object(
            self.supervisor, "_wait_port"
        ) as wait_port:
            self.supervisor._start_main({})

        wait_port.assert_not_called()
        self.assertFalse(self.supervisor.is_running())
        error = next(m for m in self.messages if m.startswith("ERROR: cannot start main"))
        self.assertIn("requirements-runtime-py312.txt", error)

    def test_wait_port_returns_as_soon_as_the_process_exits(self) -> None:
        popen = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        popen.wait()
        managed = mock.Mock(popen=popen)
        with mock.patch("butler.launcher.core._port_open", return_value=False), mock.patch(
            "butler.launcher.core.time.sleep"
        ) as sleep:
            ready = self.supervisor._wait_port("main", "localhost", 1, timeout=45, managed=managed)

        self.assertFalse(ready)
        sleep.assert_not_called()
        self.assertTrue(any("exited with code 3" in m for m in self.messages))


if __name__ == "__main__":
    unittest.main()
