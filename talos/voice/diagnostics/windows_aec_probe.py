"""Measure Windows communications-mode AEC against raw PyAudio capture.

This operator tool never writes PCM. It plays a bounded deterministic,
speech-band test signal through the selected render device, retains capture only
in memory, and prints aggregate RMS/correlation/CPU evidence as JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
from dataclasses import asdict

import numpy as np

from talos.voice.streaming.windows_audio import (
    WindowsAecCapture,
    WindowsAudioEndpoints,
    get_default_windows_audio_endpoints,
    query_windows_aec,
)


def _test_signal(sample_rate: int, seconds: float, amplitude: float) -> bytes:
    sample_count = round(sample_rate * seconds)
    timeline = np.arange(sample_count, dtype=np.float64) / sample_rate
    # Deterministic, speech-band components with slow amplitude modulation.
    carrier = (
        np.sin(2 * np.pi * 233.0 * timeline)
        + 0.65 * np.sin(2 * np.pi * 487.0 * timeline + 0.2)
        + 0.4 * np.sin(2 * np.pi * 997.0 * timeline + 0.7)
        + 0.25 * np.sin(2 * np.pi * 1789.0 * timeline + 1.1)
    )
    envelope = 0.45 + 0.35 * np.sin(2 * np.pi * 2.7 * timeline) ** 2
    signal = np.clip(carrier * envelope * amplitude / 2.3, -1.0, 1.0)
    return (signal * 32767.0).astype(np.int16).tobytes()


def _play_signal(
    pcm: bytes,
    *,
    sample_rate: int,
    output_device_index: int | None,
    warmup_seconds: float,
) -> None:
    import pyaudio

    time.sleep(max(0.0, warmup_seconds))
    interface = pyaudio.PyAudio()
    stream = interface.open(
        format=interface.get_format_from_width(2),
        channels=1,
        rate=sample_rate,
        output=True,
        output_device_index=output_device_index,
        frames_per_buffer=round(sample_rate * 0.01),
    )
    try:
        for offset in range(0, len(pcm), round(sample_rate * 0.01) * 2):
            stream.write(pcm[offset : offset + round(sample_rate * 0.01) * 2])
    finally:
        stream.stop_stream()
        stream.close()
        interface.terminate()


def _capture_raw(
    *,
    duration_seconds: float,
    sample_rate: int,
    input_device_index: int | None,
) -> tuple[bytes, float]:
    import pyaudio

    interface = pyaudio.PyAudio()
    stream = interface.open(
        format=interface.get_format_from_width(2),
        channels=1,
        rate=sample_rate,
        input=True,
        input_device_index=input_device_index,
        frames_per_buffer=round(sample_rate * 0.01),
    )
    chunks: list[bytes] = []
    cpu_started = time.process_time()
    deadline = time.monotonic() + duration_seconds
    try:
        while time.monotonic() < deadline:
            chunks.append(
                stream.read(
                    round(sample_rate * 0.01),
                    exception_on_overflow=False,
                )
            )
    finally:
        cpu_seconds = time.process_time() - cpu_started
        stream.stop_stream()
        stream.close()
        interface.terminate()
    return b"".join(chunks), cpu_seconds


def _capture_aec(
    endpoints: WindowsAudioEndpoints,
    *,
    duration_seconds: float,
    sample_rate: int,
) -> tuple[bytes, float, dict[str, object]]:
    chunks: list[bytes] = []
    lock = threading.Lock()

    def on_frame(pcm: bytes) -> None:
        with lock:
            chunks.append(pcm)

    capture = WindowsAecCapture(endpoints, sample_rate=sample_rate)
    cpu_started = time.process_time()
    capture.start(on_frame)
    try:
        time.sleep(duration_seconds)
    finally:
        capture.stop()
    cpu_seconds = time.process_time() - cpu_started
    with lock:
        pcm = b"".join(chunks)
    return pcm, cpu_seconds, capture.snapshot()


def _metrics(
    captured_pcm: bytes,
    signal_pcm: bytes,
    *,
    sample_rate: int,
    warmup_seconds: float,
) -> dict[str, float | int]:
    from scipy.signal import correlate

    captured = np.frombuffer(captured_pcm, dtype=np.int16).astype(np.float64)
    signal = np.frombuffer(signal_pcm, dtype=np.int16).astype(np.float64)
    warmup_samples = round(warmup_seconds * sample_rate)
    active = captured[warmup_samples : warmup_samples + signal.size]
    usable = min(active.size, signal.size)
    active = active[:usable]
    signal = signal[:usable]
    if not usable:
        return {
            "pcm_bytes": len(captured_pcm),
            "active_rms": 0.0,
            "peak_normalized_correlation": 0.0,
        }
    active_rms = float(np.sqrt(np.mean(active**2)))
    signal_rms = float(np.sqrt(np.mean(signal**2)))
    centered_active = active - np.mean(active)
    centered_signal = signal - np.mean(signal)
    correlation = correlate(centered_active, centered_signal, mode="full", method="fft")
    denominator = float(
        np.linalg.norm(centered_active) * np.linalg.norm(centered_signal)
    )
    normalized = 0.0 if denominator <= 0 else float(np.max(np.abs(correlation)) / denominator)
    return {
        "pcm_bytes": len(captured_pcm),
        "active_rms": round(active_rms, 3),
        "render_rms": round(signal_rms, 3),
        "peak_normalized_correlation": round(normalized, 6),
        "duration_ms": round(len(captured_pcm) / (sample_rate * 2) * 1000.0, 1),
    }


def run_probe(
    *,
    endpoints: WindowsAudioEndpoints,
    input_device_index: int | None,
    output_device_index: int | None,
    sample_rate: int = 16000,
    signal_seconds: float = 5.0,
    warmup_seconds: float = 1.0,
    amplitude: float = 0.06,
) -> dict[str, object]:
    evidence = query_windows_aec(endpoints)
    if not evidence.aec_enabled:
        raise RuntimeError("Windows did not report active AEC on the capture endpoint.")
    signal_pcm = _test_signal(sample_rate, signal_seconds, amplitude)
    duration_seconds = warmup_seconds + signal_seconds + 0.5

    def run_with_player(capture_fn):
        player = threading.Thread(
            target=_play_signal,
            kwargs={
                "pcm": signal_pcm,
                "sample_rate": sample_rate,
                "output_device_index": output_device_index,
                "warmup_seconds": warmup_seconds,
            },
            name="talos-aec-probe-player",
        )
        player.start()
        try:
            return capture_fn()
        finally:
            player.join()

    raw_pcm, raw_cpu = run_with_player(
        lambda: _capture_raw(
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            input_device_index=input_device_index,
        )
    )
    aec_pcm, aec_cpu, aec_snapshot = run_with_player(
        lambda: _capture_aec(
            endpoints,
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
        )
    )
    raw_metrics = _metrics(
        raw_pcm,
        signal_pcm,
        sample_rate=sample_rate,
        warmup_seconds=warmup_seconds,
    )
    aec_metrics = _metrics(
        aec_pcm,
        signal_pcm,
        sample_rate=sample_rate,
        warmup_seconds=warmup_seconds,
    )
    raw_rms = float(raw_metrics["active_rms"])
    aec_rms = float(aec_metrics["active_rms"])
    erle_db = (
        0.0
        if raw_rms <= 0 or aec_rms <= 0
        else 20.0 * math.log10(raw_rms / aec_rms)
    )
    return {
        "schema_version": 1,
        "stores_pcm": False,
        "endpoints": asdict(endpoints),
        "aec_evidence": asdict(evidence),
        "sample_rate": sample_rate,
        "signal_seconds": signal_seconds,
        "warmup_seconds": warmup_seconds,
        "amplitude": amplitude,
        "raw": {
            **raw_metrics,
            "cpu_seconds": round(raw_cpu, 4),
        },
        "aec": {
            **aec_metrics,
            "cpu_seconds": round(aec_cpu, 4),
            "capture": aec_snapshot,
        },
        "echo_return_loss_enhancement_db": round(erle_db, 3),
        "correlation_reduction": round(
            float(raw_metrics["peak_normalized_correlation"])
            - float(aec_metrics["peak_normalized_correlation"]),
            6,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-id")
    parser.add_argument("--render-id")
    parser.add_argument("--input-device-index", type=int)
    parser.add_argument("--output-device-index", type=int)
    parser.add_argument("--signal-seconds", type=float, default=5.0)
    parser.add_argument("--warmup-seconds", type=float, default=1.0)
    parser.add_argument("--amplitude", type=float, default=0.06)
    args = parser.parse_args()

    defaults = get_default_windows_audio_endpoints()
    endpoints = WindowsAudioEndpoints(
        capture_id=args.capture_id or defaults.capture_id,
        render_id=args.render_id or defaults.render_id,
    )
    print(
        "WARNING: AEC probe is opening the room microphone and playing a bounded "
        "test signal. PCM stays in memory and is discarded; only aggregate "
        "metrics are printed."
    )
    result = run_probe(
        endpoints=endpoints,
        input_device_index=args.input_device_index,
        output_device_index=args.output_device_index,
        signal_seconds=args.signal_seconds,
        warmup_seconds=args.warmup_seconds,
        amplitude=args.amplitude,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
