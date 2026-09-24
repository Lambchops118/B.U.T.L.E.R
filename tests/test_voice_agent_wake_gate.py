from __future__ import annotations

import unittest
from unittest import mock

from butler.voice import agent


class _FakeAudioData:
    """Minimal stand-in for speech_recognition.AudioData."""

    def __init__(self, seconds: float = 2.0) -> None:
        self.sample_width = 2
        self.sample_rate = 16000
        # Loud enough to pass the energy gate; silence (b"\x00" * n) reads as 0 rms.
        frame_count = int(self.sample_rate * seconds)
        self._raw = (b"\x10\x27" * frame_count)[: frame_count * self.sample_width]

    def get_raw_data(self):
        return self._raw


class WakeGateTelemetryTests(unittest.TestCase):
    """Regression coverage for the one silent-drop path in the recognition callback.

    A real STT-transcribed utterance that doesn't start with the wake word is
    intentionally never written to the CSV benchmark log (background chatter
    would flood it), but before this change it also left no trace in the
    pipeline telemetry stream -- only a console print that is gone once it
    scrolls. Investigating "the mic hears it but the system doesn't act" (see
    llm_io_20260924T035548: 11 stt_completed events, only 6 reached the agent)
    required watching the live console for the missing 5. This asserts the
    drop is now recorded with the transcript, correlated by request_id with
    that turn's stt_completed.
    """

    def test_missing_wake_word_emits_a_correlated_telemetry_event(self) -> None:
        with mock.patch.object(
            agent, "_acquire_transcript", return_value="turn off the desk lamp too"
        ), mock.patch.object(agent, "emit_pipeline_event") as emit:
            agent._process_recognition_audio(_FakeAudioData())

        rejected = [c for c in emit.call_args_list if c.kwargs.get("event") == "voice_turn_rejected"]
        self.assertEqual(len(rejected), 1)
        call = rejected[0].kwargs
        self.assertEqual(call["reason"], "wake_word_missing_in_transcript")
        self.assertEqual(call["transcript"], "turn off the desk lamp too")
        # request_id is the benchmark's own session_id -- the same field a real
        # turn's stt_completed event carries, from _transcribe_local's identical
        # _emit_voice_pipeline_event call -- so the two correlate in the log.
        self.assertTrue(call["request_id"])

    def test_wake_word_alone_with_no_command_emits_a_telemetry_event(self) -> None:
        with mock.patch.object(
            agent, "_acquire_transcript", return_value=f"{agent.WAKE_WORD}"
        ), mock.patch.object(agent, "emit_pipeline_event") as emit, mock.patch.object(
            agent, "_signal_wake_cue"
        ), mock.patch.object(agent, "awareness_signals"):
            agent._process_recognition_audio(_FakeAudioData())

        rejected = [c for c in emit.call_args_list if c.kwargs.get("event") == "voice_turn_rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].kwargs["reason"], "wake_word_without_command")

    def test_command_with_wake_word_dispatches_and_does_not_reject(self) -> None:
        with mock.patch.object(
            agent, "_acquire_transcript", return_value=f"{agent.WAKE_WORD}, turn off the desk lamp"
        ), mock.patch.object(agent, "emit_pipeline_event") as emit, mock.patch.object(
            agent, "_signal_wake_cue"
        ), mock.patch.object(agent, "awareness_signals"), mock.patch.object(
            agent, "_command_executor"
        ) as executor:
            agent._process_recognition_audio(_FakeAudioData())

        executor.submit.assert_called_once()
        self.assertEqual(executor.submit.call_args.args[1], "turn off the desk lamp")
        rejected = [c for c in emit.call_args_list if c.kwargs.get("event") == "voice_turn_rejected"]
        self.assertEqual(rejected, [])


if __name__ == "__main__":
    unittest.main()
