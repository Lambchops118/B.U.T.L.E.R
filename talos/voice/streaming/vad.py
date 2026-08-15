"""Silero speech probability and hysteretic barge-in utterance endpointing."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np


def select_vad_lane(
    current_lane: str | None,
    *,
    barge_pending: bool,
    idle_pending: bool,
    speaking: bool,
    idle_enabled: bool,
) -> str | None:
    """Select one VAD lane without cutting off an utterance at a mode change."""
    if current_lane == "barge_in" and barge_pending:
        return "barge_in"
    if current_lane == "idle" and idle_pending:
        return "idle"
    if speaking:
        return "barge_in"
    if idle_enabled:
        return "idle"
    return None


class SileroProbabilityVAD:
    """Stateful 16 kHz Silero VAD v6 wrapper over faster-whisper's ONNX asset."""

    frame_samples = 512

    def __init__(self, model=None) -> None:
        if model is None:
            from faster_whisper.vad import get_vad_model

            model = get_vad_model()
        self._session = model.session
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._h = np.zeros((1, 1, 128), dtype=np.float32)
            self._c = np.zeros((1, 1, 128), dtype=np.float32)
            self._context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, pcm: bytes) -> float:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size != self.frame_samples:
            raise ValueError(f"Silero VAD requires {self.frame_samples} samples")
        with self._lock:
            input_audio = np.concatenate((self._context, samples[None, :]), axis=1)
            output, self._h, self._c = self._session.run(
                None,
                {"input": input_audio, "h": self._h, "c": self._c},
            )
            self._context = samples[-64:][None, :]
        return float(np.asarray(output).reshape(-1)[-1])


@dataclass(frozen=True)
class VadGateConfig:
    sample_rate: int = 16000
    start_probability: float = 0.65
    end_probability: float = 0.35
    start_frames: int = 3
    end_silence_ms: float = 480.0
    min_speech_ms: float = 160.0
    preroll_ms: float = 500.0
    max_utterance_ms: float = 9000.0


class BargeInVadGate:
    """Separate VAD evidence gate; ASR is invoked only after endpointing."""

    def __init__(
        self,
        probability_fn: Callable[[bytes], float],
        on_utterance: Callable[[bytes, dict[str, float]], None],
        *,
        config: VadGateConfig | None = None,
        on_candidate: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config or VadGateConfig()
        self._probability_fn = probability_fn
        self._on_utterance = on_utterance
        self._on_candidate = on_candidate
        self._lock = threading.Lock()
        self._bytes = bytearray()
        self._preroll: deque[bytes] = deque()
        self._preroll_ms = 0.0
        self._active = False
        self._capture: list[bytes] = []
        self._hot: list[bytes] = []
        self._hot_probabilities: list[float] = []
        self._silence_ms = 0.0
        self._speech_ms = 0.0
        self._total_ms = 0.0
        self._probabilities: list[float] = []

    def reset(self) -> None:
        with self._lock:
            self._bytes.clear()
            self._preroll.clear()
            self._preroll_ms = 0.0
            self._active = False
            self._capture.clear()
            self._hot.clear()
            self._hot_probabilities.clear()
            self._silence_ms = 0.0
            self._speech_ms = 0.0
            self._total_ms = 0.0
            self._probabilities.clear()

    def observe(self, frame: bytes) -> None:
        frame_bytes = self.config.sample_rate * 2 * self._frame_ms / 1000.0
        self._bytes.extend(frame)
        quantum = int(round(frame_bytes))
        while len(self._bytes) >= quantum:
            vad_frame = bytes(self._bytes[:quantum])
            del self._bytes[:quantum]
            self._observe_vad_frame(vad_frame)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def has_pending_speech(self) -> bool:
        """Whether switching gates now could discard an utterance onset."""
        with self._lock:
            return self._active or bool(self._hot)

    @property
    def _frame_ms(self) -> float:
        return SileroProbabilityVAD.frame_samples / self.config.sample_rate * 1000.0

    def _observe_vad_frame(self, frame: bytes) -> None:
        probability = max(0.0, min(1.0, float(self._probability_fn(frame))))
        completed = None
        evidence = None
        candidate_probability = None
        with self._lock:
            if not self._active:
                self._push_preroll(frame)
                if probability >= self.config.start_probability:
                    self._hot.append(frame)
                    self._hot_probabilities.append(probability)
                else:
                    self._hot.clear()
                    self._hot_probabilities.clear()
                if len(self._hot) < self.config.start_frames:
                    return
                self._active = True
                self._capture = list(self._preroll)
                # Triggering frames are already present in pre-roll and count as
                # speech; this avoids the former minimum-duration off-by-trigger.
                self._speech_ms = len(self._hot) * self._frame_ms
                self._total_ms = len(self._capture) * self._frame_ms
                self._probabilities = list(self._hot_probabilities)
                self._silence_ms = 0.0
                candidate_probability = max(self._hot_probabilities)
                self._hot.clear()
                self._hot_probabilities.clear()
            else:
                self._capture.append(frame)
                self._total_ms += self._frame_ms
                self._probabilities.append(probability)
                if probability >= self.config.end_probability:
                    self._speech_ms += self._frame_ms
                    self._silence_ms = 0.0
                else:
                    self._silence_ms += self._frame_ms
                ended = self._silence_ms >= self.config.end_silence_ms
                overlong = self._total_ms >= self.config.max_utterance_ms
                if ended or overlong:
                    enough = self._speech_ms >= self.config.min_speech_ms
                    if enough:
                        completed = b"".join(self._capture)
                        evidence = {
                            "speech_ms": round(self._speech_ms, 1),
                            "duration_ms": round(self._total_ms, 1),
                            "max_probability": round(max(self._probabilities), 6),
                            "average_probability": round(
                                sum(self._probabilities) / len(self._probabilities), 6
                            ),
                        }
                    max_preroll_frames = max(
                        1, int(self.config.preroll_ms // self._frame_ms) + 1
                    )
                    capture_tail = self._capture[-max_preroll_frames:]
                    self._active = False
                    self._capture = []
                    self._speech_ms = 0.0
                    self._total_ms = 0.0
                    self._silence_ms = 0.0
                    self._probabilities.clear()
                    # Seed the next turn only with the most recent tail.  Keeping
                    # the old pre-trigger ring here can duplicate the prior wake
                    # word when two commands arrive close together.
                    self._preroll.clear()
                    self._preroll_ms = 0.0
                    for captured_frame in capture_tail:
                        self._push_preroll(captured_frame)
        if candidate_probability is not None and self._on_candidate is not None:
            self._on_candidate(candidate_probability)
        if completed is not None and evidence is not None:
            self._on_utterance(completed, evidence)

    def _push_preroll(self, frame: bytes) -> None:
        self._preroll.append(frame)
        self._preroll_ms += self._frame_ms
        while self._preroll and self._preroll_ms > self.config.preroll_ms:
            self._preroll.popleft()
            self._preroll_ms -= self._frame_ms
