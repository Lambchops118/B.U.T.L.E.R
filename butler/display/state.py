from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any


class DisplayState:
    """Thread-safe latest-value projection consumed by remote displays.

    ``put`` intentionally accepts the legacy GUI tuple contract so router and
    scheduler producers remain simple while transport changes from an in-memory
    queue to an HTTP snapshot.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._updated_at = time.time()
        self._last_command = "Waiting for command..."
        self._last_response = "Of course sir."
        self._statuses: dict[str, Any] = {}
        self._clients: dict[str, dict[str, Any]] = {}

    def put(self, message: object) -> None:
        if not isinstance(message, (tuple, list)) or not message:
            return
        kind = str(message[0])
        with self._lock:
            if kind == "VOICE_CMD" and len(message) >= 3:
                self._last_command = str(message[1])
                self._last_response = str(message[2])
            elif kind == "STATUS" and len(message) >= 2 and isinstance(message[1], dict):
                self._statuses.update(message[1])
            else:
                return
            self._sequence += 1
            self._updated_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 2,
                "sequence": self._sequence,
                "updated_at": self._updated_at,
                "last_command": self._last_command,
                "last_response": self._last_response,
                "statuses": deepcopy(self._statuses),
                "clients": deepcopy(self._clients),
            }

    def record_client_heartbeat(
        self,
        client_id: str,
        *,
        sequence: int | None,
        status: str = "rendered",
        error: str | None = None,
    ) -> None:
        """Record bounded proof that a remote panel consumed a snapshot."""
        normalized = str(client_id or "").strip()[:100]
        if not normalized:
            raise ValueError("client_id is required")
        with self._lock:
            self._clients[normalized] = {
                "client_id": normalized,
                "last_seen_at": time.time(),
                "sequence": int(sequence) if sequence is not None else None,
                "status": str(status or "unknown")[:40],
                "error": str(error)[:300] if error else None,
            }
            # Bound cardinality if a machine is repeatedly reinstalled with a
            # new client identity.
            if len(self._clients) > 20:
                oldest = min(
                    self._clients,
                    key=lambda key: self._clients[key].get("last_seen_at", 0),
                )
                self._clients.pop(oldest, None)
