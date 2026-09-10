"""Local, read-only debug dashboard for Butler."""

from .server import DebugSnapshotService, run_server

__all__ = ["DebugSnapshotService", "run_server"]
