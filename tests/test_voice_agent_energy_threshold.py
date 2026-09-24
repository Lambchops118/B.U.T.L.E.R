from __future__ import annotations

import unittest
from unittest import mock

from butler.voice import agent


class DynamicEnergyThresholdTests(unittest.TestCase):
    """Regression coverage for the runaway-recording root cause.

    2026-09-24 (llm_io_20260924T035548, request_id bd6e014a): with
    dynamic_energy_threshold=False, the recognizer's energy_threshold was
    fixed at whatever adjust_for_ambient_noise measured once, at startup, and
    never adjusted again. Ambient noise later staying above that fixed value
    for longer than pause_threshold (0.6s) meant no pause was ever detected,
    so one "phrase" recorded for 137792ms (2m18s) before finally closing --
    its one real command wasn't acted on until ~4 minutes after it was
    spoken. Enabling dynamic_energy_threshold lets the recognizer keep
    tracking ambient noise instead of relying on a single one-time reading.
    """

    def test_dynamic_energy_threshold_is_enabled(self) -> None:
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
        ), mock.patch.object(agent, "BARGE_IN_ENABLED", False):
            agent.run_voice_recognition()

        self.assertTrue(agent.r.dynamic_energy_threshold)


if __name__ == "__main__":
    unittest.main()
