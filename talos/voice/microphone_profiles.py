"""Explicit room-microphone profiles shared by the launcher and voice worker."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MicrophoneProfile:
    name: str
    label: str
    capture_endpoint_env: str
    device_name_env: str
    default_device_name: str
    source_channels: int
    selected_channel: int
    preferred_host_api: str
    energy_threshold: str
    windows_aec: bool


MICROPHONE_PROFILES = {
    "respeaker": MicrophoneProfile(
        name="respeaker",
        label="ReSpeaker XVF3800 (ASR channel)",
        capture_endpoint_env="TALOS_RESPEAKER_CAPTURE_ENDPOINT_ID",
        device_name_env="TALOS_RESPEAKER_DEVICE_NAME",
        # PortAudio's MME name is truncated after this unambiguous prefix on the
        # deployed host. Matching the product suffix would miss that endpoint.
        default_device_name="Echo Cancelling Speakerphone",
        source_channels=2,
        selected_channel=1,
        preferred_host_api="MME",
        energy_threshold="auto",
        # Windows AEC accepted on this microphone 2026-09-08, on the evidence
        # the earlier fail-closed decision asked for. A bounded live probe at
        # amplitude 0.06 measured 43.668 dB ERLE (far-end RMS 278.863 -> 1.829),
        # peak normalized correlation 0.546 -> 0.050, and 0 callback errors --
        # comparable to the Yeti baseline that qualified this contract
        # (45.696 dB at amplitude 0.03). Windows reports the AEC, noise
        # suppression, AGC and deep-noise-suppression effects active, and the
        # far-end reference resolves as `system_default_verified`: the pinned
        # render endpoint IS the current default, which is the reference the
        # open question was about. Re-run
        # `python -m talos.voice.diagnostics.windows_aec_probe` and set this
        # back to False if the render path or the microphone changes.
        windows_aec=True,
    ),
    "yeti": MicrophoneProfile(
        name="yeti",
        label="Blue Yeti",
        capture_endpoint_env="TALOS_YETI_CAPTURE_ENDPOINT_ID",
        device_name_env="TALOS_YETI_DEVICE_NAME",
        default_device_name="Yeti",
        source_channels=1,
        selected_channel=0,
        preferred_host_api="MME",
        energy_threshold="500",
        windows_aec=True,
    ),
}

DEFAULT_MICROPHONE_PROFILE = "respeaker"


def normalize_microphone_profile(value: object) -> str:
    name = str(value or "").strip().lower()
    return name if name in MICROPHONE_PROFILES else DEFAULT_MICROPHONE_PROFILE


def get_microphone_profile(value: object) -> MicrophoneProfile:
    return MICROPHONE_PROFILES[normalize_microphone_profile(value)]


def resolve_energy_threshold(
    profile: MicrophoneProfile,
    configured: str | None,
    calibrated: float,
) -> float:
    """Resolve a fixed threshold or preserve the recognizer's room calibration."""

    raw = (configured or profile.energy_threshold).strip().lower()
    if raw == "auto":
        if calibrated <= 0:
            raise ValueError("calibrated energy threshold must be positive")
        return float(calibrated)
    try:
        threshold = float(raw)
    except ValueError as exc:
        raise ValueError(
            "TALOS_RECOGNIZER_ENERGY_THRESHOLD must be positive or 'auto'."
        ) from exc
    if threshold <= 0:
        raise ValueError(
            "TALOS_RECOGNIZER_ENERGY_THRESHOLD must be positive or 'auto'."
        )
    return threshold
