"""Process supervision for the TALOS stack.

The :class:`Supervisor` starts the components in dependency order, pins the
GPU-bound ones to the correct card, streams every child's output through a
single callback (console or GUI), waits for readiness where it matters, and
shuts everything down cleanly on stop.

It is intentionally UI-agnostic: give it a ``log`` callable and it will report
progress and interleaved child output through it.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import config
from .config import LauncherConfig, Ports, REPO_ROOT, venv_python

LogFn = Callable[[str, str], None]
"""``log(source, message)`` — ``source`` is a short tag like ``main`` or
``launcher``; ``message`` is a single line without a trailing newline."""


@dataclass
class ManagedProcess:
    name: str
    popen: subprocess.Popen
    reader: threading.Thread


def _base_env() -> dict[str, str]:
    """Environment shared by every child.

    Forces PCI-bus GPU ordering so ``CUDA_VISIBLE_DEVICES`` indices line up with
    ``nvidia-smi`` (and with the indices the config stored), and disables Python
    output buffering so logs stream live.
    """

    env = os.environ.copy()
    env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _gpu_env(base: dict[str, str], gpu_index: int) -> dict[str, str]:
    """Return ``base`` with ``CUDA_VISIBLE_DEVICES`` set for this component.

    A non-negative index pins the child to exactly that card (overriding any
    inherited value). A negative index means "no pin", so we drop the variable
    entirely — otherwise the child would silently inherit whatever restriction
    the launching shell already had in ``CUDA_VISIBLE_DEVICES``.
    """

    env = dict(base)
    if gpu_index is not None and gpu_index >= 0:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
    else:
        env.pop("CUDA_VISIBLE_DEVICES", None)
    return env


def _venv_env(base: dict[str, str], python_path: Path) -> dict[str, str]:
    """Return ``base`` with ``python_path``'s virtualenv activated.

    Prepends the venv's ``Scripts``/``bin`` directory to ``PATH`` and sets
    ``VIRTUAL_ENV`` — the same thing ``activate`` does. This matters because the
    child processes (and anything *they* spawn) may reference a bare ``python``.
    In particular the ``talos-local`` MCP server is configured with
    ``"command": "python"``: without activation that resolves to whatever Python
    is first on the launcher's ``PATH`` (often a system interpreter with none of
    the project dependencies), so the MCP server crashes on import and the tool
    surface comes up degraded. With activation, bare ``python`` resolves to the
    component's own venv interpreter.

    If ``python_path`` is not inside a recognizable venv (e.g. a bare ``python``
    fallback), the environment is returned unchanged.
    """

    bin_dir = python_path.parent
    if not python_path.exists() or bin_dir.name not in ("Scripts", "bin"):
        return dict(base)

    env = dict(base)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(bin_dir.parent)
    # A stray PYTHONHOME would override the venv; activation clears it.
    env.pop("PYTHONHOME", None)
    return env


def _api_model_env(base: dict[str, str], api_model: str) -> dict[str, str]:
    """Return ``base`` overridden to use hosted OpenAI models instead of local.

    Injected only into the main agent and voice worker for the current run;
    ``settings.env`` is never modified, so unchecking the option next run simply
    falls back to the local Ollama/faster-whisper values there. Real env vars win
    over ``settings.env`` (``load_environment`` never overrides them), which is
    exactly how these overrides take effect in the children.

    Requires ``OPENAI_API_KEY`` in ``.env`` (the OpenAI client reads it when no
    explicit ``TALOS_LLM_API_KEY`` is set).
    """

    env = dict(base)
    env["TALOS_LLM_BACKEND"] = "openai_chat"
    env["TALOS_LLM_BASE_URL"] = "https://api.openai.com/v1"
    env["TALOS_LLM_MODEL"] = api_model
    # Route STT to hosted whisper-1 instead of the local faster-whisper pass.
    env["TALOS_LOCAL_STT"] = "0"
    env["TALOS_REMOTE_STT_FALLBACK"] = "1"
    env["TALOS_REMOTE_LLM_FALLBACK"] = "1"
    # Hosted OpenAI uses max_completion_tokens (the factory also infers this from
    # the openai.com base URL, but set it explicitly to be safe).
    env["TALOS_LLM_MAX_TOKENS_PARAM"] = "max_completion_tokens"
    # OpenAI models do not understand Qwen's /think and /no_think soft switches,
    # so force the think-mode off to avoid leaking those tokens into the prompt.
    env["TALOS_LLM_THINK_MODE"] = "off"
    return env


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Supervisor:
    """Starts, monitors, and stops the TALOS component processes."""

    def __init__(self, cfg: LauncherConfig, log: Optional[LogFn] = None) -> None:
        self._cfg = cfg
        self._ports = Ports.load()
        self._log = log or (lambda source, msg: print(f"[{source}] {msg}"))
        self._procs: list[ManagedProcess] = []
        self._stopping = threading.Event()
        self._lock = threading.Lock()

    # -- logging helpers ---------------------------------------------------

    def _say(self, message: str) -> None:
        self._log("launcher", message)

    # -- process spawning --------------------------------------------------

    def _spawn(self, name: str, args: list[str], env: dict[str, str]) -> ManagedProcess:
        self._say(f"starting {name}: {' '.join(str(a) for a in args)}")
        creationflags = 0
        if os.name == "nt":
            # New process group so we can send CTRL_BREAK to the group without
            # killing the launcher itself.
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
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
        reader = threading.Thread(
            target=self._pump_output, args=(name, popen), daemon=True
        )
        reader.start()
        managed = ManagedProcess(name=name, popen=popen, reader=reader)
        with self._lock:
            self._procs.append(managed)
        return managed

    def _pump_output(self, name: str, popen: subprocess.Popen) -> None:
        assert popen.stdout is not None
        for line in popen.stdout:
            self._log(name, line.rstrip("\n"))
        code = popen.wait()
        if not self._stopping.is_set():
            self._log(name, f"exited with code {code}")

    # -- readiness ---------------------------------------------------------

    def _wait_port(self, name: str, host: str, port: int, timeout: float) -> bool:
        self._say(f"waiting for {name} on {host}:{port} (up to {int(timeout)}s)...")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stopping.is_set():
                return False
            if _port_open(host, port):
                self._say(f"{name} is ready on {host}:{port}")
                return True
            time.sleep(0.5)
        self._say(f"WARNING: {name} did not become ready on {host}:{port}")
        return False

    # -- individual components --------------------------------------------

    def _start_ollama(self, base: dict[str, str]) -> None:
        if _port_open("127.0.0.1", self._ports.ollama):
            self._say(
                "Ollama already running on 127.0.0.1:"
                f"{self._ports.ollama} (not managed by launcher — its GPU pin is "
                "whatever it was started with)."
            )
            return
        if not self._cfg.manage_ollama:
            self._say("Ollama not running and manage_ollama is off; skipping.")
            return
        env = _gpu_env(base, self._cfg.llm_gpu_index)
        # Ollama uses all VRAM on the visible card by default.
        self._spawn("ollama", ["ollama", "serve"], env)
        self._wait_port("ollama", "127.0.0.1", self._ports.ollama, timeout=60)

    def _start_awareness_db(self) -> None:
        if not self._cfg.manage_docker:
            self._say("manage_docker is off; assuming Postgres is already up.")
            return
        self._say("bringing up awareness Postgres container (docker compose)...")
        # Synchronous: --wait blocks until the container is healthy.
        result = self._run_blocking(
            ["docker", "compose", "-f", config.DOCKER_COMPOSE_FILE, "up", "-d", "--wait"],
            name="docker",
        )
        if result != 0:
            self._say(
                "WARNING: docker compose exited non-zero; awareness may fail to connect."
            )

    def _run_migrations(self) -> None:
        if not self._cfg.run_migrations:
            return
        py = venv_python("awareness")
        self._say("running awareness database migrations...")
        self._run_blocking(
            [str(py), "-m", "talos.awareness", "migrate"],
            name="migrate",
            env=_venv_env(_base_env(), py),
        )

    def _start_awareness(self, base: dict[str, str]) -> None:
        py = venv_python("awareness")
        env = _venv_env(base, py)
        self._spawn("awareness", [str(py), "-m", "talos.awareness", "serve"], env)
        self._wait_port(
            "awareness",
            self._ports.awareness_host,
            self._ports.awareness,
            timeout=45,
        )

    def _start_main(self, base: dict[str, str]) -> None:
        py = venv_python("main")
        # The LLM lives on the 5080 via Ollama; the main agent is a client of it,
        # so it does not need a GPU pin of its own. It does spawn the local MCP
        # server as a child ("command": "python"), so its venv must be active.
        env = _venv_env(base, py)
        if self._cfg.use_api_models:
            env = _api_model_env(env, self._cfg.api_llm_model)
        self._spawn("main", [str(py), "-m", "talos"], env)
        self._wait_port(
            "main",
            self._ports.text_agent_host,
            self._ports.text_agent,
            timeout=45,
        )

    def _start_voice(self, base: dict[str, str]) -> None:
        py = venv_python("voice")
        # Speech-to-text (faster-whisper) is pinned to the 2060.
        env = _venv_env(_gpu_env(base, self._cfg.stt_gpu_index), py)
        # In API mode STT is hosted (whisper-1); the local GPU pin is then unused
        # but harmless, and these overrides route the LLM + STT to OpenAI.
        if self._cfg.use_api_models:
            env = _api_model_env(env, self._cfg.api_llm_model)
        # The voice worker reaches the main agent over TALOS_TEXT_AGENT_URL. When
        # the launcher also starts the main agent locally, the worker must talk
        # to it on loopback. A real OS-level TALOS_TEXT_AGENT_URL (e.g. a
        # Tailscale address meant for remote clients) would otherwise win over
        # settings.env -- load_dotenv never overrides a variable already in the
        # environment -- and the co-located worker would time out reaching a
        # remote/unbound interface. Pin it to the local text server explicitly.
        if self._cfg.start_main:
            local_url = f"http://127.0.0.1:{self._ports.text_agent}"
            env["TALOS_TEXT_AGENT_URL"] = local_url
            self._say(f"pointing voice worker at local main agent: {local_url}")
        self._spawn("voice", [str(py), "-m", "talos.voice.worker"], env)

    def _run_blocking(
        self, args: list[str], name: str, env: dict[str, str] | None = None
    ) -> int:
        """Run a short-lived command to completion, streaming its output."""

        try:
            popen = subprocess.Popen(
                args,
                cwd=str(REPO_ROOT),
                env=env or os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self._log(name, f"command not found: {args[0]}")
            return 127
        assert popen.stdout is not None
        for line in popen.stdout:
            self._log(name, line.rstrip("\n"))
        return popen.wait()

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        """Start every enabled component in dependency order."""

        self._stopping.clear()
        base = _base_env()
        gpus = config.detect_gpus()
        if gpus:
            self._say("detected GPUs: " + ", ".join(f"{g.index}:{g.name}" for g in gpus))
        else:
            self._say("no NVIDIA GPUs detected (nvidia-smi unavailable).")
        self._say(
            f"GPU pins -> LLM/Ollama: {self._cfg.llm_gpu_index}, "
            f"STT/voice: {self._cfg.stt_gpu_index}"
        )

        if self._cfg.use_api_models:
            self._say(
                f"API mode ON: using hosted OpenAI models (LLM={self._cfg.api_llm_model}, "
                "STT=whisper-1). Local Ollama will not be started."
            )
            if not config.has_secret("OPENAI_API_KEY"):
                self._say(
                    "WARNING: OPENAI_API_KEY is not set in .env — hosted API calls "
                    "will fail. Add it and restart."
                )

        # In API mode the local LLM is unused, so skip Ollama regardless of the
        # component checkbox (it would only waste VRAM/startup time).
        if self._cfg.start_ollama and not self._cfg.use_api_models:
            self._start_ollama(base)
        if self._cfg.start_awareness_db:
            self._start_awareness_db()
        if self._cfg.start_awareness:
            self._run_migrations()
            self._start_awareness(base)
        if self._cfg.start_main:
            self._start_main(base)
        if self._cfg.start_voice:
            self._start_voice(base)

        self._say("all requested components launched.")

    def is_running(self) -> bool:
        with self._lock:
            return any(p.popen.poll() is None for p in self._procs)

    def wait(self) -> None:
        """Block until interrupted, then stop everything."""

        try:
            while self.is_running():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._say("interrupt received; shutting down...")
        finally:
            self.stop()

    def stop(self) -> None:
        """Terminate managed processes in reverse (dependent-first) order."""

        if self._stopping.is_set():
            return
        self._stopping.set()
        with self._lock:
            procs = list(reversed(self._procs))

        for managed in procs:
            if managed.popen.poll() is not None:
                continue
            self._say(f"stopping {managed.name}...")
            self._terminate(managed.popen)

        # Give them a moment, then hard-kill stragglers.
        deadline = time.monotonic() + 10
        for managed in procs:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                managed.popen.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                self._say(f"force-killing {managed.name}...")
                managed.popen.kill()

        self._say("shutdown complete.")

    @staticmethod
    def _terminate(popen: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                popen.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                popen.terminate()
        except (OSError, ValueError):
            try:
                popen.terminate()
            except OSError:
                pass


def run_headless(cfg: LauncherConfig, log: Optional[LogFn] = None) -> int:
    """Start the stack and block until Ctrl+C. Returns a process exit code."""

    supervisor = Supervisor(cfg, log=log)
    supervisor.start()
    if not supervisor.is_running() and not any(
        [cfg.start_awareness_db, cfg.start_awareness, cfg.start_main, cfg.start_voice]
    ):
        return 0
    supervisor.wait()
    return 0
