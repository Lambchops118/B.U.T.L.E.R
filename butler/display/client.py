from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlencode

from butler.text.service_client import get_json


class RemoteDisplayQueue:
    """Queue-shaped polling client used by the existing pygame render loop."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        poll_seconds: float | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv(
            "BUTLER_DISPLAY_AGENT_URL",
            os.getenv("BUTLER_TEXT_AGENT_URL", "http://127.0.0.1:8420"),
        )
        self.token = token if token is not None else os.getenv(
            "BUTLER_DISPLAY_AGENT_TOKEN",
            os.getenv("BUTLER_TEXT_AGENT_TOKEN", os.getenv("TEXT_AGENT_API_TOKEN", "")),
        )
        try:
            configured_poll = poll_seconds if poll_seconds is not None else float(
                os.getenv("BUTLER_DISPLAY_POLL_SECONDS", "0.5")
            )
        except (TypeError, ValueError):
            configured_poll = 0.5
        self.poll_seconds = max(0.1, configured_poll)
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.client_id = os.getenv("BUTLER_DISPLAY_CLIENT_ID", uuid.uuid4().hex[:12])[:100]
        self._consumed_sequence: int | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._poll,
            name="butler-display-poll",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))
        self._thread = None

    def get_nowait(self) -> tuple[str, dict[str, Any]]:
        item = self._queue.get_nowait()
        snapshot = item[1]
        sequence = snapshot.get("sequence") if isinstance(snapshot, dict) else None
        if isinstance(sequence, int):
            self._consumed_sequence = sequence
        return item

    def _state_path(self) -> str:
        if self._consumed_sequence is None:
            return "/display/state"
        query = urlencode(
            {"client_id": self.client_id, "sequence": self._consumed_sequence}
        )
        return f"/display/state?{query}"

    def _publish(self, snapshot: dict[str, Any]) -> None:
        item = ("DISPLAY_STATE", snapshot)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(item)

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                snapshot = get_json(
                    self.base_url,
                    self._state_path(),
                    token=self.token,
                    timeout=max(2.0, self.poll_seconds * 2),
                )
                # Sleep state can change without a content event, so publish
                # every successful poll even when the sequence is unchanged.
                self._publish(snapshot)
            except Exception as exc:
                self._publish({"connected": False, "error": str(exc)})
            self._stop.wait(self.poll_seconds)
