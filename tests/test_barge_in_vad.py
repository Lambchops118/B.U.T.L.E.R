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

    def test_candidate_opens_every_utterance_exactly_once(self):
        """The gate is the sole endpointer, so the agent decides whether a turn
        is an interruption or an ordinary command from ``on_candidate``, which
        fires when the gate opens. That routing is only correct if every
        utterance is preceded by exactly one candidate."""
        probabilities = iter([0.9] * 3 + [0.0] * 3 + [0.9] * 3 + [0.0] * 3)
        events = []
        gate = BargeInVadGate(
            lambda _: next(probabilities),
            lambda pcm, evidence: events.append("utterance"),
            on_candidate=lambda probability: events.append("candidate"),
            config=VadGateConfig(
                start_frames=3,
                min_speech_ms=90,
                end_silence_ms=90,
                preroll_ms=96,
            ),
        )
        for _ in range(12):
            gate.observe(b"\x01\x00" * 512)
        self.assertEqual(
            events, ["candidate", "utterance", "candidate", "utterance"]
        )

    def test_max_utterance_ms_closes_a_turn_that_never_pauses(self):
        """Unbroken speech still has to terminate, or a turn endpointed only by
        the gate could be held open indefinitely."""
        utterances = []
        gate = BargeInVadGate(
            lambda _: 0.9,
            lambda pcm, evidence: utterances.append(evidence),
            config=VadGateConfig(
                start_frames=1,
                min_speech_ms=32,
                end_silence_ms=1000,
                preroll_ms=0,
                max_utterance_ms=160,
            ),
        )
        for _ in range(12):
            gate.observe(b"\x01\x00" * 512)
        self.assertTrue(utterances)
        self.assertLessEqual(utterances[0]["duration_ms"], 160 + 32)


if __name__ == "__main__":
    unittest.main()
