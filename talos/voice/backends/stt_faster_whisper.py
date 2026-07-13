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
        model: Any | None = None,
    ) -> None:
        self.model_size = model_size
        self.language = (language or "").strip() or None
        self.beam_size = max(1, int(beam_size))
        self.vad_filter = vad_filter
        self._device = device
        self._compute_type = compute_type
        self._model = model
        self._lock = threading.Lock()

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

    def transcribe(self, audio: AudioChunk) -> TranscriptResult:
        import numpy as np

        model = self._ensure_model()
        samples = np.frombuffer(audio.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return TranscriptResult(text="")

        segments, info = model.transcribe(
            samples,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text for segment in segments).strip()
        return TranscriptResult(
            text=text,
            language=getattr(info, "language", None),
            raw=info,
        )


def _cuda_available() -> bool:
    """Detect CUDA without importing torch (faster-whisper pulls in ctranslate2)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
