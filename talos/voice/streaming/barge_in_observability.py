"""Privacy-safe measurement and opt-in fixture capture for room barge-in.

The production default is deliberately inert: aggregate metrics contain no
transcripts or PCM, and the synchronized fixture recorder writes nothing unless
an operator explicitly enables it.  Audio file writes happen on a bounded
background queue so measurement cannot block microphone or speaker callbacks.
"""

from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import uuid
import wave
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


_REJECTION_REASONS = frozenset(
    {
        "asr_error",
        "echo",
        "empty_transcript",
        "insufficient_speech",
        "low_asr_quality",
        "no_speech",
        "asr_queue_overflow",
        "no_wake_word",
        "stale_session",
    }
)


@dataclass
class _Measurement:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    last: float | None = None

    def add(self, value: float) -> None:
        value = float(value)
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.last = value

    def snapshot(self) -> dict[str, float | int | None]:
        average = None if not self.count else round(self.total / self.count, 3)
        return {
            "count": self.count,
            "min": self.minimum,
            "max": self.maximum,
            "average": average,
            "last": self.last,
        }


class BargeInMetrics:
    """Thread-safe counters and bounded numeric summaries.

    The first-pass detector has neither AEC residual audio nor speech-probability
    VAD.  The capability flags make those absences explicit instead of
    mislabeling mixed microphone RMS or inventing a probability.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._measurements: dict[str, _Measurement] = {}
        self._asr_confidence_observed = False
        self._vad_probability_observed = False
        self._aec_residual_rms_observed = False

    def candidate_started(
        self,
        *,
        capture_rms: float,
        render_rms: float,
        threshold_rms: float,
        pause_latency_ms: float,
    ) -> None:
        with self._lock:
            self._counters["candidate_started"] += 1
            self._add_locked("candidate_capture_rms", capture_rms)
            self._add_locked("candidate_render_rms", render_rms)
            self._add_locked("trigger_threshold_rms", threshold_rms)
            self._add_locked("pause_latency_ms", pause_latency_ms)

    def observe_levels(self, *, capture_rms: float, render_rms: float) -> None:
        """Summarize aligned levels without retaining a waveform."""
        with self._lock:
            self._add_locked("mixed_capture_rms", capture_rms)
            self._add_locked("render_rms", render_rms)

    def vad_candidate_started(
        self,
        *,
        probability: float,
        pause_latency_ms: float = 0.0,
    ) -> None:
        with self._lock:
            self._counters["candidate_started"] += 1
            self._add_locked("vad_start_probability", probability)
            self._add_locked("pause_latency_ms", pause_latency_ms)
            self._vad_probability_observed = True

    def vad_capture_completed(
        self,
        *,
        duration_ms: float,
        speech_ms: float,
        average_probability: float,
        max_probability: float,
    ) -> None:
        with self._lock:
            self._add_locked("capture_duration_ms", duration_ms)
            self._add_locked("vad_speech_ms", speech_ms)
            self._add_locked("vad_average_probability", average_probability)
            self._add_locked("vad_max_probability", max_probability)
            self._vad_probability_observed = True

    def observe_aec_residual_rms(self, residual_rms: float) -> None:
        with self._lock:
            self._add_locked("aec_residual_rms", residual_rms)
            self._aec_residual_rms_observed = True

    def capture_completed(
        self,
        *,
        capture_duration_ms: float,
        heuristic_speech_ms: float,
    ) -> None:
        with self._lock:
            self._add_locked("capture_duration_ms", capture_duration_ms)
            # This is intentionally not named VAD speech: it is only energy
            # above the old heuristic's continuation threshold.
            self._add_locked("heuristic_speech_ms", heuristic_speech_ms)

    def candidate_rejected(
        self,
        reason: str,
        *,
        asr_latency_ms: float | None = None,
        asr_confidence: float | None = None,
    ) -> None:
        bounded_reason = reason if reason in _REJECTION_REASONS else "other"
        with self._lock:
            self._counters["candidate_rejected"] += 1
            self._counters[f"candidate_rejected_{bounded_reason}"] += 1
            if asr_latency_ms is not None:
                self._add_locked("asr_latency_ms", asr_latency_ms)
            if asr_confidence is not None:
                self._add_locked("asr_confidence", asr_confidence)
                self._asr_confidence_observed = True

    def accepted(
        self,
        *,
        asr_latency_ms: float,
        asr_confidence: float | None = None,
    ) -> None:
        with self._lock:
            self._counters["accepted"] += 1
            self._add_locked("asr_latency_ms", asr_latency_ms)
            if asr_confidence is not None:
                self._add_locked("asr_confidence", asr_confidence)
                self._asr_confidence_observed = True

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = {
                name: self._counters.get(name, 0)
                for name in ("candidate_started", "candidate_rejected", "accepted")
            }
            counters.update(
                {
                    name: value
                    for name, value in sorted(self._counters.items())
                    if name.startswith("candidate_rejected_")
                }
            )
            measurements = {
                name: measurement.snapshot()
                for name, measurement in sorted(self._measurements.items())
            }
            return {
                "counters": counters,
                "measurements": measurements,
                "capabilities": {
                    "vad_probability_available": self._vad_probability_observed,
                    "aec_residual_rms_available": self._aec_residual_rms_observed,
                    "asr_confidence_observed": self._asr_confidence_observed,
                },
            }

    def _add_locked(self, name: str, value: float) -> None:
        measurement = self._measurements.setdefault(name, _Measurement())
        measurement.add(value)


@dataclass(frozen=True)
class FixtureRecorderConfig:
    """Bounds for one explicit synchronized room-audio recording session."""

    enabled: bool = False
    directory: Path = Path("logs/barge_in_fixtures")
    sample_rate: int = 16000
    sample_width: int = 2
    channels: int = 1
    max_duration_seconds: float = 120.0
    max_pcm_bytes: int = 32 * 1024 * 1024
    max_sessions: int = 5
    queue_frames: int = 512

    def validate(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("fixture sample_rate must be positive")
        if self.sample_width <= 0:
            raise ValueError("fixture sample_width must be positive")
        if self.channels <= 0:
            raise ValueError("fixture channels must be positive")
        if self.max_duration_seconds <= 0:
            raise ValueError("fixture max_duration_seconds must be positive")
        if self.max_pcm_bytes <= 0:
            raise ValueError("fixture max_pcm_bytes must be positive")
        if self.max_sessions <= 0:
            raise ValueError("fixture max_sessions must be positive")
        if self.queue_frames <= 0:
            raise ValueError("fixture queue_frames must be positive")


@dataclass(frozen=True)
class _AudioRecord:
    stream: str
    timestamp_ns: int
    pcm: bytes


class SynchronizedFixtureRecorder:
    """Write synchronized render/capture fixtures after explicit opt-in.

    The WAV files contain each stream's samples in device order.  ``events.jsonl``
    maps every block to a monotonic timestamp plus its sample offset, allowing a
    test harness to align capture and render without inserting guessed silence.
    """

    _PREFIX = "barge-in-fixture-"

    def __init__(
        self,
        config: FixtureRecorderConfig,
        *,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self.config = config
        self._clock_ns = clock_ns
        self._queue: queue.Queue[_AudioRecord] = queue.Queue(
            maxsize=max(1, config.queue_frames)
        )
        self._lock = threading.Lock()
        self._accepting = False
        self._closed = False
        self._dropped_frames = 0
        self._dropped_pcm_bytes = 0
        self._written_pcm_bytes = 0
        self._limit_reason: str | None = None
        self._error: str | None = None
        self._started_ns: int | None = None
        self._ended_ns: int | None = None
        self.session_directory: Path | None = None
        self._thread: threading.Thread | None = None

        if not config.enabled:
            return

        config.validate()
        root = config.directory.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._prune_retention(root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        session_name = f"{self._PREFIX}{stamp}-{uuid.uuid4().hex[:8]}"
        self.session_directory = root / session_name
        self.session_directory.mkdir(parents=False, exist_ok=False)
        self._started_ns = self._clock_ns()
        self._accepting = True
        self._write_manifest(final=False)
        self._thread = threading.Thread(
            target=self._writer,
            name="talos-barge-in-fixture-writer",
            daemon=True,
        )
        self._thread.start()
        print(
            "WARNING: synchronized room-audio fixture recording is ENABLED. "
            f"PCM is being stored locally in {self.session_directory} "
            f"for at most {config.max_duration_seconds:g}s / "
            f"{config.max_pcm_bytes} PCM bytes; at most "
            f"{config.max_sessions} sessions are retained."
        )

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._accepting and not self._closed

    def record_capture(self, pcm: bytes) -> None:
        self._enqueue("capture", pcm)

    def record_render(self, pcm: bytes) -> None:
        self._enqueue("render", pcm)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self._accepting and not self._closed,
                "session_directory": (
                    str(self.session_directory) if self.session_directory else None
                ),
                "written_pcm_bytes": self._written_pcm_bytes,
                "dropped_frames": self._dropped_frames,
                "dropped_pcm_bytes": self._dropped_pcm_bytes,
                "limit_reason": self._limit_reason,
                "error": self._error,
            }

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._accepting = False
            self._closed = True
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def _enqueue(self, stream: str, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            if not self._accepting or self._closed:
                return
        record = _AudioRecord(stream=stream, timestamp_ns=self._clock_ns(), pcm=pcm)
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            with self._lock:
                self._dropped_frames += 1
                self._dropped_pcm_bytes += len(pcm)

    def _writer(self) -> None:
        assert self.session_directory is not None
        render_offsets = {"capture": 0, "render": 0}
        capture_path = self.session_directory / "capture.wav"
        render_path = self.session_directory / "render.wav"
        events_path = self.session_directory / "events.jsonl"
        try:
            with (
                wave.open(str(capture_path), "wb") as capture_wave,
                wave.open(str(render_path), "wb") as render_wave,
                events_path.open("w", encoding="utf-8") as events,
            ):
                for output in (capture_wave, render_wave):
                    output.setnchannels(self.config.channels)
                    output.setsampwidth(self.config.sample_width)
                    output.setframerate(self.config.sample_rate)

                while True:
                    with self._lock:
                        accepting = self._accepting
                    if not accepting and self._queue.empty():
                        break
                    try:
                        record = self._queue.get(timeout=0.1)
                    except queue.Empty:
                        continue

                    if self._would_exceed_limit(record):
                        with self._lock:
                            self._dropped_frames += 1
                            self._dropped_pcm_bytes += len(record.pcm)
                        self._drop_queued_records()
                        break

                    target = capture_wave if record.stream == "capture" else render_wave
                    sample_count = len(record.pcm) // (
                        self.config.sample_width * self.config.channels
                    )
                    sample_start = render_offsets[record.stream]
                    target.writeframesraw(record.pcm)
                    events.write(
                        json.dumps(
                            {
                                "stream": record.stream,
                                "elapsed_ns": record.timestamp_ns - (self._started_ns or 0),
                                "sample_start": sample_start,
                                "sample_count": sample_count,
                                "pcm_bytes": len(record.pcm),
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    render_offsets[record.stream] += sample_count
                    with self._lock:
                        self._written_pcm_bytes += len(record.pcm)
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"[:500]
                self._accepting = False
        finally:
            with self._lock:
                self._accepting = False
                self._ended_ns = self._clock_ns()
            self._write_manifest(final=True)

    def _would_exceed_limit(self, record: _AudioRecord) -> bool:
        with self._lock:
            elapsed = (record.timestamp_ns - (self._started_ns or 0)) / 1_000_000_000
            if elapsed > self.config.max_duration_seconds:
                self._limit_reason = "max_duration"
                self._accepting = False
                return True
            if self._written_pcm_bytes + len(record.pcm) > self.config.max_pcm_bytes:
                self._limit_reason = "max_pcm_bytes"
                self._accepting = False
                return True
        return False

    def _drop_queued_records(self) -> None:
        while True:
            try:
                record = self._queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._dropped_frames += 1
                self._dropped_pcm_bytes += len(record.pcm)

    def _write_manifest(self, *, final: bool) -> None:
        if self.session_directory is None:
            return
        snapshot = self.snapshot()
        payload = {
            "schema_version": 1,
            "operator_opt_in_required": True,
            "contains_room_audio": True,
            "sample_rate": self.config.sample_rate,
            "sample_width": self.config.sample_width,
            "channels": self.config.channels,
            "max_duration_seconds": self.config.max_duration_seconds,
            "max_pcm_bytes": self.config.max_pcm_bytes,
            "max_sessions": self.config.max_sessions,
            "started_monotonic_ns": self._started_ns,
            "ended_monotonic_ns": self._ended_ns if final else None,
            **snapshot,
        }
        manifest = self.session_directory / "manifest.json"
        temporary = self.session_directory / "manifest.json.tmp"
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(manifest)

    def _prune_retention(self, root: Path) -> None:
        sessions = sorted(
            (
                path
                for path in root.iterdir()
                if path.is_dir()
                and path.name.startswith(self._PREFIX)
                and (path / "manifest.json").is_file()
            ),
            key=lambda path: path.name,
        )
        remove_count = max(0, len(sessions) - self.config.max_sessions + 1)
        for path in sessions[:remove_count]:
            shutil.rmtree(path)
