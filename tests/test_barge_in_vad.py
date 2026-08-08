import unittest

from talos.voice.streaming.vad import BargeInVadGate, VadGateConfig


class BargeInVadGateTests(unittest.TestCase):
    def test_trigger_frames_count_toward_minimum_speech(self):
        probabilities = iter([0.9, 0.9, 0.9] + [0.0] * 3)
        utterances = []
        candidates = []
        gate = BargeInVadGate(
            lambda _: next(probabilities),
            lambda pcm, evidence: utterances.append((pcm, evidence)),
            on_candidate=candidates.append,
            config=VadGateConfig(
                start_frames=3,
                min_speech_ms=90,
                end_silence_ms=90,
                preroll_ms=96,
            ),
        )
        for _ in range(6):
            gate.observe(b"\x01\x00" * 512)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(utterances), 1)
        self.assertGreaterEqual(utterances[0][1]["speech_ms"], 96)

    def test_short_transient_never_reaches_asr(self):
        probabilities = iter([0.9, 0.1, 0.1, 0.1])
        utterances = []
        gate = BargeInVadGate(
            lambda _: next(probabilities),
            lambda pcm, evidence: utterances.append((pcm, evidence)),
            config=VadGateConfig(start_frames=2, end_silence_ms=64),
        )
        for _ in range(4):
            gate.observe(b"\x01\x00" * 512)
        self.assertEqual(utterances, [])

    def test_far_end_residual_below_probability_gate_never_candidates(self):
        utterances = []
        candidates = []
        gate = BargeInVadGate(
            lambda _: 0.08,
            lambda pcm, evidence: utterances.append((pcm, evidence)),
            on_candidate=candidates.append,
        )
        for _ in range(100):
            gate.observe(b"\x40\x00" * 512)
        self.assertEqual(candidates, [])
        self.assertEqual(utterances, [])


if __name__ == "__main__":
    unittest.main()
