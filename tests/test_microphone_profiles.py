from __future__ import annotations

import array
import unittest

from talos.voice.microphone_profiles import (
    get_microphone_profile,
    normalize_microphone_profile,
    resolve_energy_threshold,
)
from talos.voice.streaming.portaudio_input import (
    PortAudioChannelMicrophone,
    find_input_device,
    select_pcm16_channel,
)


class _FakeStream:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.closed = False
        self.read_calls = []

    def read(self, size, exception_on_overflow=True):
        self.read_calls.append((size, exception_on_overflow))
        return self.pcm

    def close(self):
        self.closed = True


class _FakeAudio:
    def __init__(self, devices, hosts, pcm=b"") -> None:
        self.devices = devices
        self.hosts = hosts
        self.stream = _FakeStream(pcm)
        self.open_kwargs = None
        self.terminated = False

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]

    def get_host_api_info_by_index(self, index):
        return self.hosts[index]

    def is_format_supported(self, rate, **kwargs):
        if self.devices[kwargs["input_device"]].get("unsupported"):
            raise ValueError("unsupported")
        return rate

    def open(self, **kwargs):
        self.open_kwargs = kwargs
        return self.stream

    def terminate(self):
        self.terminated = True


class _FakePyAudio:
    paInt16 = 8

    def __init__(self, audio) -> None:
        self.audio = audio

    def PyAudio(self):
        return self.audio


def _device(index, name, host, channels=2, rate=16000, **extra):
    return {
        "index": index,
        "name": name,
        "hostApi": host,
        "maxInputChannels": channels,
        "defaultSampleRate": rate,
        **extra,
    }


class MicrophoneProfileTests(unittest.TestCase):
    def test_profile_names_are_normalized_and_invalid_values_fail_safe(self):
        self.assertEqual(normalize_microphone_profile(" YETI "), "yeti")
        self.assertEqual(normalize_microphone_profile("unknown"), "respeaker")

    def test_respeaker_profile_selects_second_of_two_channels(self):
        profile = get_microphone_profile("respeaker")
        self.assertEqual(profile.source_channels, 2)
        self.assertEqual(profile.selected_channel, 1)

    def test_both_deployed_profiles_carry_the_windows_aec_contract(self):
        """Barge-in is gated on this flag alone, in the worker and the launcher.

        The ReSpeaker was fail-closed until its far-end reference was measured
        on this host; the probe recorded 43.7 dB ERLE with a verified
        system-default render reference, so it now carries the contract.
        """
        for name in ("respeaker", "yeti"):
            with self.subTest(profile=name):
                self.assertTrue(get_microphone_profile(name).windows_aec)

    def test_respeaker_uses_calibrated_energy_threshold(self):
        profile = get_microphone_profile("respeaker")
        self.assertEqual(resolve_energy_threshold(profile, None, 412.5), 412.5)

    def test_yeti_keeps_fixed_energy_threshold(self):
        profile = get_microphone_profile("yeti")
        self.assertEqual(resolve_energy_threshold(profile, None, 123.0), 500.0)

    def test_invalid_energy_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_energy_threshold(get_microphone_profile("yeti"), "zero", 100)


class PortAudioSelectionTests(unittest.TestCase):
    def test_pcm_channel_selection_extracts_right_channel(self):
        pcm = array.array("h", [1, 101, 2, 102]).tobytes()
        selected = select_pcm16_channel(
            pcm,
            source_channels=2,
            selected_channel=1,
        )
        self.assertEqual(array.array("h", selected).tolist(), [101, 102])

    def test_named_device_prefers_requested_host_api(self):
        audio = _FakeAudio(
            [
                _device(0, "Echo Cancelling Speakerphone", 0, rate=48000),
                _device(1, "Echo Cancelling Speakerphone (reSpeaker)", 1),
            ],
            [{"name": "Windows WASAPI"}, {"name": "MME"}],
        )
        selected = find_input_device(
            audio,
            name_contains="Echo Cancelling Speakerphone",
            preferred_host_api="MME",
            sample_rate=16000,
            channels=2,
            sample_format=8,
        )
        self.assertEqual(selected.index, 1)
        self.assertEqual(selected.host_api, "MME")

    def test_named_device_must_support_the_full_stereo_format(self):
        audio = _FakeAudio(
            [_device(0, "Echo Cancelling Speakerphone", 0, unsupported=True)],
            [{"name": "MME"}],
        )
        with self.assertRaisesRegex(RuntimeError, "supports 16000 Hz/2ch"):
            find_input_device(
                audio,
                name_contains="Echo Cancelling Speakerphone",
                preferred_host_api="MME",
                sample_rate=16000,
                channels=2,
                sample_format=8,
            )

    def test_audio_source_opens_stereo_and_returns_only_asr_channel(self):
        pcm = array.array("h", [10, 110, 20, 120]).tobytes()
        audio = _FakeAudio(
            [_device(0, "Echo Cancelling Speakerphone (r", 0)],
            [{"name": "MME"}],
            pcm=pcm,
        )
        source = PortAudioChannelMicrophone(
            name_contains="Echo Cancelling Speakerphone",
            preferred_host_api="MME",
            source_channels=2,
            selected_channel=1,
            pyaudio_module=_FakePyAudio(audio),
        )
        with source:
            captured = source.stream.read(2)
            self.assertEqual(array.array("h", captured).tolist(), [110, 120])
            self.assertEqual(audio.open_kwargs["channels"], 2)
            self.assertEqual(audio.open_kwargs["rate"], 16000)
            self.assertEqual(source.device.index, 0)
        self.assertTrue(audio.stream.closed)
        self.assertTrue(audio.terminated)


if __name__ == "__main__":
    unittest.main()
