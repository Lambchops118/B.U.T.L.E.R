"""Named multi-channel PortAudio input with deterministic channel selection."""

from __future__ import annotations

import array
import sys
from dataclasses import dataclass
from typing import Any

import speech_recognition as sr


@dataclass(frozen=True)
class PortAudioInputDevice:
    index: int
    name: str
    host_api: str
    max_input_channels: int
    default_sample_rate: float


def select_pcm16_channel(
    pcm: bytes,
    *,
    source_channels: int,
    selected_channel: int,
) -> bytes:
    """Extract one channel from interleaved signed little-endian PCM16."""

    if source_channels <= 0:
        raise ValueError("source_channels must be positive")
    if not 0 <= selected_channel < source_channels:
        raise ValueError("selected_channel is outside the source channel range")
    frame_bytes = source_channels * 2
    if len(pcm) % frame_bytes:
        raise ValueError("PCM byte count is not aligned to a complete input frame")
    if source_channels == 1:
        return bytes(pcm)

    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    selected = array.array("h", samples[selected_channel::source_channels])
    if sys.byteorder != "little":
        selected.byteswap()
    return selected.tobytes()


def find_input_device(
    audio: Any,
    *,
    name_contains: str,
    preferred_host_api: str,
    sample_rate: int,
    channels: int,
    sample_format: int,
) -> PortAudioInputDevice:
    """Find a named input supporting the requested format, with stable ranking."""

    needle = name_contains.strip().lower()
    if not needle:
        raise ValueError("name_contains must not be empty")
    preferred = preferred_host_api.strip().lower()
    matches: list[tuple[tuple[int, int, int], PortAudioInputDevice]] = []
    for index in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(index)
        name = str(info.get("name") or "")
        max_channels = int(info.get("maxInputChannels") or 0)
        if needle not in name.lower() or max_channels < channels:
            continue
        host_index = int(info.get("hostApi") or 0)
        host = audio.get_host_api_info_by_index(host_index)
        host_name = str(host.get("name") or "")
        try:
            audio.is_format_supported(
                sample_rate,
                input_device=index,
                input_channels=channels,
                input_format=sample_format,
            )
        except (ValueError, OSError):
            continue
        device = PortAudioInputDevice(
            index=index,
            name=name,
            host_api=host_name,
            max_input_channels=max_channels,
            default_sample_rate=float(info.get("defaultSampleRate") or 0.0),
        )
        score = (
            int(bool(preferred) and preferred in host_name.lower()),
            int(device.default_sample_rate == sample_rate),
            -index,
        )
        matches.append((score, device))
    if not matches:
        raise RuntimeError(
            f"No PortAudio input containing '{name_contains}' supports "
            f"{sample_rate} Hz/{channels}ch PCM16."
        )
    return max(matches, key=lambda item: item[0])[1]


class _SelectedChannelStream:
    def __init__(self, inner: Any, source_channels: int, selected_channel: int) -> None:
        self._inner = inner
        self._source_channels = source_channels
        self._selected_channel = selected_channel

    def read(self, size: int) -> bytes:
        pcm = self._inner.read(size, exception_on_overflow=False)
        return select_pcm16_channel(
            pcm,
            source_channels=self._source_channels,
            selected_channel=self._selected_channel,
        )

    def close(self) -> None:
        self._inner.close()


class PortAudioChannelMicrophone(sr.AudioSource):
    """SpeechRecognition source that opens a named device and selects one channel."""

    def __init__(
        self,
        *,
        name_contains: str,
        preferred_host_api: str = "",
        sample_rate: int = 16000,
        source_channels: int = 1,
        selected_channel: int = 0,
        chunk_size: int = 1024,
        pyaudio_module: Any | None = None,
    ) -> None:
        if sample_rate <= 0 or source_channels <= 0 or chunk_size <= 0:
            raise ValueError("capture format values must be positive")
        if not 0 <= selected_channel < source_channels:
            raise ValueError("selected_channel is outside the source channel range")
        self.name_contains = name_contains
        self.preferred_host_api = preferred_host_api
        self.source_channels = source_channels
        self.selected_channel = selected_channel
        self.SAMPLE_RATE = sample_rate
        self.SAMPLE_WIDTH = 2
        self.CHUNK = chunk_size
        self._pyaudio_module = pyaudio_module
        self.audio = None
        self.stream = None
        self.device: PortAudioInputDevice | None = None

    def __enter__(self):
        if self.stream is not None:
            raise RuntimeError("Audio source is already entered")
        module = self._pyaudio_module
        if module is None:
            import pyaudio as module

        audio = module.PyAudio()
        try:
            device = find_input_device(
                audio,
                name_contains=self.name_contains,
                preferred_host_api=self.preferred_host_api,
                sample_rate=self.SAMPLE_RATE,
                channels=self.source_channels,
                sample_format=module.paInt16,
            )
            inner = audio.open(
                input_device_index=device.index,
                channels=self.source_channels,
                format=module.paInt16,
                rate=self.SAMPLE_RATE,
                frames_per_buffer=self.CHUNK,
                input=True,
            )
        except Exception:
            audio.terminate()
            raise
        self.audio = audio
        self.device = device
        self.stream = _SelectedChannelStream(
            inner,
            self.source_channels,
            self.selected_channel,
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        stream = self.stream
        audio = self.audio
        self.stream = None
        self.audio = None
        if stream is not None:
            stream.close()
        if audio is not None:
            audio.terminate()
