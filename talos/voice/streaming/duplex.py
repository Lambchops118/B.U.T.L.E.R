"""Bounded full-duplex transport for the room voice worker."""

from __future__ import annotations

import queue
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Protocol

import speech_recognition as sr

from talos.voice.streaming.windows_audio import (
    WindowsAecCapture,
    WindowsAudioEndpoints,
)


class DuplexAudioProcessor(Protocol):
    """Small capture-side contract selected by the voice worker."""

    def start(self, on_frame: Callable[[bytes], None]) -> None: ...

    def stop(self, timeout: float = 5.0) -> None: ...

    def snapshot(self) -> dict[str, object]: ...


@dataclass
class BufferCounters:
    enqueued: int = 0
    dequeued: int = 0
    dropped: int = 0
    resets: int = 0


class BoundedFrameQueue:
    """Drop-oldest queue: audio callbacks never block behind consumers."""

    def __init__(self, max_frames: int) -> None:
        if max_frames <= 0:
            raise ValueError("max_frames must be positive")
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max_frames)
        self._lock = threading.Lock()
        self.counters = BufferCounters()

    def put_nowait(self, frame: bytes) -> None:
        try:
            self._queue.put_nowait(bytes(frame))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            with self._lock:
                self.counters.dropped += 1
            self._queue.put_nowait(bytes(frame))
        with self._lock:
            self.counters.enqueued += 1

    def get(self, timeout: float | None = None) -> bytes:
        frame = self._queue.get(timeout=timeout)
        with self._lock:
            self.counters.dequeued += 1
        return frame

    def reset(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self.counters.resets += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "enqueued": self.counters.enqueued,
                "dequeued": self.counters.dequeued,
                "dropped": self.counters.dropped,
                "resets": self.counters.resets,
                "depth": self._queue.qsize(),
                "capacity": self._queue.maxsize,
            }


class DuplexAudioPipeline:
    """Continuously drains clean capture and fans it out off the callback."""

    def __init__(
        self,
        processor: DuplexAudioProcessor,
        *,
        sample_rate: int = 16000,
        sample_width: int = 2,
        capture_queue_frames: int = 256,
        recognizer_queue_frames: int = 512,
        render_ring_frames: int = 500,
        clean_ring_frames: int = 500,
        on_clean_frame: Callable[[bytes], None] | None = None,
    ) -> None:
        self.processor = processor
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self._capture = BoundedFrameQueue(capture_queue_frames)
        self._recognizer = BoundedFrameQueue(recognizer_queue_frames)
        self._render = deque(maxlen=render_ring_frames)
        self._clean = deque(maxlen=clean_ring_frames)
        self._ring_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._on_clean_frame = on_clean_frame
        self._speaking = False
        self._running = False
        self._stop = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._render_overflows = 0
        self._clean_overflows = 0

    def start(self) -> None:
        with self._state_lock:
            if self._running:
                return
            self._running = True
            self._stop.clear()
        self._dispatcher = threading.Thread(
            target=self._dispatch,
            name="talos-duplex-dispatch",
            daemon=True,
        )
        self._dispatcher.start()
        try:
            self.processor.start(self._capture.put_nowait)
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        self._stop.set()
        self.processor.stop()
        dispatcher = self._dispatcher
        if dispatcher is not None:
            dispatcher.join(timeout=2.0)
        with self._state_lock:
            self._running = False
            self._speaking = False
        self._dispatcher = None

    def note_render_submitted(self, frame: bytes) -> bool:
        """Record output and atomically enter speaking state on first frame."""
        with self._ring_lock:
            if len(self._render) == self._render.maxlen:
                self._render_overflows += 1
            self._render.append(bytes(frame))
        with self._state_lock:
            first = not self._speaking
            self._speaking = True
        return first

    def finish_speaking(self) -> None:
        with self._state_lock:
            self._speaking = False

    @property
    def speaking(self) -> bool:
        with self._state_lock:
            return self._speaking

    @property
    def healthy(self) -> bool:
        with self._state_lock:
            running = self._running
        if not running:
            return False
        snapshot = self.processor.snapshot()
        return bool(snapshot.get("running")) and not snapshot.get("error")

    def read_for_recognizer(self, size: int) -> bytes:
        """Return exactly one recognizer read, consuming capture continuously."""
        if size <= 0:
            return b""
        parts: list[bytes] = []
        total = 0
        while total < size and not self._stop.is_set():
            try:
                frame = self._recognizer.get(timeout=0.2)
            except queue.Empty:
                continue
            parts.append(frame)
            total += len(frame)
        pcm = b"".join(parts)
        if len(pcm) > size:
            # AudioSource normally asks for the native CHUNK size (one quantum);
            # keep behavior deterministic if a caller requests a smaller read.
            pcm = pcm[:size]
        if self.speaking:
            return b"\x00" * len(pcm)
        return pcm

    def reset_utterance_buffers(self) -> None:
        self._recognizer.reset()

    def snapshot(self) -> dict[str, object]:
        with self._ring_lock:
            rings = {
                "render_depth": len(self._render),
                "render_capacity": self._render.maxlen,
                "render_overflows": self._render_overflows,
                "clean_depth": len(self._clean),
                "clean_capacity": self._clean.maxlen,
                "clean_overflows": self._clean_overflows,
            }
        return {
            "running": self._running,
            "speaking": self.speaking,
            "capture_queue": self._capture.snapshot(),
            "recognizer_queue": self._recognizer.snapshot(),
            "rings": rings,
            "processor": self.processor.snapshot(),
        }

    def _dispatch(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._capture.get(timeout=0.2)
            except queue.Empty:
                processor = self.processor.snapshot()
                if processor.get("error") or not processor.get("running", True):
                    with self._state_lock:
                        self._running = False
                        self._speaking = False
                    self._stop.set()
                continue
            with self._ring_lock:
                if len(self._clean) == self._clean.maxlen:
                    self._clean_overflows += 1
                self._clean.append(frame)
            self._recognizer.put_nowait(frame)
            handler = self._on_clean_frame
            if handler is not None:
                try:
                    handler(frame)
                except Exception:
                    # Runtime reports candidate-stage failures separately; a
                    # consumer must never kill continuous capture.
                    continue


class DuplexRecognizerAudioSource(sr.AudioSource):
    """SpeechRecognition AudioSource backed by the clean duplex queue."""

    def __init__(self, pipeline: DuplexAudioPipeline, chunk_samples: int = 160) -> None:
        self.pipeline = pipeline
        self.SAMPLE_RATE = pipeline.sample_rate
        self.SAMPLE_WIDTH = pipeline.sample_width
        self.CHUNK = chunk_samples
        self.stream = None

    def __enter__(self):
        if self.stream is not None:
            raise RuntimeError("Audio source is already entered")
        self.stream = _DuplexRecognizerStream(
            self.pipeline,
            self.CHUNK * self.SAMPLE_WIDTH,
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stream = None


class _DuplexRecognizerStream:
    def __init__(self, pipeline: DuplexAudioPipeline, native_bytes: int) -> None:
        self._pipeline = pipeline
        self._native_bytes = native_bytes

    def read(self, size: int) -> bytes:
        requested_bytes = (
            size * self._pipeline.sample_width if size else self._native_bytes
        )
        return self._pipeline.read_for_recognizer(requested_bytes)

    def close(self) -> None:
        return None


def build_windows_duplex_pipeline(
    endpoints: WindowsAudioEndpoints,
    **kwargs,
) -> DuplexAudioPipeline:
    return DuplexAudioPipeline(WindowsAecCapture(endpoints), **kwargs)
