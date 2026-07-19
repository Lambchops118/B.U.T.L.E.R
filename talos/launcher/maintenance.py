"""One-shot maintenance actions for the launcher (memory clearing).

These are destructive and irreversible; callers must confirm with the user
first. Both clears require TALOS to be stopped: the conversation store is a
SQLite file that a running main agent holds open (deleting it under a lock fails
on Windows), and clearing while processes write is otherwise racy.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import config
from .config import REPO_ROOT, venv_python

LogFn = Callable[[str, str], None]


def _default_log(source: str, message: str) -> None:
    print(f"[{source}] {message}")


# talos/memory/store.py:DEFAULT_MEMORY_DB_PATH
_DEFAULT_CONVERSATION_DB = REPO_ROOT / "db" / "talos_memory.sqlite3"


def conversation_db_path() -> Path:
    """Resolve the persistent-conversation SQLite path the same way the app does."""

    override = os.getenv("TALOS_MEMORY_DB_PATH", "").strip()
    if override and override != ":memory:":
        return Path(override)
    if override == ":memory:":
        # In-memory store has nothing on disk to clear.
        return Path(":memory:")
    return _DEFAULT_CONVERSATION_DB


def clear_conversation_memory(log: Optional[LogFn] = None) -> str:
    """Delete the persistent conversation store (facts, summaries, history).

    Removes the SQLite database plus its WAL/SHM sidecars. The schema is
    recreated empty the next time the main agent starts.
    """

    log = log or _default_log
    path = conversation_db_path()
    if str(path) == ":memory:":
        log("clear", "conversation memory is in-memory only; nothing on disk to clear.")
        return "conversation memory is in-memory; nothing to clear"

    removed: list[str] = []
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            try:
                candidate.unlink()
                removed.append(candidate.name)
            except OSError as exc:
                raise RuntimeError(
                    f"could not delete {candidate} ({exc}). Is TALOS still running?"
                ) from exc

    if removed:
        log("clear", f"deleted conversation memory: {', '.join(removed)}")
        return f"cleared conversation memory ({', '.join(removed)})"
    log("clear", f"no conversation memory file found at {path}")
    return "no conversation memory file existed"


def clear_awareness_memory(
    log: Optional[LogFn] = None, ensure_db: bool = True
) -> str:
    """Truncate the awareness long-term memory tables.

    If ``ensure_db`` is set, brings up the Postgres container first (idempotent)
    so the clear works even when the awareness backend is not running. Delegates
    the actual truncate to :mod:`talos.launcher.awareness_clear`, run under the
    awareness virtualenv so it reuses the backend's own DB settings.
    """

    log = log or _default_log

    if ensure_db:
        log("clear", "ensuring awareness Postgres is up...")
        rc = _run(
            ["docker", "compose", "-f", config.DOCKER_COMPOSE_FILE, "up", "-d", "--wait"],
            log,
            "docker",
        )
        if rc != 0:
            raise RuntimeError(
                "awareness Postgres is not available (docker compose up failed)."
            )

    py = venv_python("awareness")
    log("clear", "truncating awareness memory tables...")
    rc = _run(
        [str(py), "-m", "talos.launcher.awareness_clear"],
        log,
        "clear",
        env=_awareness_env(py),
    )
    if rc != 0:
        raise RuntimeError("clearing awareness memory failed (see log above).")
    return "cleared awareness long-term memory"


def clear_all_memory(log: Optional[LogFn] = None, ensure_db: bool = True) -> str:
    """Clear both the awareness long-term memory and the conversation store."""

    log = log or _default_log
    conv = clear_conversation_memory(log)
    aware = clear_awareness_memory(log, ensure_db=ensure_db)
    return f"{aware}; {conv}"


def _awareness_env(python_path: Path) -> dict[str, str]:
    """Activate the awareness venv so its interpreter/deps resolve correctly."""

    env = os.environ.copy()
    bin_dir = python_path.parent
    if python_path.exists() and bin_dir.name in ("Scripts", "bin"):
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(bin_dir.parent)
        env.pop("PYTHONHOME", None)
    return env


def _run(args: list[str], log: LogFn, name: str, env: dict[str, str] | None = None) -> int:
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
        log(name, f"command not found: {args[0]}")
        return 127
    assert popen.stdout is not None
    for line in popen.stdout:
        log(name, line.rstrip("\n"))
    return popen.wait()
