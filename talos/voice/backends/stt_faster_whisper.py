"""Local speech-to-text via faster-whisper (CTranslate2).

Runs the *same code* on both target environments, selected by device:

- macOS dev:  ``device="cpu"``  (CTranslate2 has no Metal backend)
- 2060 deploy: ``device="cuda"``, ``compute_type="int8_float16"``

This replaces the cloud ``whisper-1`` round-trip (~1.6s) with a local pass
(~0.2-0.8s) and, in the voice worker, lets a single transcription serve both
wake-word detection and the command (removing the redundant second pass).

The model is loaded lazily on first use so importing this module is cheap and
free of heavy dependencies. ``WhisperModel`` can be injected for tests.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from talos.voice.backends.base import AudioChunk, STTBackend, TranscriptResult


class FasterWhisperSTT(STTBackend):
    def __init__(
        self,
        *,
        model_size: str = "distil-large-v3",
        device: str | None = None,
        compute_type: str | None = None,
        language: str | None = "en",
        beam_size: int = 1,
        vad_filter: bool = False,
        vad_parameters: dict[str, Any] | None = None,
        model: Any | None = None,
    ) -> None:
        self.model_size = model_size
        self.language = (language or "").strip() or None
        self.beam_size = max(1, int(beam_size))
        self.vad_filter = vad_filter
        self.vad_parameters = dict(vad_parameters or {})
        self._device = device
        self._compute_type = compute_type
        self._model = model
        self._lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self.last_model_preloaded: bool | None = None
        self.last_model_load_ms: float | None = None

    def _resolve_device(self) -> tuple[str, str]:
        device = self._device or ("cuda" if _cuda_available() else "cpu")
        compute = self._compute_type or ("int8_float16" if device == "cuda" else "int8")
        return device, compute

    def _ensure_model(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel

                    device, compute = self._resolve_device()
                    print(
                        f"Loading local STT model '{self.model_size}' "
                        f"(device={device}, compute={compute})..."
                    )
                    self._model = WhisperModel(
                        self.model_size, device=device, compute_type=compute
                    )
        return self._model

    def preload(self) -> float:
        """Load model weights ahead of the first utterance and return load ms."""
        self._model_with_metrics()
        return float(self.last_model_load_ms or 0.0)

    def _model_with_metrics(self) -> Any:
        model_preloaded = self._model is not None
        model_load_started = time.perf_counter()
        model = self._ensure_model()
        self.last_model_preloaded = model_preloaded
        self.last_model_load_ms = (
            0.0
            if model_preloaded
            else round((time.perf_counter() - model_load_started) * 1000.0, 1)
        )
        return model

    def transcribe(self, audio: AudioChunk) -> TranscriptResult:
        return self._transcribe(audio, vad_filter=self.vad_filter)

    def transcribe_barge_in(self, audio: AudioChunk) -> TranscriptResult:
        """Use Silero prefiltering for the independently VAD-gated room burst."""
        return self._transcribe(audio, vad_filter=True)

    def _transcribe(
        self,
        audio: AudioChunk,
        *,
        vad_filter: bool,
    ) -> TranscriptResult:
        import numpy as np

        model = self._model_with_metrics()
        samples = np.frombuffer(audio.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return TranscriptResult(text="")

        with self._transcribe_lock:
            segments, info = model.transcribe(
                samples,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=vad_filter,
                vad_parameters=self.vad_parameters or {
                    "threshold": 0.5,
                    "min_speech_duration_ms": 120,
                    "min_silence_duration_ms": 250,
                    "speech_pad_ms": 120,
                },
                condition_on_previous_text=False,
            )
            materialized = list(segments)
        text = " ".join(segment.text for segment in materialized).strip()
        durations = [
            max(0.0, float(segment.end) - float(segment.start))
            for segment in materialized
            if getattr(segment, "start", None) is not None
            and getattr(segment, "end", None) is not None
        ]
        log_probabilities = [
            float(segment.avg_logprob)
            for segment in materialized
            if getattr(segment, "avg_logprob", None) is not None
        ]
        no_speech_probabilities = [
            float(segment.no_speech_prob)
            for segment in materialized
            if getattr(segment, "no_speech_prob", None) is not None
        ]
        return TranscriptResult(
            text=text,
            language=getattr(info, "language", None),
            duration_seconds=sum(durations) if durations else audio.duration_seconds,
            average_log_probability=(
                sum(log_probabilities) / len(log_probabilities)
                if log_probabilities
                else None
            ),
            no_speech_probability=(
                max(no_speech_probabilities) if no_speech_probabilities else None
            ),
            raw=info,
        )


def _cuda_available() -> bool:
    """Detect CUDA without importing torch (faster-whisper pulls in ctranslate2)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
