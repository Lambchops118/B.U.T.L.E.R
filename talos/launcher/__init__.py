"""One-command startup for the whole TALOS stack.

TALOS normally runs as several cooperating processes in separate virtual
environments (main agent, voice worker, awareness backend) plus a Postgres
container and a local Ollama server. Starting them by hand means several
terminals. This package supervises all of them from a single entry point and
pins each GPU-bound process to the right card:

- the language model (Ollama) runs on the RTX 5080
- the speech-to-text model (faster-whisper in the voice worker) runs on the
  RTX 2060

Run it with either::

    python -m talos.launcher            # GUI (default)
    python -m talos.launcher --no-gui   # headless, streams logs to the console

or use the ``Start-Talos.ps1`` / ``talos.cmd`` bootstrap scripts at the repo
root, which pick the correct virtual environment automatically.
"""

from __future__ import annotations

__all__ = ["config", "core"]
