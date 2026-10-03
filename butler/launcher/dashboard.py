"""Start (if needed) and open the read-only debug console from the launcher.

The console is independent of the Butler stack: it only reads logs and
databases, so it is useful before Start, after Stop, and while running. It is
therefore owned by the launcher window rather than the Supervisor, and a
console someone already started by hand is reused instead of duplicated.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from typing import Callable, Optional

from .config import REPO_ROOT, venv_python, venv_setup_hint

LogFn = Callable[[str, str], None]

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8787
DASHBOARD_URL = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
STARTUP_TIMEOUT_SECONDS = 15.0


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class DashboardProcess:
    """The debug console child process this launcher started, if any."""

    def __init__(self, log: LogFn) -> None:
        self._log = log
        self._popen: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def ensure_running(self) -> str:
        """Return the console URL once it accepts connections.

        Raises ``RuntimeError`` with an operator-readable reason otherwise.
        """
        with self._lock:
            if _port_open(DASHBOARD_HOST, DASHBOARD_PORT):
                return DASHBOARD_URL
            if self._popen is None or self._popen.poll() is not None:
                self._popen = self._spawn()
            popen = self._popen
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if popen.poll() is not None:
                raise RuntimeError(
                    f"debug console exited with code {popen.returncode}; see the Logs tab"
                )
            if _port_open(DASHBOARD_HOST, DASHBOARD_PORT):
                return DASHBOARD_URL
            time.sleep(0.2)
        raise RuntimeError(
            f"debug console did not open {DASHBOARD_URL} within {int(STARTUP_TIMEOUT_SECONDS)}s"
        )

    def _spawn(self) -> subprocess.Popen:
        python = venv_python("main")
        args = [
            str(python), "-m", "butler.debug_dashboard",
            "--host", DASHBOARD_HOST, "--port", str(DASHBOARD_PORT),
        ]
        self._log("dashboard", f"starting debug console: {' '.join(args)}")
        env = dict(os.environ)
        env["BUTLER_PROCESS_NAME"] = "dashboard"
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        try:
            popen = subprocess.Popen(
                args,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            hint = venv_setup_hint("main")
            raise RuntimeError(
                f"cannot start the debug console: {python} not found"
                + (f"; {hint}" if hint else "")
            ) from exc
        threading.Thread(target=self._pump, args=(popen,), daemon=True).start()
        return popen

    def _pump(self, popen: subprocess.Popen) -> None:
        assert popen.stdout is not None
        for line in popen.stdout:
            self._log("dashboard", line.rstrip("\n"))

    def stop(self) -> None:
        """Stop the console only if this launcher started it."""
        with self._lock:
            popen, self._popen = self._popen, None
        if popen is None or popen.poll() is not None:
            return
        popen.terminate()
        try:
            popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            popen.kill()
