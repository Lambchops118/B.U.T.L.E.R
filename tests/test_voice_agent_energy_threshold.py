from __future__ import annotations

import unittest
from unittest import mock

from butler.voice import agent


class DynamicEnergyThresholdTests(unittest.TestCase):
    """Protect the duplex barge-in contract from recognizer adaptation."""

    def test_dynamic_energy_threshold_stays_disabled(self) -> None:
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

        # DuplexRecognizerAudioSource emits zeroed frames during playback so
        # SpeechRecognition cannot duplicate the dedicated AEC/VAD barge-in
        # lane. Adapting against those frames collapses the threshold and breaks
        # capture after playback, so the startup calibration must remain fixed.
        self.assertFalse(agent.r.dynamic_energy_threshold)


if __name__ == "__main__":
    unittest.main()
