from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.voice.streaming.barge_in import (
    NOTHING_HEARD_MARKER,
    BargeInConfig,
    BargeInDetector,
    SpeechSession,
    classify_barge_in,
    is_stop_command,
    looks_like_echo,
    normalize_phrase,
    strip_wake_word,
    truncated_transcript,
)


FRAME_SAMPLES = 1024  # what SpeechRecognition reads per call: 64 ms at 16 kHz


def tone(amplitude: int, samples: int = FRAME_SAMPLES) -> bytes:
    """A frame with the requested RMS-ish amplitude (square wave keeps it exact)."""
    values = [amplitude if (i // 8) % 2 == 0 else -amplitude for i in range(samples)]
    return struct.pack(f"<{samples}h", *values)


SILENCE = tone(0)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SpeechSessionTests(unittest.TestCase):
    def test_gain_follows_duck_state(self):
        session = SpeechSession(duck_gain=0.1)
        self.assertEqual(session.gain, 1.0)
        session.duck()
        self.assertEqual(session.gain, 0.1)
        self.assertTrue(session.is_ducked)
        session.unduck()
        self.assertEqual(session.gain, 1.0)

    def test_spoken_text_accumulates_only_what_played(self):
        session = SpeechSession()
        session.note_playing("The pump is running.")
        session.note_playing("Channel two is idle.")
        self.assertEqual(
            session.spoken_text, "The pump is running. Channel two is idle."
        )

    def test_partial_chunk_never_claims_unheard_words(self):
        session = SpeechSession()
        session.note_playing("The first sentence finished.")
        session.note_partial("Future words must not appear.")
        self.assertIn("The first sentence finished.", session.spoken_text)
        self.assertIn("partially heard", session.spoken_text)
        self.assertNotIn("Future words", session.spoken_text)

    def test_cancel_keeps_the_first_reason(self):
        session = SpeechSession()
        self.assertFalse(session.is_cancelled)
        session.cancel("barge_in")
        session.cancel("shutdown")
        self.assertTrue(session.is_cancelled)
        self.assertEqual(session.cancel_reason, "barge_in")


class BargeInDetectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.config = BargeInConfig(
            floor_rms=500,
            echo_margin=2.0,
            trigger_frames=3,
            endpoint_silence_ms=200.0,
            min_speech_ms=120.0,
            preroll_ms=128.0,
            refractory_ms=0.0,
        )
        self.detector = BargeInDetector(self.config, clock=self.clock)
        self.session = SpeechSession(duck_gain=0.1)

    def feed(self, frame: bytes, *, output: bytes | None = None):
        """Advance one frame, optionally having written `output` to the speakers.

        The output is timestamped so that it lands inside the alignment window
        of the microphone frame fed after it, mirroring the real delay between
        writing PCM and hearing it in the room.
        """
        if output is not None:
            self.clock.advance(-self.config.output_delay_ms / 1000.0)
            self.detector.observe_output(output)
            self.clock.advance(self.config.output_delay_ms / 1000.0)
        captured = self.detector.observe_input(frame)
        self.clock.advance(FRAME_SAMPLES / 16000.0)
        return captured

    def test_idle_detector_ignores_input(self):
        self.assertIsNone(self.detector.observe_input(tone(9000)))
        self.assertFalse(self.detector.armed)

    def test_echo_at_the_learned_level_does_not_trigger(self):
        self.detector.arm(self.session)
        for _ in range(40):
            # Our own voice, heard back at a steady level.
            self.assertIsNone(self.feed(tone(3000), output=tone(9000)))
        self.assertFalse(self.session.is_ducked)

    def test_speech_over_the_echo_ducks_and_captures(self):
        self.detector.arm(self.session)
        for _ in range(20):
            self.feed(tone(3000), output=tone(9000))

        # The user starts talking, well above the learned echo level.
        for _ in range(self.config.trigger_frames):
            self.assertIsNone(self.feed(tone(12000), output=tone(9000)))
        self.assertTrue(self.session.is_ducked)
        candidate_metrics = self.detector.metrics_snapshot()
        self.assertEqual(candidate_metrics["counters"]["candidate_started"], 1)
        self.assertAlmostEqual(
            candidate_metrics["measurements"]["pause_latency_ms"]["last"],
            self.config.trigger_frames * 64.0,
        )
        self.assertGreater(
            candidate_metrics["measurements"]["candidate_capture_rms"]["last"],
            candidate_metrics["measurements"]["trigger_threshold_rms"]["last"],
        )
        self.assertGreater(
            candidate_metrics["measurements"]["render_rms"]["count"],
            self.config.trigger_frames,
        )

        # Ducking drops the echo, so the room goes quiet once they stop.
        for _ in range(3):
            self.feed(tone(12000), output=tone(900))
        captured = None
        for _ in range(6):
            captured = self.feed(SILENCE, output=tone(900))
            if captured:
                break
        self.assertIsNotNone(captured)
        # Pre-roll means the capture is longer than the frames after the trigger.
        self.assertGreater(len(captured), FRAME_SAMPLES * 2 * 6)

    def test_capture_includes_preroll_from_before_the_trigger(self):
        self.detector.arm(self.session)
        for _ in range(10):
            self.feed(tone(3000), output=tone(9000))
        for _ in range(self.config.trigger_frames):
            self.feed(tone(12000), output=tone(9000))
        for _ in range(3):
            self.feed(tone(12000), output=tone(900))
        captured = None
        for _ in range(10):
            captured = self.feed(SILENCE, output=tone(900))
            if captured:
                break
        self.assertIsNotNone(captured)
        # The trigger costs three frames; without pre-roll the capture would
        # start after them and clip the start of the word.
        frames_after_trigger = 3 + int(self.config.endpoint_silence_ms / 64.0) + 1
        self.assertGreater(len(captured), FRAME_SAMPLES * 2 * frames_after_trigger)

    def test_short_transient_unducks_without_reporting_a_capture(self):
        self.detector.arm(self.session)
        for _ in range(20):
            self.feed(tone(3000), output=tone(9000))
        # A bang: loud enough to trip the gate, too short to be speech.
        for _ in range(self.config.trigger_frames):
            self.feed(tone(14000), output=tone(9000))
        self.assertTrue(self.session.is_ducked)
        captured = None
        for _ in range(8):
            captured = self.feed(SILENCE, output=tone(900))
            if captured:
                break
        self.assertIsNone(captured)
        self.assertFalse(self.session.is_ducked)
        snapshot = self.detector.metrics_snapshot()
        self.assertEqual(snapshot["counters"]["candidate_started"], 1)
        self.assertEqual(snapshot["counters"]["candidate_rejected"], 1)
        self.assertEqual(
            snapshot["counters"]["candidate_rejected_insufficient_speech"], 1
        )
        self.assertIn("capture_duration_ms", snapshot["measurements"])
        self.assertFalse(
            snapshot["capabilities"]["vad_probability_available"]
        )

    def test_quiet_speech_during_an_output_gap_still_triggers(self):
        """Between sentences we make no sound, so the bar drops to ambient.

        The alignment window is 240 ms wide and takes the loudest thing in it, so
        the bar only drops once the last of our audio has aged out of it.
        """
        self.detector.arm(self.session)
        for _ in range(20):
            self.feed(tone(3000), output=tone(9000))
        # Speech far below the echo level, which would never trigger while we
        # were still making noise.
        for _ in range(8):
            self.feed(tone(1500), output=SILENCE)
        self.assertTrue(self.session.is_ducked)

    def test_disarm_with_a_stale_session_leaves_the_current_one_armed(self):
        self.detector.arm(self.session)
        replacement = SpeechSession()
        self.detector.arm(replacement)
        self.detector.disarm(self.session)
        self.assertTrue(self.detector.armed)
        self.detector.disarm(replacement)
        self.assertFalse(self.detector.armed)

    def test_recognizer_is_muted_only_while_armed(self):
        self.assertFalse(self.detector.should_mute_recognizer())
        self.detector.arm(self.session)
        self.assertTrue(self.detector.should_mute_recognizer())
        self.detector.disarm()
        self.assertFalse(self.detector.should_mute_recognizer())

    def test_refractory_period_blocks_an_immediate_retrigger(self):
        detector = BargeInDetector(
            BargeInConfig(
                floor_rms=500,
                echo_margin=2.0,
                trigger_frames=1,
                refractory_ms=500.0,
            ),
            clock=self.clock,
        )
        detector.arm(self.session)
        detector.resume_after_rejection()
        for _ in range(3):
            detector.observe_input(tone(14000))
        self.assertFalse(self.session.is_ducked)
        self.clock.advance(1.0)
        detector.observe_input(tone(14000))
        self.assertTrue(self.session.is_ducked)


class EchoTextTests(unittest.TestCase):
    def test_verbatim_repeat_of_our_own_words_is_echo(self):
        self.assertTrue(
            looks_like_echo(
                "the pump is running normally",
                "I checked the tank. The pump is running normally, and the level is fine.",
            )
        )

    def test_a_real_interruption_is_not_echo(self):
        self.assertFalse(
            looks_like_echo(
                "no I meant the bedroom",
                "The kitchen light is on and the thermostat is set to sixty eight.",
            )
        )

    def test_single_token_is_left_to_the_caller(self):
        self.assertFalse(looks_like_echo("stop", "please stop the pump now"))

    def test_empty_inputs_are_not_echo(self):
        self.assertFalse(looks_like_echo("", "something"))
        self.assertFalse(looks_like_echo("something", ""))


class StopCommandTests(unittest.TestCase):
    def test_bare_stop_phrases(self):
        for phrase in ["stop", "Stop.", "shut up", "never mind", "That's enough!"]:
            self.assertTrue(is_stop_command(phrase), phrase)

    def test_a_stop_with_a_follow_up_is_a_real_command(self):
        self.assertFalse(is_stop_command("stop and tell me about the pump instead"))

    def test_normalize_strips_punctuation_and_case(self):
        self.assertEqual(normalize_phrase("  Stop, please!  "), "stop please")


class ClassifyBargeInTests(unittest.TestCase):
    """Stage 2: what a burst captured over our own voice is allowed to do."""

    SPOKEN = "The kitchen light is on and the thermostat is set to sixty eight."

    def classify(self, transcript, spoken=None, **kwargs):
        return classify_barge_in(
            transcript,
            self.SPOKEN if spoken is None else spoken,
            wake_word="butler",
            **kwargs,
        )

    def test_a_correction_interrupts_and_becomes_the_next_command(self):
        decision = self.classify("no I meant the bedroom")
        self.assertTrue(decision.accepted)
        self.assertFalse(decision.is_stop_request)
        self.assertEqual(decision.command, "no i meant the bedroom")

    def test_wake_word_is_stripped_from_the_command(self):
        decision = self.classify("Butler, check the garage instead")
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.command, "check the garage instead")

    def test_stop_asks_for_silence_not_for_an_answer(self):
        decision = self.classify("stop")
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.is_stop_request)
        self.assertEqual(decision.command, "")

    def test_our_own_voice_coming_back_is_rejected(self):
        decision = self.classify("the thermostat is set to sixty eight")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "echo")

    def test_silence_is_rejected(self):
        decision = self.classify("   ")
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "empty_transcript")

    def test_strict_mode_needs_the_wake_word(self):
        decision = self.classify("no I meant the bedroom", require_wake_word=True)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "no_wake_word")

    def test_strict_mode_still_honours_a_bare_stop(self):
        decision = self.classify("stop", require_wake_word=True)
        self.assertTrue(decision.accepted)
        self.assertTrue(decision.is_stop_request)

    def test_strict_mode_accepts_the_wake_word_anywhere(self):
        decision = self.classify("wait butler check the garage", require_wake_word=True)
        self.assertTrue(decision.accepted)

    def test_echo_check_runs_before_the_wake_word_check(self):
        """We say our own name; hearing it back must not count as being called."""
        decision = self.classify(
            "butler here is the kitchen status",
            spoken="Butler here is the kitchen status you asked for.",
            require_wake_word=True,
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "echo")


class StripWakeWordTests(unittest.TestCase):
    def test_leading_wake_word_and_punctuation_go(self):
        self.assertEqual(strip_wake_word("Butler, do it", "butler"), "do it")
        self.assertEqual(strip_wake_word("butler do it", "butler"), "do it")

    def test_wake_word_elsewhere_is_left_alone(self):
        self.assertEqual(
            strip_wake_word("ask the butler about it", "butler"),
            "ask the butler about it",
        )


class TruncatedTranscriptTests(unittest.TestCase):
    def test_marks_where_the_user_cut_in(self):
        result = truncated_transcript("The first channel is primed.")
        self.assertTrue(result.startswith("The first channel is primed."))
        self.assertIn("interrupted", result)

    def test_nothing_heard_when_no_audio_played(self):
        self.assertEqual(truncated_transcript("   "), NOTHING_HEARD_MARKER)


if __name__ == "__main__":
    unittest.main()
