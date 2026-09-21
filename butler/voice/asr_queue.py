"""Bounded priority queue for room speech-recognition work."""

from __future__ import annotations

import itertools
import queue
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class AsrPriority(IntEnum):
    IDLE_COMMAND = 0
    BARGE_IN_CONFIRMATION = 1


@dataclass(order=True, frozen=True)
class AsrWorkItem:
    priority: int
    sequence: int
    kind: str = field(compare=False)
    payload: Any = field(compare=False)


class BoundedAsrQueue:
    """FIFO within priority, with idle commands ahead of queued confirmations."""

    def __init__(self, capacity: int = 8) -> None:
        if capacity <= 0:
            raise ValueError("ASR queue capacity must be positive")
        self._queue: "queue.PriorityQueue[AsrWorkItem]" = queue.PriorityQueue(
            maxsize=capacity
        )
        self._sequence = itertools.count()
        self._sequence_lock = threading.Lock()

    def put_nowait(
        self,
        *,
        kind: str,
        payload: Any,
        priority: AsrPriority,
    ) -> bool:
        with self._sequence_lock:
            sequence = next(self._sequence)
        try:
            self._queue.put_nowait(
                AsrWorkItem(int(priority), sequence, kind, payload)
            )
        except queue.Full:
            return False
        return True

    def get(self) -> AsrWorkItem:
        return self._queue.get()

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def capacity(self) -> int:
        return self._queue.maxsize
