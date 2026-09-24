from __future__ import annotations

import unittest
from unittest import mock

from butler.voice import agent


class PhraseTimeLimitTests(unittest.TestCase):
    """Regression coverage for the runaway-recording safety net.

    2026-09-24 (llm_io_20260924T035548, request_id bd6e014a): the fixed,
    once-calibrated energy_threshold left the recognizer unable to detect a
    pause, so one "phrase" grew to 137792ms and its one real command wasn't
    acted on until ~4 minutes after it was spoken. listen_in_background was
    never given a phrase_time_limit, so nothing bounded how long a phrase
    could run. This asserts the call is now bounded by
    RECOGNIZER_PHRASE_TIME_LIMIT_SECONDS.
    """

    def test_listen_in_background_receives_the_phrase_time_limit(self) -> None:
        fake_source = mock.MagicMock()
        fake_source.__enter__ = mock.Mock(return_value=fake_source)
        fake_source.__exit__ = mock.Mock(return_value=False)

        with mock.patch.object(agent, "_ensure_asr_worker"), mock.patch.object(
            agent, "_ensure_stt_preload_worker"
        ), mock.patch.object(agent, "_ensure_wake_cue_worker"), mock.patch.object(
            agent, "_ensure_polly_keepalive_worker"
        ), mock.patch.object(agent, "_ensure_boot_phrase_worker"), mock.patch.object(
            agent, "_build_profile_microphone", return_value=fake_source
        ), mock.patch.object(agent.r, "adjust_for_ambient_noise"), mock.patch.object(
            agent.r, "listen_in_background", return_value=lambda wait_for_stop=True: None
        ) as listen_in_background, mock.patch.object(
            agent, "BARGE_IN_ENABLED", False
        ):
            agent.run_voice_recognition()

        listen_in_background.assert_called_once()
        self.assertEqual(
            listen_in_background.call_args.kwargs.get("phrase_time_limit"),
            agent.RECOGNIZER_PHRASE_TIME_LIMIT_SECONDS,
        )

    def test_default_is_well_above_any_real_command_but_bounded(self) -> None:
        # voice_benchmarks.csv: the longest genuine single command on record is
        # under 5 seconds; the runaway one was 137792ms. 12s bounds the latter
        # by more than 10x without ever touching a real command.
        self.assertGreater(agent.RECOGNIZER_PHRASE_TIME_LIMIT_SECONDS, 5.0)
        self.assertLess(agent.RECOGNIZER_PHRASE_TIME_LIMIT_SECONDS, 30.0)


if __name__ == "__main__":
    unittest.main()
