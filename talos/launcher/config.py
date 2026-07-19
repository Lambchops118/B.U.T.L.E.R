"""Configuration for the TALOS launcher.

Two kinds of state live here:

1. **TALOS settings** already stored in ``settings.env`` (e.g. the Ollama model,
   MQTT enablement, ports). The launcher reads these to know how to start and
   probe each service, and can write a few of them back (comment-preserving) so
   the GUI can edit them in place.
2. **Launcher-only choices** — which components to start, whether to manage
   Ollama/Docker, and the GPU assignment. These are stored in
   ``launcher.config.json`` at the repo root (git-ignored).

Everything here is deliberately dependency-free (standard library only) so the
launcher can run from any of the project virtual environments without importing
the heavier TALOS runtime.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path


# talos/launcher/config.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]

SETTINGS_PATH = REPO_ROOT / "settings.env"
LAUNCHER_CONFIG_PATH = REPO_ROOT / "launcher.config.json"

# Per-component Python interpreters. Windows layout (Scripts) with a POSIX
# fallback so the same code works if the repo is ever run under WSL/Linux.
VENV_DIRS = {
    "main": ".venv-main",
    "voice": ".venv-voice",
    "awareness": ".venv-awareness",
}

# Fallback ("legacy") venv used if a per-component one is missing.
FALLBACK_VENV = ".venv"

DOCKER_COMPOSE_FILE = "docker-compose.awareness.yml"


def venv_python(component: str) -> Path:
    """Return the interpreter path for a component, falling back sensibly.

    Prefers the dedicated per-component venv, then the legacy ``.venv``, then a
    bare ``python`` on PATH. Never raises here so the GUI can show a clear
    "interpreter missing" message instead of crashing.
    """

    candidates = []
    if component in VENV_DIRS:
        candidates.append(VENV_DIRS[component])
    candidates.append(FALLBACK_VENV)

    for rel in candidates:
        win = REPO_ROOT / rel / "Scripts" / "python.exe"
        if win.exists():
            return win
        posix = REPO_ROOT / rel / "bin" / "python"
        if posix.exists():
            return posix

    # Last resort: whatever "python" resolves to on PATH.
    return Path(shutil.which("python") or "python")


# ---------------------------------------------------------------------------
# settings.env reading / writing (comment-preserving)
# ---------------------------------------------------------------------------

# Built-in defaults for the handful of keys the launcher needs when a value is
# neither active nor present as a commented default in settings.env.
_SETTINGS_DEFAULTS = {
    "TALOS_LLM_MODEL": "hermes3:8b",
    "TALOS_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
    "TALOS_LLM_THINK_MODE": "off",
    "TALOS_AWARENESS_MQTT_ENABLED": "1",
    "TALOS_AWARENESS_MQTT_HOST": "192.168.1.160",
    "TALOS_AWARENESS_MQTT_PORT": "1883",
    "TALOS_AWARENESS_API_PORT": "8600",
    "TALOS_AWARENESS_API_HOST": "127.0.0.1",
    "TEXT_AGENT_PORT": "8420",
    "TEXT_AGENT_HOST": "localhost",
}

_ACTIVE_RE = "^(?P<indent>\\s*)(?P<key>{key})=(?P<value>.*)$"
_COMMENTED_RE = "^(?P<indent>\\s*)#\\s*(?P<key>{key})=(?P<value>.*)$"


def _settings_lines() -> list[str]:
    if not SETTINGS_PATH.exists():
        return []
    return SETTINGS_PATH.read_text(encoding="utf-8").splitlines()


def get_setting(key: str, default: str | None = None) -> str:
    """Return the effective value of a ``settings.env`` key.

    Active (uncommented) assignment wins; otherwise the first commented default
    in the file is used; otherwise the launcher's built-in default, then the
    caller-supplied ``default``, then an empty string.
    """

    active_re = re.compile(_ACTIVE_RE.format(key=re.escape(key)))
    commented_re = re.compile(_COMMENTED_RE.format(key=re.escape(key)))

    commented_value: str | None = None
    for line in _settings_lines():
        match = active_re.match(line)
        if match:
            return _strip_inline(match.group("value"))
        if commented_value is None:
            cmatch = commented_re.match(line)
            if cmatch:
                commented_value = _strip_inline(cmatch.group("value"))

    if commented_value is not None:
        return commented_value
    if key in _SETTINGS_DEFAULTS:
        return _SETTINGS_DEFAULTS[key]
    return default if default is not None else ""


def _strip_inline(raw: str) -> str:
    """Strip surrounding whitespace and a trailing ``# comment`` from a value.

    Values in this file are unquoted and never contain a literal ``#``, so a
    simple split is safe and keeps parsing predictable.
    """

    value = raw.strip()
    if " #" in value:
        value = value.split(" #", 1)[0].strip()
    return value


def set_setting(key: str, value: str) -> None:
    """Write ``key=value`` into ``settings.env``, preserving comments/order.

    Resolution order: replace the first active assignment; else uncomment the
    first commented default and set it; else append at the end of the file.
    """

    lines = _settings_lines()
    active_re = re.compile(_ACTIVE_RE.format(key=re.escape(key)))
    commented_re = re.compile(_COMMENTED_RE.format(key=re.escape(key)))

    for i, line in enumerate(lines):
        match = active_re.match(line)
        if match:
            lines[i] = f"{match.group('indent')}{key}={value}"
            _write_settings(lines)
            return

    for i, line in enumerate(lines):
        match = commented_re.match(line)
        if match:
            lines[i] = f"{match.group('indent')}{key}={value}"
            _write_settings(lines)
            return

    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"{key}={value}")
    _write_settings(lines)


def _write_settings(lines: list[str]) -> None:
    SETTINGS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def setting_bool(key: str, default: bool = False) -> bool:
    raw = get_setting(key, "1" if default else "0").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


ENV_PATH = REPO_ROOT / ".env"


def has_secret(key: str) -> bool:
    """True if ``key`` has a non-empty value in the OS env or in ``.env``.

    Used to warn before enabling API models when ``OPENAI_API_KEY`` is missing.
    We do not return the value — only whether it is set — so no secret is logged.
    """

    import os

    if os.getenv(key, "").strip():
        return True
    if not ENV_PATH.exists():
        return False
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*(.*)$")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            value = match.group(1).strip().strip("'\"").strip()
            if value:
                return True
    return False


# ---------------------------------------------------------------------------
# GPU detection
# ---------------------------------------------------------------------------

@dataclass
class Gpu:
    index: int
    name: str


def detect_gpus() -> list[Gpu]:
    """Enumerate NVIDIA GPUs via ``nvidia-smi``.

    ``nvidia-smi`` indexes by PCI bus order, which is the same order we force on
    CUDA (``CUDA_DEVICE_ORDER=PCI_BUS_ID``), so these indices are exactly what
    goes into ``CUDA_VISIBLE_DEVICES``. Returns an empty list if the tool is
    unavailable.
    """

    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    try:
        out = subprocess.run(
            [smi, "--query-gpu=index,name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []

    gpus: list[Gpu] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        idx, _, name = line.partition(",")
        try:
            gpus.append(Gpu(index=int(idx.strip()), name=name.strip()))
        except ValueError:
            continue
    return gpus


def _pick_gpu(gpus: list[Gpu], preferred: str, fallback_index: int) -> int:
    """Index of the first GPU whose name contains ``preferred`` (e.g. "5080")."""

    for gpu in gpus:
        if preferred.lower() in gpu.name.lower():
            return gpu.index
    if gpus and 0 <= fallback_index < len(gpus):
        return gpus[fallback_index].index
    return fallback_index


# ---------------------------------------------------------------------------
# Launcher configuration (launcher.config.json)
# ---------------------------------------------------------------------------

@dataclass
class LauncherConfig:
    """Which pieces to start and how. Persisted to ``launcher.config.json``."""

    # Components to launch, in dependency order.
    start_ollama: bool = True
    start_awareness_db: bool = True
    start_awareness: bool = True
    start_main: bool = True
    start_voice: bool = True

    # Whether the launcher manages these itself vs. assuming they're already up.
    manage_ollama: bool = True
    manage_docker: bool = True
    run_migrations: bool = True

    # GPU pinning. -1 means "no pin" (let CUDA see every card).
    llm_gpu_index: int = 0
    stt_gpu_index: int = 1

    # Use hosted OpenAI API models instead of the local Ollama LLM + local STT.
    # When on, the launcher injects the hosted-model settings into the main agent
    # and voice worker for that run (settings.env is left untouched) and skips
    # the local Ollama server. Requires OPENAI_API_KEY in .env.
    use_api_models: bool = False
    api_llm_model: str = "gpt-4o-mini"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def load(cls) -> "LauncherConfig":
        cfg = cls()
        if LAUNCHER_CONFIG_PATH.exists():
            try:
                data = json.loads(LAUNCHER_CONFIG_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = {}
            known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
            for key, value in data.items():
                if key in known:
                    setattr(cfg, key, value)
        else:
            # First run: seed GPU choices from detected hardware.
            gpus = detect_gpus()
            cfg.llm_gpu_index = _pick_gpu(gpus, "5080", 0)
            cfg.stt_gpu_index = _pick_gpu(gpus, "2060", 1)
        return cfg

    def save(self) -> None:
        LAUNCHER_CONFIG_PATH.write_text(self.to_json() + "\n", encoding="utf-8")


@dataclass
class Ports:
    """Ports the launcher probes for readiness, read from settings.env."""

    ollama: int = 11434
    awareness: int = field(default=8600)
    text_agent: int = field(default=8420)
    awareness_host: str = "127.0.0.1"
    text_agent_host: str = "localhost"

    @classmethod
    def load(cls) -> "Ports":
        def as_int(key: str, default: int) -> int:
            try:
                return int(get_setting(key, str(default)))
            except ValueError:
                return default

        return cls(
            ollama=11434,
            awareness=as_int("TALOS_AWARENESS_API_PORT", 8600),
            text_agent=as_int("TEXT_AGENT_PORT", 8420),
            awareness_host=get_setting("TALOS_AWARENESS_API_HOST", "127.0.0.1"),
            text_agent_host=get_setting("TEXT_AGENT_HOST", "localhost"),
        )
