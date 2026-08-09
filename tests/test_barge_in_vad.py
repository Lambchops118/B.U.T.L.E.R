import struct
import unittest

from talos.voice.streaming.vad import (
    BargeInVadGate,
    VadGateConfig,
    select_vad_lane,
)


class BargeInVadGateTests(unittest.TestCase):
    def test_vad_lane_preserves_speech_across_playback_boundary(self):
        self.assertEqual(
            select_vad_lane(
                "barge_in",
                barge_pending=True,
                idle_pending=False,
                speaking=False,
                idle_enabled=False,
            ),
            "barge_in",
        )
        self.assertEqual(
            select_vad_lane(
                "idle",
                barge_pending=False,
                idle_pending=True,
                speaking=True,
                idle_enabled=True,
            ),
            "idle",
        )

    def test_vad_lane_uses_independent_idle_rollout_state(self):
        self.assertIsNone(
            select_vad_lane(
                None,
                barge_pending=False,
                idle_pending=False,
                speaking=False,
                idle_enabled=False,
            )
        )
        self.assertEqual(
            select_vad_lane(
                None,
                barge_pending=False,
                idle_pending=False,
                speaking=False,
                idle_enabled=True,
            ),
            "idle",
        )

    def test_default_preroll_preserves_sr_equivalent_wake_leadin(self):
        emitted = []

        def probability(frame):
            index = struct.unpack_from("<h", frame)[0]
            return 0.9 if 20 <= index < 28 else 0.0

        gate = BargeInVadGate(
            probability,
            lambda pcm, evidence: emitted.append(pcm),
            config=VadGateConfig(
                start_frames=3,
                min_speech_ms=160,
                end_silence_ms=96,
            ),
        )
        for index in range(35):
            gate.observe(struct.pack("<h", index) * 512)

        first_retained = struct.unpack_from("<h", emitted[0])[0]
        usable_leadin_ms = (20 - first_retained) * 32
        self.assertEqual(usable_leadin_ms, 384)

    def test_pending_speech_is_visible_before_and_after_gate_opens(self):
        probabilities = iter([0.9, 0.9, 0.9])
        gate = BargeInVadGate(
            lambda _: next(probabilities),
            lambda pcm, evidence: None,
            config=VadGateConfig(start_frames=3),
        )
        self.assertFalse(gate.has_pending_speech)
        gate.observe(b"\x01\x00" * 512)
        self.assertTrue(gate.has_pending_speech)
        self.assertFalse(gate.active)
        gate.observe(b"\x01\x00" * 512)
        gate.observe(b"\x01\x00" * 512)
        self.assertTrue(gate.active)

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
