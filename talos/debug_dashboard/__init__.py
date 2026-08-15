"""Local, read-only debug dashboard for TALOS."""

from .server import DebugSnapshotService, run_server

__all__ = ["DebugSnapshotService", "run_server"]
