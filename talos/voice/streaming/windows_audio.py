"""Windows communications-mode capture with observable system AEC.

This module keeps all PyWinRT imports lazy so dependency-light tests and
non-Windows processes can still import the voice package.  The selected capture
and render endpoints are explicit.  If Windows does not report an active AEC
effect, or if automatic reference selection would point at a different default
render endpoint, initialization fails closed.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable


FrameCallback = Callable[[bytes], None]


def normalize_audio_graph_pcm(
    pcm: bytes,
    *,
    samples_per_quantum: int,
    channels: int,
) -> bytes:
    """Convert AudioGraph's native float32 frame-node output to PCM16."""
    expected_samples = samples_per_quantum * channels
    if len(pcm) != expected_samples * 4:
        return pcm

    import numpy as np

    floats = np.frombuffer(pcm, dtype=np.float32)
    return (np.clip(floats, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


@dataclass(frozen=True)
class WindowsAudioEndpoints:
    capture_id: str
    render_id: str


@dataclass(frozen=True)
class WindowsAecEvidence:
    capture_id: str
    render_id: str
    effect_types: tuple[str, ...]
    aec_enabled: bool
    aec_state_settable: bool
    reference_mode: str


def get_default_windows_audio_endpoints() -> WindowsAudioEndpoints:
    """Return the full MMDevice IDs used by WinRT, not unstable PyAudio indexes."""
    try:
        from winrt.windows.media.devices import AudioDeviceRole, MediaDevice
    except ImportError as exc:  # pragma: no cover - platform/dependency guard
        raise RuntimeError(
            "Windows audio support requires the pinned PyWinRT voice dependencies."
        ) from exc

    return WindowsAudioEndpoints(
        capture_id=MediaDevice.get_default_audio_capture_id(AudioDeviceRole.DEFAULT),
        render_id=MediaDevice.get_default_audio_render_id(AudioDeviceRole.DEFAULT),
    )


def query_windows_aec(
    endpoints: WindowsAudioEndpoints,
) -> WindowsAecEvidence:
    """Query communications-mode effects and bind/verify the render reference."""
    try:
        from winrt.windows.media import AudioProcessing
        from winrt.windows.media.capture import MediaCategory
        from winrt.windows.media.effects import (
            AudioEffectState,
            AudioEffectType,
            AudioEffectsManager,
        )
    except ImportError as exc:  # pragma: no cover - platform/dependency guard
        raise RuntimeError(
            "Windows AEC discovery requires the pinned PyWinRT voice dependencies."
        ) from exc

    manager = AudioEffectsManager.create_audio_capture_effects_manager_with_mode(
        endpoints.capture_id,
        MediaCategory.COMMUNICATIONS,
        AudioProcessing.DEFAULT,
    )
    effects = tuple(manager.get_audio_capture_effects())
    effect_types = tuple(effect.audio_effect_type.name for effect in effects)
    aec = next(
        (
            effect
            for effect in effects
            if effect.audio_effect_type == AudioEffectType.ACOUSTIC_ECHO_CANCELLATION
        ),
        None,
    )
    if aec is None:
        return WindowsAecEvidence(
            capture_id=endpoints.capture_id,
            render_id=endpoints.render_id,
            effect_types=effect_types,
            aec_enabled=False,
            aec_state_settable=False,
            reference_mode="unavailable",
        )

    if aec.state != AudioEffectState.ON and aec.can_set_state:
        aec.set_state(AudioEffectState.ON)
    enabled = aec.state == AudioEffectState.ON
    configuration = aec.acoustic_echo_cancellation_configuration
    if configuration is not None:
        configuration.set_echo_cancellation_render_endpoint(endpoints.render_id)
        reference_mode = "explicit"
    else:
        defaults = get_default_windows_audio_endpoints()
        if defaults.render_id != endpoints.render_id:
            raise RuntimeError(
                "Windows AEC does not expose reference control and the pinned "
                "render endpoint is not the current default."
            )
        reference_mode = "system_default_verified"

    return WindowsAecEvidence(
        capture_id=endpoints.capture_id,
        render_id=endpoints.render_id,
        effect_types=effect_types,
        aec_enabled=enabled,
        aec_state_settable=bool(aec.can_set_state),
        reference_mode=reference_mode,
    )


class WindowsAecCapture:
    """Long-lived AudioGraph capture source producing echo-cleaned PCM.

    The AudioGraph callback only copies one fixed quantum and invokes the
    supplied callback. Callers must keep that callback bounded and non-blocking.
    """

    def __init__(
        self,
        endpoints: WindowsAudioEndpoints,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        bits_per_sample: int = 16,
        desired_frame_ms: int = 10,
        startup_timeout: float = 10.0,
        endpoint_poll_seconds: float = 1.0,
    ) -> None:
        if sample_rate <= 0 or channels <= 0 or bits_per_sample <= 0:
            raise ValueError("Windows capture format values must be positive.")
        if desired_frame_ms <= 0:
            raise ValueError("desired_frame_ms must be positive.")
        self.endpoints = endpoints
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.bits_per_sample = int(bits_per_sample)
        self.desired_frame_ms = int(desired_frame_ms)
        self.startup_timeout = float(startup_timeout)
        self.endpoint_poll_seconds = max(0.1, float(endpoint_poll_seconds))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_frame: FrameCallback | None = None
        self._error: str | None = None
        self._frames = 0
        self._pcm_bytes = 0
        self._callback_errors = 0
        self._samples_per_quantum: int | None = None
        self._native_frame_bytes: int | None = None
        self._evidence: WindowsAecEvidence | None = None

    def start(self, on_frame: FrameCallback) -> None:
        if not callable(on_frame):
            raise TypeError("on_frame must be callable")
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("Windows AEC capture is already started.")
            self._on_frame = on_frame
            self._stop.clear()
            self._ready.clear()
            self._error = None
            self._evidence = query_windows_aec(self.endpoints)
            if not self._evidence.aec_enabled:
                raise RuntimeError(
                    "The selected Windows capture endpoint does not report active AEC."
                )
            self._thread = threading.Thread(
                target=self._thread_main,
                name="talos-windows-aec-capture",
                daemon=True,
            )
            self._thread.start()

        if not self._ready.wait(timeout=max(0.1, self.startup_timeout)):
            self.stop()
            raise RuntimeError("Timed out starting the Windows AEC AudioGraph.")
        with self._lock:
            error = self._error
        if error:
            self.stop()
            raise RuntimeError(error)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout))
        with self._lock:
            self._thread = None
            self._on_frame = None

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "frames": self._frames,
                "pcm_bytes": self._pcm_bytes,
                "callback_errors": self._callback_errors,
                "samples_per_quantum": self._samples_per_quantum,
                "native_frame_bytes": self._native_frame_bytes,
                "error": self._error,
                "evidence": asdict(self._evidence) if self._evidence else None,
            }

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run_graph())
        except BaseException as exc:  # noqa: BLE001 - surfaced through snapshot/start
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"[:1000]
            self._ready.set()

    async def _run_graph(self) -> None:
        try:
            from winrt.windows.devices.enumeration import DeviceInformation
            from winrt.windows.media import AudioBufferAccessMode, AudioProcessing
            from winrt.windows.media.audio import (
                AudioGraph,
                AudioGraphCreationStatus,
                AudioGraphSettings,
                AudioDeviceNodeCreationStatus,
                QuantumSizeSelectionMode,
            )
            from winrt.windows.media.capture import MediaCategory
            from winrt.windows.media.mediaproperties import AudioEncodingProperties
            from winrt.windows.media.render import AudioRenderCategory
        except ImportError as exc:  # pragma: no cover - platform/dependency guard
            raise RuntimeError(
                "Windows AudioGraph capture requires the pinned PyWinRT dependencies."
            ) from exc

        capture_device = await DeviceInformation.create_from_id_async(
            self.endpoints.capture_id
        )
        render_device = await DeviceInformation.create_from_id_async(
            self.endpoints.render_id
        )
        if capture_device is None or render_device is None:
            raise RuntimeError("A pinned Windows audio endpoint is unavailable.")

        encoding = AudioEncodingProperties.create_pcm(
            self.sample_rate,
            self.channels,
            self.bits_per_sample,
        )
        settings = AudioGraphSettings(AudioRenderCategory.COMMUNICATIONS)
        settings.primary_render_device = render_device
        settings.desired_render_device_audio_processing = AudioProcessing.DEFAULT
        settings.encoding_properties = encoding
        settings.quantum_size_selection_mode = QuantumSizeSelectionMode.CLOSEST_TO_DESIRED
        settings.desired_samples_per_quantum = round(
            self.sample_rate * self.desired_frame_ms / 1000
        )

        graph_result = await AudioGraph.create_async(settings)
        if graph_result.status != AudioGraphCreationStatus.SUCCESS:
            raise RuntimeError(
                "Windows AudioGraph creation failed: "
                f"{graph_result.status.name} ({graph_result.extended_error})."
            )
        graph = graph_result.graph
        capture_node = None
        output_node = None
        quantum_token = None
        try:
            capture_result = (
                await graph.create_device_input_node_with_format_on_device_async(
                    MediaCategory.COMMUNICATIONS,
                    encoding,
                    capture_device,
                )
            )
            if capture_result.status != AudioDeviceNodeCreationStatus.SUCCESS:
                raise RuntimeError(
                    "Windows communications capture creation failed: "
                    f"{capture_result.status.name} "
                    f"({capture_result.extended_error})."
                )
            capture_node = capture_result.device_input_node
            output_node = graph.create_frame_output_node_with_format(encoding)
            capture_node.add_outgoing_connection(output_node)

            def on_quantum_started(_sender, _args) -> None:
                frame = output_node.get_frame()
                if frame is None:
                    return
                try:
                    with frame:
                        with frame.lock_buffer(AudioBufferAccessMode.READ) as buffer:
                            with buffer.create_reference() as reference:
                                pcm = bytes(memoryview(reference)[: buffer.length])
                    if not pcm:
                        return
                    native_frame_bytes = len(pcm)
                    samples_per_quantum = self._samples_per_quantum
                    if samples_per_quantum is not None:
                        pcm = normalize_audio_graph_pcm(
                            pcm,
                            samples_per_quantum=samples_per_quantum,
                            channels=self.channels,
                        )
                    with self._lock:
                        callback = self._on_frame
                        if self._native_frame_bytes is None:
                            self._native_frame_bytes = native_frame_bytes
                        self._frames += 1
                        self._pcm_bytes += len(pcm)
                    if callback is not None:
                        try:
                            callback(pcm)
                        except Exception:
                            with self._lock:
                                self._callback_errors += 1
                except Exception as exc:
                    with self._lock:
                        self._callback_errors += 1
                        if self._error is None:
                            self._error = f"AudioGraph callback: {type(exc).__name__}: {exc}"[
                                :1000
                            ]

            quantum_token = graph.add_quantum_started(on_quantum_started)
            with self._lock:
                self._samples_per_quantum = int(graph.samples_per_quantum)
            graph.start()
            self._ready.set()

            next_endpoint_check = time.monotonic() + self.endpoint_poll_seconds
            while not self._stop.is_set():
                await asyncio.sleep(0.05)
                if time.monotonic() < next_endpoint_check:
                    continue
                next_endpoint_check = time.monotonic() + self.endpoint_poll_seconds
                defaults = get_default_windows_audio_endpoints()
                evidence = self._evidence
                if (
                    evidence is not None
                    and evidence.reference_mode == "system_default_verified"
                    and defaults.render_id != self.endpoints.render_id
                ):
                    raise RuntimeError(
                        "Default Windows render endpoint changed; disabling AEC capture."
                    )
                if defaults.capture_id != self.endpoints.capture_id:
                    raise RuntimeError(
                        "Default Windows capture endpoint changed; disabling AEC capture."
                    )
        finally:
            self._stop.set()
            if quantum_token is not None:
                graph.remove_quantum_started(quantum_token)
            graph.stop()
            if output_node is not None:
                output_node.close()
            if capture_node is not None:
                capture_node.close()
            graph.close()
            self._ready.set()
