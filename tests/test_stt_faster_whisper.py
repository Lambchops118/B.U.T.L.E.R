from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.voice.backends.base import AudioChunk, TranscriptResult
from talos.voice.backends.stt_faster_whisper import FasterWhisperSTT


def _pcm(num_samples: int) -> bytes:
    # Non-zero 16-bit samples so the backend does not treat the clip as empty.
    return struct.pack("<" + "h" * num_samples, *([1000] * num_samples))


class FakeModel:
    def __init__(self, segments, language="en"):
        self._segments = segments
        self._language = language
        self.calls = []

    def transcribe(self, samples, **kwargs):
        self.calls.append(kwargs)
        info = SimpleNamespace(language=self._language)
        segments = [SimpleNamespace(text=t) for t in self._segments]
        return iter(segments), info


class FasterWhisperSTTTests(unittest.TestCase):
    def test_joins_segments_and_strips(self):
        model = FakeModel([" Butler ", " what time is it "])
        stt = FasterWhisperSTT(model=model)
        result = stt.transcribe(AudioChunk(pcm=_pcm(1600), sample_rate=16000))
        self.assertIsInstance(result, TranscriptResult)
        self.assertEqual(result.text, "Butler   what time is it")
        self.assertEqual(result.language, "en")

    def test_empty_audio_returns_empty_without_calling_model(self):
        model = FakeModel(["should not be used"])
        stt = FasterWhisperSTT(model=model)
        result = stt.transcribe(AudioChunk(pcm=b"", sample_rate=16000))
        self.assertTrue(result.is_empty)
        self.assertEqual(model.calls, [])

    def test_transcribe_uses_greedy_and_disables_context(self):
        model = FakeModel(["hi"])
        stt = FasterWhisperSTT(model=model, beam_size=1, language="en")
        stt.transcribe(AudioChunk(pcm=_pcm(800), sample_rate=16000))
        kwargs = model.calls[0]
        self.assertEqual(kwargs["beam_size"], 1)
        self.assertEqual(kwargs["language"], "en")
        self.assertFalse(kwargs["condition_on_previous_text"])

    def test_device_resolution_cpu_default(self):
        stt = FasterWhisperSTT(device="cpu")
        self.assertEqual(stt._resolve_device(), ("cpu", "int8"))

    def test_device_resolution_cuda_uses_int8_float16(self):
        stt = FasterWhisperSTT(device="cuda")
        self.assertEqual(stt._resolve_device(), ("cuda", "int8_float16"))

    def test_explicit_compute_type_respected(self):
        stt = FasterWhisperSTT(device="cuda", compute_type="float16")
        self.assertEqual(stt._resolve_device(), ("cuda", "float16"))


if __name__ == "__main__":
    unittest.main()
