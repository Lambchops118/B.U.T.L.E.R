from __future__ import annotations

import io
import os
import re
import time
import wave
import random
import audioop
import tempfile
import boto3
from botocore.config import Config as BotocoreConfig
import openai
import whisper
import pyaudio
import threading
import contextlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import speech_recognition as sr

from talos.config import env_bool, env_float, env_int, load_environment, require_env
from talos.services import awareness_signals
from talos.telemetry import emit_pipeline_event
from talos.text.service_client import (
    send_interrupt,
    send_message,
    send_prewarm,
    stream_message,
)
from talos.voice.asr_queue import AsrPriority, BoundedAsrQueue
from talos.voice.benchmarking import VoiceBenchmarkSession
from talos.voice.microphone_profiles import (
    get_microphone_profile,
    resolve_energy_threshold,
)
from talos.voice.streaming import barge_in as barge_in_module
from talos.voice.streaming.barge_in import BargeInConfig, BargeInDetector, SpeechSession
from talos.voice.streaming.barge_in_observability import (
    FixtureRecorderConfig,
    SynchronizedFixtureRecorder,
)
from talos.voice.streaming.speaker import StreamingSpeaker
from talos.voice.streaming.vad import select_vad_lane


load_environment()

r = sr.Recognizer()
MICROPHONE_PROFILE = get_microphone_profile(
    os.getenv("TALOS_MICROPHONE_PROFILE", "respeaker")
)
WAKE_WORD = os.getenv("WAKE_WORD", "butler").lower()
# What to remove from the front of a transcript once the wake word is found.
#
# The wake word plus any punctuation is obvious. The possessive is not: asked
# for "butler, sleep mode", faster-whisper regularly writes "butler's sleep
# mode" -- the comma pause is short and the following word starts with an s.
# Slicing off only ``len(WAKE_WORD)`` and stripping " ,.:;!?-" left the
# apostrophe behind, so the command reaching the agent was "'s sleep mode",
# which matched no sleep phrase and was handed to the model as a garbled turn.
_WAKE_WORD_PREFIX = re.compile(
    rf"^{re.escape(WAKE_WORD)}(?:['’]s|s\b)?[\s,.:;!?\-]*"
)
WAKE_WORD_MODE = os.getenv("WAKE_WORD_MODE", "local").lower()
WAKE_WORD_MODEL = os.getenv("WAKE_WORD_MODEL", "base")
VOICE_SESSION_ID = os.getenv("TALOS_VOICE_SESSION", "voice-worker")
VOICE_AGENT_URL = os.getenv("TALOS_TEXT_AGENT_URL", "http://127.0.0.1:8420")
VOICE_AGENT_TOKEN = os.getenv("TALOS_TEXT_AGENT_TOKEN", os.getenv("TEXT_AGENT_API_TOKEN", ""))
VOICE_AGENT_TIMEOUT = float(os.getenv("TALOS_TEXT_AGENT_CLIENT_TIMEOUT", "30"))
VOICE_STREAMING = env_bool("TALOS_VOICE_STREAMING", True)
# Use one local STT pass to serve both wake-word and command (removes the remote
# whisper-1 round-trip and the separate local wake transcription).
LOCAL_STT_ENABLED = env_bool("TALOS_LOCAL_STT", True)
# Offline-first: a local STT failure is surfaced instead of silently sending
# recorded speech to a hosted transcription service. The old remote fallback is
# still available only when explicitly enabled.
REMOTE_STT_FALLBACK_ENABLED = env_bool("TALOS_REMOTE_STT_FALLBACK", False)
# The legacy voice command path calls the non-streaming hosted Responses lane.
# Keep that failover opt-in so an Ollama outage cannot silently move inference
# off the machine.
REMOTE_LLM_FALLBACK_ENABLED = env_bool("TALOS_REMOTE_LLM_FALLBACK", False)
# The LLM's GPU idles down within ~10 s, and the clock ramp is otherwise paid by
# the next turn. Transcription gives us a few hundred milliseconds of cover to
# absorb it, so the agent is nudged awake as soon as there is speech worth
# transcribing. Shares its flag with the agent-side implementation.
PRERAMP_ENABLED = env_bool("TALOS_AGENT_PRERAMP", True)
# Boot phrase: one synthetic command driven through the whole voice pipeline once
# listening is live, so the first real utterance does not pay the MCP tool build,
# cold prompt evaluation, and the first Polly connection all at once. It is a
# genuine turn (LLM, TTS, playback), so it is also the audible "system is up"
# signal. It runs against its own session id so the synthetic exchange never
# enters the conversation history real turns are built from.
BOOT_PHRASE_ENABLED = env_bool("TALOS_BOOT_PHRASE", True)
BOOT_PHRASE = os.getenv("TALOS_BOOT_PHRASE_TEXT", "").strip() or (
    'This is the initial boot phrase. Simply say: "Booting complete. Good Day Sir"'
)
BOOT_PHRASE_SESSION_ID = os.getenv("TALOS_BOOT_PHRASE_SESSION", "voice-boot-warmup")

audio_interface = pyaudio.PyAudio()
aws_access_key = os.getenv("AWS_ACCESS_KEY")
aws_secret_key = os.getenv("AWS_SECRET_KEY")
VOICE_AUDIO_OUTPUT_DEVICE_INDEX = os.getenv("TALOS_AUDIO_OUTPUT_DEVICE_INDEX")

_remote_stt_client = None
# Region is latency, not preference: every synthesis pays the round trip. Measured
# TLS connect from this box -- us-east-1 50 ms, us-east-2 88 ms, us-west-2 239 ms.
# Override if the box moves.
POLLY_REGION = os.getenv("TALOS_POLLY_REGION", "").strip() or "us-east-1"
# Timeouts are a stale-socket backstop, not a latency knob. An idle-dropped
# connection still looks established, so the write succeeds and we block on the
# read -- read_timeout is what unsticks it. Keep it well above real synthesis
# time (warm calls are 72-140 ms; long text is several hundred): botocore treats
# a read timeout as retryable, so a tight value aborts legitimate requests and
# pays a reconnect to retry them.
polly_client = boto3.client(
    "polly",
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name=POLLY_REGION,
    config=BotocoreConfig(
        tcp_keepalive=True,
        connect_timeout=2,
        read_timeout=3,
        retries={"max_attempts": 2, "mode": "standard"},
        max_pool_connections=4,
    ),
)

# Polly's pooled TLS connection dies after a couple of minutes idle -- most
# likely the local NAT mapping expiring rather than anything AWS does -- and the
# next real synthesis pays DNS + TCP + TLS again. Measured against
# logs/voice_benchmarks.csv: calls following >300 s idle averaged 718 ms versus
# 236 ms otherwise, and every one of the ten slowest calls in the corpus follows
# an idle gap of 167 s or more. tcp_keepalive above is not enough on its own,
# because Windows waits 2 h before the first probe. Real traffic on a short
# interval is what actually holds the mapping open.
#
# describe_voices, not synthesize_speech: it rides the same pooled connection to
# the same endpoint but is a metadata call, so the ping bills no characters.
POLLY_KEEPALIVE_SECONDS = float(os.getenv("TALOS_POLLY_KEEPALIVE_SECONDS", "60"))
_polly_keepalive_started = False
_polly_keepalive_lock = threading.Lock()

_wake_model = None
_wake_model_lock = threading.Lock()
_wake_infer_lock = threading.Lock()
_command_executor = ThreadPoolExecutor(max_workers=2)

# --------------------------------------------------------------------------- #
# Barge-in
# --------------------------------------------------------------------------- #
# The RMS-plus-ASR detector is experimental and known to accept false commands.
# Fail closed until the AEC/VAD replacement has passed the recorded-fixture and
# live-room acceptance gates in docs/voice/BARGE_IN_REDESIGN_PLAN.md.
BARGE_IN_ENABLED = env_bool("TALOS_BARGE_IN", False)
BARGE_IN_BACKEND = os.getenv("TALOS_BARGE_IN_BACKEND", "aec").strip().lower()
# Strict mode only accepts an interruption that names the wake word or is a bare
# "stop". This is partial containment for an explicitly accepted diagnostic
# run; it does not prevent false ducking.
BARGE_IN_REQUIRE_WAKE_WORD = env_bool("TALOS_BARGE_IN_REQUIRE_WAKE_WORD", False)
# Session the proactive lane records its turns under (see talos/router.py); an
# interrupted alert has to be corrected where it was written.
PROACTIVE_SESSION_ID = os.getenv("TALOS_VOICE_PROACTIVE_SESSION", "voice")
# Must stay above the agent's own TALOS_INTERRUPTION_LOCK_TIMEOUT, so a slow
# correction is waited out rather than abandoned while the server still applies
# it behind our back.
VOICE_INTERRUPT_TIMEOUT = env_float("TALOS_VOICE_INTERRUPT_TIMEOUT", 15.0)
# 20 ms of 16 kHz mono 16-bit PCM.
PLAYBACK_SLICE_BYTES = 640
# Audible acknowledgement that the wake word landed, played the moment the
# transcript is confirmed to address us. It exists to distinguish "did not hear
# you" from "still working", which is otherwise indistinguishable silence until
# the reply speaks.
WAKE_CUE_ENABLED = env_bool("TALOS_WAKE_CUE", True)
WAKE_CUE_DIR = Path(
    os.getenv("TALOS_WAKE_CUE_DIR")
    or (Path(__file__).resolve().parent / "assets")
)

_BARGE_IN_CONFIG = BargeInConfig(
    floor_rms=env_int("TALOS_BARGE_IN_FLOOR_RMS", 550),
    echo_margin=env_float("TALOS_BARGE_IN_ECHO_MARGIN", 2.2),
    output_delay_ms=env_float("TALOS_BARGE_IN_OUTPUT_DELAY_MS", 180.0),
    trigger_frames=env_int("TALOS_BARGE_IN_TRIGGER_FRAMES", 3),
    endpoint_silence_ms=env_float("TALOS_BARGE_IN_ENDPOINT_SILENCE_MS", 550.0),
    min_speech_ms=env_float("TALOS_BARGE_IN_MIN_SPEECH_MS", 260.0),
    duck_gain=env_float("TALOS_BARGE_IN_DUCK_GAIN", 0.12),
)
_barge_in = BargeInDetector(_BARGE_IN_CONFIG)
_duplex = None
_barge_vad_gate = None
_idle_vad_gate = None
_barge_probability_vad = None
_idle_probability_vad = None
_vad_lane = None
_barge_in_runtime_ready = False
_asr_queue = BoundedAsrQueue(capacity=env_int("TALOS_ASR_QUEUE_CAPACITY", 8))
_asr_worker_started = False
_asr_worker_lock = threading.Lock()
_stt_preload_started = False
_stt_preload_lock = threading.Lock()
_boot_phrase_started = False
_boot_phrase_lock = threading.Lock()

# --------------------------------------------------------------------------- #
# Endpointing
# --------------------------------------------------------------------------- #
# SpeechRecognition remains the production idle endpointer.  The independent
# idle Silero lane can only replace it after both an operator request and an
# explicit recorded-corpus acceptance acknowledgement.  The legacy setting is
# retained as a request alias, but cannot bypass the acceptance gate.
IDLE_VAD_ENDPOINTING_REQUESTED = env_bool(
    "TALOS_IDLE_VAD_ENDPOINTING",
    env_bool("TALOS_VAD_ENDPOINTING", False),
)
IDLE_VAD_CORPUS_ACCEPTED = env_bool("TALOS_IDLE_VAD_CORPUS_ACCEPTED", False)
IDLE_VAD_ENDPOINTING_ENABLED = (
    IDLE_VAD_ENDPOINTING_REQUESTED and IDLE_VAD_CORPUS_ACCEPTED
)
_vad_endpointing_active = False

_FIXTURE_RECORDING_ENABLED = env_bool(
    "TALOS_BARGE_IN_FIXTURE_RECORDING", False
)
_FIXTURE_DIRECTORY = Path(
    os.getenv(
        "TALOS_BARGE_IN_FIXTURE_DIR",
        str(Path(__file__).resolve().parents[2] / "logs" / "barge_in_fixtures"),
    )
)


def _build_fixture_recorder() -> SynchronizedFixtureRecorder:
    config = FixtureRecorderConfig(
        enabled=_FIXTURE_RECORDING_ENABLED,
        directory=_FIXTURE_DIRECTORY,
        max_duration_seconds=env_float(
            "TALOS_BARGE_IN_FIXTURE_MAX_SECONDS", 120.0
        ),
        max_pcm_bytes=env_int(
            "TALOS_BARGE_IN_FIXTURE_MAX_PCM_BYTES", 32 * 1024 * 1024
        ),
        max_sessions=env_int("TALOS_BARGE_IN_FIXTURE_MAX_SESSIONS", 5),
        queue_frames=env_int("TALOS_BARGE_IN_FIXTURE_QUEUE_FRAMES", 512),
    )
    try:
        return SynchronizedFixtureRecorder(config)
    except Exception as exc:
        # Recording is diagnostic-only. A bad directory or bound must not take
        # down ordinary wake-word operation, but the failed opt-in is visible.
        print(f"Barge-in fixture recording could not start: {exc}")
        return SynchronizedFixtureRecorder(FixtureRecorderConfig(enabled=False))


_fixture_recorder = _build_fixture_recorder()
# Only one utterance may hold the output device at a time. Also guarantees a
# cancelled reply has fully torn down before the reply that interrupted it opens
# its own stream.
_playback_lock = threading.RLock()
_silence_frames: dict[int, bytes] = {}


def _emit_voice_pipeline_event(benchmark, event: str, **fields) -> None:
    if benchmark is None:
        return
    snapshot = benchmark.pipeline_snapshot()
    emit_pipeline_event(
        request_id=benchmark.session_id,
        component="voice_worker",
        event=event,
        dimensions=snapshot.get("dimensions") or {},
        metrics=snapshot.get("latencies_ms") or {},
        **fields,
    )


def _get_remote_stt_client():
    global _remote_stt_client
    if _remote_stt_client is None:
        api_key = require_env("OPENAI_API_KEY")
        _remote_stt_client = openai.OpenAI(api_key=api_key)
    return _remote_stt_client


def _resolve_output_device_index():
    if not VOICE_AUDIO_OUTPUT_DEVICE_INDEX:
        return None
    try:
        return int(VOICE_AUDIO_OUTPUT_DEVICE_INDEX)
    except ValueError as exc:
        raise RuntimeError(
            "TALOS_AUDIO_OUTPUT_DEVICE_INDEX must be an integer if set."
        ) from exc


def _describe_output_device(device_index):
    try:
        if device_index is None:
            info = audio_interface.get_default_output_device_info()
        else:
            info = audio_interface.get_device_info_by_index(device_index)
    except Exception as exc:
        return f"unavailable ({exc})"

    name = info.get("name", "unknown")
    host_api = info.get("hostApi")
    max_channels = info.get("maxOutputChannels")
    return f"{name} (index={info.get('index')}, hostApi={host_api}, maxOutputChannels={max_channels})"


def _build_profile_microphone():
    """Build an explicit input source instead of trusting the Windows default."""

    from talos.voice.streaming.portaudio_input import PortAudioChannelMicrophone

    profile = MICROPHONE_PROFILE
    device_name = (
        os.getenv(profile.device_name_env, "").strip()
        or profile.default_device_name
    )
    source = PortAudioChannelMicrophone(
        name_contains=device_name,
        preferred_host_api=profile.preferred_host_api,
        sample_rate=16000,
        source_channels=profile.source_channels,
        selected_channel=profile.selected_channel,
    )
    print(
        f"Microphone profile '{profile.name}': matching '{device_name}', "
        f"16 kHz/{profile.source_channels}ch -> channel "
        f"{profile.selected_channel + 1}."
    )
    return source


def _open_speech_stream():
    """Open the 16 kHz mono output stream both spoken paths write into."""
    output_device_index = _resolve_output_device_index()
    print(
        "Opening playback stream on output device: "
        f"{_describe_output_device(output_device_index)}"
    )
    return audio_interface.open(
        format=audio_interface.get_format_from_width(2),
        channels=1,
        rate=16000,
        output=True,
        output_device_index=output_device_index,
    )


def _barge_in_available() -> bool:
    """Barge-in needs local STT to confirm an interruption.

    Without it the only evidence would be microphone energy, which in this room
    is mostly TALOS's own voice -- it would cut itself off constantly. If local
    STT is off or has failed, stay uninterruptible rather than unusable.
    """
    backend_ready = (
        BARGE_IN_BACKEND == "heuristic_diagnostic"
        or (
            BARGE_IN_BACKEND == "aec"
            and _barge_in_runtime_ready
            and _duplex is not None
            and _duplex.healthy
        )
    )
    return (
        BARGE_IN_ENABLED
        and backend_ready
        and LOCAL_STT_ENABLED
        and not _stt_unavailable
    )


def _arm_playback(session: SpeechSession) -> None:
    if _barge_in_available() and BARGE_IN_BACKEND == "heuristic_diagnostic":
        _barge_in.arm(session)


def _disarm_playback(session: SpeechSession) -> None:
    if BARGE_IN_ENABLED:
        _barge_in.disarm(session)
        if _duplex is not None:
            _duplex.finish_speaking()
        if _barge_vad_gate is not None:
            _barge_vad_gate.reset()
        _emit_barge_in_metrics(session)


def _emit_barge_in_metrics(session: SpeechSession) -> None:
    snapshot = _barge_in.metrics_snapshot()
    measurements = {}
    for name, summary in (snapshot.get("measurements") or {}).items():
        if not isinstance(summary, dict):
            continue
        for statistic in ("count", "min", "max", "average", "last"):
            measurements[f"{name}_{statistic}"] = summary.get(statistic)
    emit_pipeline_event(
        request_id=session.request_id or session.session_id or "voice",
        component="voice_worker",
        event="barge_in_metrics_snapshot",
        counters=snapshot.get("counters") or {},
        measurements=measurements,
        capabilities=snapshot.get("capabilities") or {},
    )


def _write_pcm(stream, session: SpeechSession, pcm: bytes) -> bool:
    """Play PCM in short slices, applying the current duck gain.

    A synthesized sentence is seconds of audio; handing it to ``stream.write``
    in one call would block for that whole time and could not be interrupted.
    Slicing also gives the detector a steady record of what we are emitting, so
    it can tell its own echo apart from the user.
    """
    for start in range(0, len(pcm), PLAYBACK_SLICE_BYTES):
        if session.is_cancelled:
            return False
        chunk = pcm[start : start + PLAYBACK_SLICE_BYTES]
        gain = session.gain
        if gain < 1.0:
            chunk = audioop.mul(chunk, 2, gain)
        stream.write(chunk)
        _fixture_recorder.record_render(chunk)
        if (
            _duplex is not None
            and _barge_in_available()
            and BARGE_IN_BACKEND == "aec"
        ):
            if _duplex.note_render_submitted(chunk):
                _barge_in.arm(session)
        elif BARGE_IN_BACKEND == "heuristic_diagnostic":
            _barge_in.observe_output(chunk)
    return not session.is_cancelled


_wake_cue_pcms: list[bytes] = []
_wake_cue_stream = None
_wake_cue_event = threading.Event()
_wake_cue_started = False
_wake_cue_lock = threading.Lock()


def _read_wake_cue(path: Path) -> tuple[tuple[int, int, int], bytes] | None:
    """Return ``((channels, width, rate), pcm)`` for one cue, or None if unusable."""
    try:
        with wave.open(str(path), "rb") as cue:
            spec = (cue.getnchannels(), cue.getsampwidth(), cue.getframerate())
            pcm = cue.readframes(cue.getnframes())
    except Exception as exc:
        print(f"Skipping unreadable wake cue '{path.name}': {exc}")
        return None
    if not pcm:
        print(f"Skipping empty wake cue '{path.name}'.")
        return None
    return spec, pcm


def _load_wake_cue() -> bool:
    """Decode every cue in the directory and open one shared output stream.

    Everything expensive happens here so the trigger path is a bare
    ``Event.set()``. The stream is deliberately separate from the reply's:
    ``_playback_lock`` is held for a whole reply, so a cue sharing it would
    either block the reply behind itself or, worse, queue behind a reply that is
    still unwinding and sound after the answer it was meant to precede.

    One stream serves every cue, so they must agree on format. The first file in
    sorted order sets it and any file that disagrees is skipped rather than
    played at the wrong rate, which would be audible garbage rather than a
    missing sound. Re-recording the whole set at a different rate or channel
    count still needs no code change.
    """
    global _wake_cue_pcms, _wake_cue_stream
    paths = sorted(WAKE_CUE_DIR.glob("*.wav"))
    if not paths:
        print(f"No wake cues found in '{WAKE_CUE_DIR}'; continuing without them.")
        return False

    spec: tuple[int, int, int] | None = None
    pcms: list[bytes] = []
    names: list[str] = []
    for path in paths:
        loaded = _read_wake_cue(path)
        if loaded is None:
            continue
        cue_spec, pcm = loaded
        if spec is None:
            spec = cue_spec
        elif cue_spec != spec:
            print(
                f"Skipping wake cue '{path.name}': {cue_spec[0]}ch/"
                f"{cue_spec[1] * 8}bit/{cue_spec[2]}Hz does not match the "
                f"{spec[0]}ch/{spec[1] * 8}bit/{spec[2]}Hz stream."
            )
            continue
        pcms.append(pcm)
        names.append(path.name)

    if spec is None or not pcms:
        print("No usable wake cues; continuing without them.")
        return False

    channels, sample_width, frame_rate = spec
    try:
        stream = audio_interface.open(
            format=audio_interface.get_format_from_width(sample_width),
            channels=channels,
            rate=frame_rate,
            output=True,
            output_device_index=_resolve_output_device_index(),
        )
    except Exception as exc:
        # A cue that will not play must never cost a turn; the pipeline is fully
        # functional without it.
        print(f"Wake cue output unavailable, continuing without it: {exc}")
        return False

    _wake_cue_pcms = pcms
    _wake_cue_stream = stream
    bytes_per_ms = frame_rate * channels * sample_width / 1000.0
    spread = "/".join(f"{len(pcm) / bytes_per_ms:.0f}" for pcm in pcms)
    print(
        f"Wake cues ready: {len(pcms)} of {len(paths)} "
        f"({', '.join(names)}) -- {spread} ms, "
        f"{channels}ch, {frame_rate} Hz"
    )
    return True


def _play_wake_cue() -> None:
    """Write the cue in slices, declaring it as render output while it plays.

    The declaration matters more than it looks. Idle capture is live the whole
    time this is playing, so without telling the duplex pipeline we are emitting
    audio, ``select_vad_lane`` keeps the idle Silero gate armed and any echo the
    system AEC fails to cancel can be captured as a fresh utterance. Marking it
    as render moves the lane to barge-in, which is where the echo-rejection
    machinery lives.
    """
    pcms = _wake_cue_pcms
    stream = _wake_cue_stream
    if not pcms or stream is None:
        return
    # Chosen here rather than in the trigger so the caller -- the serialized ASR
    # worker -- does no work at all beyond setting the event.
    pcm = random.choice(pcms)
    declared = _duplex is not None and _barge_in_available() and BARGE_IN_BACKEND == "aec"
    try:
        for start in range(0, len(pcm), PLAYBACK_SLICE_BYTES):
            chunk = pcm[start : start + PLAYBACK_SLICE_BYTES]
            stream.write(chunk)
            if declared:
                _duplex.note_render_submitted(chunk)
    except Exception as exc:
        print(f"Wake cue playback error: {exc}")
    finally:
        # Only hand back the speaking flag if the reply has not already claimed
        # it. Clearing it underneath a reply in progress would drop the lane back
        # to idle mid-utterance and arm the idle gate against our own voice.
        if declared and _barge_in.session is None:
            _duplex.finish_speaking()


def _wake_cue_loop() -> None:
    while True:
        _wake_cue_event.wait()
        _wake_cue_event.clear()
        _play_wake_cue()


def _ensure_wake_cue_worker() -> None:
    global _wake_cue_started
    if not WAKE_CUE_ENABLED:
        return
    with _wake_cue_lock:
        if _wake_cue_started:
            return
        if not _load_wake_cue():
            # Mark started anyway: a cue that failed to load will not load on the
            # next utterance either, and retrying per turn would put file I/O on
            # the latency path.
            _wake_cue_started = True
            return
        threading.Thread(
            target=_wake_cue_loop,
            name="talos-wake-cue",
            daemon=True,
        ).start()
        _wake_cue_started = True


def _signal_wake_cue() -> None:
    """Fire the cue without blocking the caller.

    Called from the single serialized ASR worker, where the command dispatch
    that follows is directly on the latency path and anything queued behind this
    turn -- including barge-in confirmations -- waits on it. So this must never
    touch the audio device itself: it sets an event and returns.
    """
    if _wake_cue_stream is not None:
        _wake_cue_event.set()


def _silence_like(frame: bytes) -> bytes:
    silence = _silence_frames.get(len(frame))
    if silence is None:
        silence = b"\x00" * len(frame)
        _silence_frames[len(frame)] = silence
    return silence


class _MicrophoneTap:
    """Wraps the recognizer's audio stream to give barge-in the raw frames.

    Two jobs. It feeds every frame to the detector while TALOS is speaking, and
    it hands the background recognizer silence for those frames. The second part
    matters as much as the first: ``Recognizer.listen`` only delivers a phrase
    after 0.6 s of quiet, so unbroken echo would keep it mid-phrase for the whole
    reply and the interruption would surface seconds late as one echo-and-user
    blob. Muting it also drops the wasted transcription passes TALOS currently
    runs on its own voice.

    When nothing is playing the tap is completely transparent, which is what
    keeps the normal wake-word path unchanged.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def read(self, size):
        frame = self._inner.read(size)
        _fixture_recorder.record_capture(frame)
        if not _barge_in.should_mute_recognizer():
            return frame
        try:
            captured = _barge_in.observe_input(frame)
        except Exception as exc:
            print(f"Barge-in detector error: {exc}")
            return frame
        if captured:
            try:
                _confirm_barge_in(captured)
            except Exception as exc:
                print(f"Barge-in confirmation error: {exc}")
                _barge_in.resume_after_rejection()
        return _silence_like(frame)

    def close(self):
        self._inner.close()


class _TappedMicrophone(sr.Microphone):
    def __enter__(self):
        super().__enter__()
        self.stream = _MicrophoneTap(self.stream)
        return self


def _confirm_barge_in(
    captured_pcm: bytes,
    expected_session: SpeechSession | None = None,
    vad_evidence: dict[str, float] | None = None,
) -> None:
    """Decide whether a captured burst was really the user talking over us.

    Runs inline on the listener thread so speech-to-text stays single-threaded,
    as it already is for wake-word capture. The reply is ducked (not stopped)
    while this runs, so rejecting costs the user a moment of quieter audio
    rather than a truncated answer.
    """
    from talos.voice.backends.base import AudioChunk

    session = _barge_in.session
    if (
        session is None
        or session.is_cancelled
        or (expected_session is not None and session is not expected_session)
    ):
        _barge_in.record_rejection("stale_session")
        return

    started = time.perf_counter()
    try:
        backend = _get_stt_backend()
        transcribe = (
            getattr(backend, "transcribe_barge_in")
            if BARGE_IN_BACKEND == "aec"
            and hasattr(backend, "transcribe_barge_in")
            else backend.transcribe
        )
        result = transcribe(AudioChunk(pcm=captured_pcm, sample_rate=16000))
        transcript = (result.text or "").strip()
    except Exception as exc:
        # Cannot confirm, so do not stop: an unverified cut is worse than a
        # missed one. Playback resumes at full volume.
        print(f"Barge-in transcription failed: {exc}")
        _barge_in.record_rejection(
            "asr_error",
            asr_latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
        _barge_in.resume_after_rejection()
        return

    decision_ms = round((time.perf_counter() - started) * 1000.0, 1)
    asr_confidence = (
        float(result.confidence) if result.confidence is not None else None
    )
    if (
        result.duration_seconds is not None
        and result.duration_seconds
        < env_float("TALOS_BARGE_IN_ASR_MIN_DURATION_SECONDS", 0.16)
    ):
        _barge_in.record_rejection("insufficient_speech", asr_latency_ms=decision_ms)
        _barge_in.resume_after_rejection()
        return
    if (
        result.average_log_probability is not None
        and result.average_log_probability
        < env_float("TALOS_BARGE_IN_ASR_MIN_AVG_LOGPROB", -1.2)
    ):
        _barge_in.record_rejection("low_asr_quality", asr_latency_ms=decision_ms)
        _barge_in.resume_after_rejection()
        return
    if (
        result.no_speech_probability is not None
        and result.no_speech_probability
        > env_float("TALOS_BARGE_IN_ASR_MAX_NO_SPEECH_PROBABILITY", 0.6)
    ):
        _barge_in.record_rejection("no_speech", asr_latency_ms=decision_ms)
        _barge_in.resume_after_rejection()
        return
    decision = barge_in_module.classify_barge_in(
        transcript,
        session.spoken_text,
        wake_word=WAKE_WORD,
        require_wake_word=BARGE_IN_REQUIRE_WAKE_WORD,
        reject_echo=BARGE_IN_BACKEND == "heuristic_diagnostic",
    )
    if not decision.accepted:
        _barge_in.record_rejection(
            decision.reason,
            asr_latency_ms=decision_ms,
            asr_confidence=asr_confidence,
        )
        print(
            f"Barge-in rejected ({decision.reason}) after {decision_ms} ms: "
            f"'{transcript}'"
        )
        emit_pipeline_event(
            request_id=session.request_id or session.session_id or "voice",
            component="voice_worker",
            event="barge_in_candidate_rejected",
            reason=decision.reason,
            asr_latency_ms=decision_ms,
            asr_confidence=asr_confidence,
            transcript_chars=len(transcript),
        )
        _barge_in.resume_after_rejection()
        return

    _accept_barge_in(
        session,
        transcript,
        decision,
        decision_ms,
        asr_confidence=asr_confidence,
    )


def _accept_barge_in(
    session,
    transcript,
    decision,
    decision_ms: float,
    *,
    asr_confidence: float | None = None,
) -> None:
    spoken = session.spoken_text
    # Stops audio within one 20 ms slice; everything after this is bookkeeping.
    session.cancel("barge_in")
    _barge_in.record_acceptance(
        asr_latency_ms=decision_ms,
        asr_confidence=asr_confidence,
    )
    _barge_in.disarm(session)
    print(f"Barge-in accepted after {decision_ms} ms: '{transcript}'")
    emit_pipeline_event(
        request_id=session.request_id or session.session_id or "voice",
        component="voice_worker",
        event="barge_in_accepted",
        barge_in_decision_ms=decision_ms,
        asr_confidence=asr_confidence,
        spoken_chars=len(spoken),
        transcript_chars=len(transcript),
    )

    command = "" if decision.is_stop_request else decision.command
    if decision.is_stop_request:
        # "stop" asks for silence, not for an answer about stopping.
        print("Barge-in was a stop request; not starting a new turn.")

    # Reporting and re-dispatch happen on one background thread, in that order,
    # and off the listener thread so the microphone keeps being read. The order
    # matters: both take the agent's per-session conversation lock, and a
    # follow-up that got there first would have the correction land on its own
    # reply instead of the interrupted one.
    threading.Thread(
        target=_report_then_redispatch,
        args=(session, spoken, transcript, command),
        name="talos-barge-in-report",
        daemon=True,
    ).start()


def _report_then_redispatch(
    session: SpeechSession, spoken_text: str, transcript: str, command: str
) -> None:
    try:
        send_interrupt(
            session_id=session.session_id or VOICE_SESSION_ID,
            request_id=session.request_id,
            spoken_text=spoken_text,
            base_url=VOICE_AGENT_URL,
            token=VOICE_AGENT_TOKEN,
            timeout=VOICE_INTERRUPT_TIMEOUT,
        )
    except Exception as exc:
        # The correction is best-effort; losing it must not also lose the command
        # the user just spoke.
        print(f"Could not report the interruption to the agent: {exc}")

    if not command:
        return
    benchmark = VoiceBenchmarkSession(wake_word=WAKE_WORD, wake_word_mode=WAKE_WORD_MODE)
    benchmark.mark_wake_word_detected()
    awareness_signals.record_presence(
        modality="wake_word", confidence=0.95, detail="barge_in", force=True
    )
    benchmark.set_transcript(transcript.lower())
    benchmark.set_command(command)
    benchmark.set_dimension("barge_in", True)
    benchmark.add_note("Command spoken over a reply that was in progress.")
    _command_executor.submit(handle_command, command, benchmark)


def _get_wake_model():
    global _wake_model
    if _wake_model is None:
        with _wake_model_lock:
            if _wake_model is None:
                print(f"Loading local wake-word model: {WAKE_WORD_MODEL}")
                _wake_model = whisper.load_model(WAKE_WORD_MODEL)
    return _wake_model


def _local_wake_word_detect(audio_data):
    if WAKE_WORD_MODE != "local":
        return True

    try:
        model = _get_wake_model()
        raw_audio = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        if not raw_audio:
            return False

        audio = np.frombuffer(raw_audio, np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return False

        with _wake_infer_lock:
            result = model.transcribe(
                audio,
                language="en",
                task="transcribe",
                fp16=False,
                temperature=0,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )

        text = (result.get("text") or "").strip().lower()
        print(f"Local wake check: '{text}'")
        return WAKE_WORD in text
    except Exception as exc:
        print(f"Local wake-word detection error: {exc}")
        return True


def _extract_transcription_text(transcription_result):
    if isinstance(transcription_result, str):
        return transcription_result
    text = getattr(transcription_result, "text", None)
    if text is not None:
        return text
    if isinstance(transcription_result, dict):
        return transcription_result.get("text", "")
    return ""


def _extract_transcription_words(transcription_result):
    words = getattr(transcription_result, "words", None)
    if words is not None:
        return words
    if isinstance(transcription_result, dict):
        return transcription_result.get("words")
    return None


def play_audio(filename, benchmark=None):
    """Play a fully rendered WAV file.

    Used only by the opt-in non-streaming fallback path. Not barge-in aware: the
    text was synthesized in one piece, so there is no way to say which part of it
    the user had heard when they cut in, and recording a guess would be worse
    than recording nothing.
    """
    try:
        chunk = 1024
        output_device_index = _resolve_output_device_index()
        if benchmark:
            benchmark.mark_stage("audio_open_start")
        with _playback_lock, wave.open(filename, "rb") as wf:
            print(f"Opening playback stream for '{filename}' using output device: {_describe_output_device(output_device_index)}")
            stream = audio_interface.open(
                format=audio_interface.get_format_from_width(wf.getsampwidth()),
                channels=wf.getnchannels(),
                rate=wf.getframerate(),
                output=True,
                output_device_index=output_device_index,
            )
            if benchmark:
                benchmark.mark_stage("audio_stream_ready")
            data = wf.readframes(chunk)
            first_chunk = True
            while data:
                stream.write(data)
                if benchmark and first_chunk:
                    # Record the timestamp live (in-memory, cheap). The CSV flush
                    # is deferred to after playback so disk I/O stays off the
                    # audio hot path.
                    benchmark.mark_stage("first_audio")
                    first_chunk = False
                data = wf.readframes(chunk)
            stream.stop_stream()
            stream.close()
            print(f"Finished playback for '{filename}'.")
            if benchmark:
                benchmark.mark_stage("pipeline_done")
                _emit_voice_pipeline_event(benchmark, "voice_pipeline_completed")

        time.sleep(0.2)
        os.remove(filename)
        print(f"Removed temporary audio file '{filename}'.")
    except Exception as exc:
        if benchmark:
            benchmark.add_error(f"Audio playback error: {exc}")
            benchmark.emit_summary_once("audio_playback_error")
            benchmark.mark_stage("pipeline_done")
            _emit_voice_pipeline_event(
                benchmark,
                "voice_pipeline_failed",
                error_type=type(exc).__name__,
            )
        print(f"Error in play_audio: {exc}")


_stt_backend = None
_stt_backend_lock = threading.Lock()
_stt_unavailable = False


def _get_stt_backend():
    global _stt_backend
    if _stt_backend is None:
        with _stt_backend_lock:
            if _stt_backend is None:
                from talos.voice.backends.factory import get_stt_backend
                _stt_backend = get_stt_backend()
    return _stt_backend


def _preload_stt_backend() -> None:
    try:
        backend = _get_stt_backend()
        preload = getattr(backend, "preload", None)
        if not callable(preload):
            return
        load_ms = preload()
        print(f"Local STT model ready (startup preload={load_ms:.1f} ms).")
    except Exception as exc:
        # Do not permanently disable STT for a startup race (for example, a GPU
        # service becoming ready slightly later).  The first utterance retries
        # through the existing explicit failure/fallback policy.
        print(f"Local STT startup preload failed; first utterance will retry: {exc}")


def _ensure_stt_preload_worker() -> None:
    global _stt_preload_started
    if not LOCAL_STT_ENABLED:
        return
    with _stt_preload_lock:
        if _stt_preload_started:
            return
        threading.Thread(
            target=_preload_stt_backend,
            name="talos-stt-preload",
            daemon=True,
        ).start()
        _stt_preload_started = True


def _run_boot_phrase() -> None:
    try:
        started = time.perf_counter()
        # No benchmark session: this is a synthetic turn, and letting it write a
        # row would skew the voice-latency corpus with a deliberately cold one.
        handle_command(BOOT_PHRASE, session_id=BOOT_PHRASE_SESSION_ID)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        print(f"Boot phrase complete in {elapsed_ms:.1f} ms; pipeline is warm.")
    except Exception as exc:
        # Warming is never a precondition for listening. A failure here (model
        # server still starting, AWS not reachable) leaves the first real
        # utterance exactly where it was before.
        print(f"Boot phrase failed; first utterance pays full latency: {exc}")


def _ensure_boot_phrase_worker() -> None:
    global _boot_phrase_started
    if not BOOT_PHRASE_ENABLED:
        return
    with _boot_phrase_lock:
        if _boot_phrase_started:
            return
        threading.Thread(
            target=_run_boot_phrase,
            name="talos-boot-phrase",
            daemon=True,
        ).start()
        _boot_phrase_started = True


def _run_polly_keepalive() -> None:
    """Hold the pooled Polly connection open so no real turn pays a reconnect."""
    while True:
        time.sleep(POLLY_KEEPALIVE_SECONDS)
        try:
            # LanguageCode only to keep the response small; the reply is discarded.
            polly_client.describe_voices(LanguageCode="en-GB")
        except Exception:
            # Never let a ping failure kill the thread or surface to the user. A
            # dropped ping just means the next one reconnects; worst case a real
            # synthesis pays the same cold path it paid before this existed.
            pass


def _ensure_polly_keepalive_worker() -> None:
    global _polly_keepalive_started
    if POLLY_KEEPALIVE_SECONDS <= 0:
        return
    with _polly_keepalive_lock:
        if _polly_keepalive_started:
            return
        threading.Thread(
            target=_run_polly_keepalive,
            name="talos-polly-keepalive",
            daemon=True,
        ).start()
        _polly_keepalive_started = True


def _transcribe_local(audio_data, benchmark):
    """Single local STT pass; the transcript serves both wake and command."""
    from talos.voice.backends.base import AudioChunk

    raw = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
    backend = _get_stt_backend()
    benchmark.set_dimension("stt_backend", "local:faster_whisper")
    benchmark.mark_stage("stt_send")
    result = backend.transcribe(AudioChunk(pcm=raw, sample_rate=16000))
    benchmark.mark_stage("stt_done")
    model_load_ms = getattr(backend, "last_model_load_ms", None)
    if model_load_ms is not None:
        benchmark.set_metric("stt_model_load_ms", model_load_ms)
    _emit_voice_pipeline_event(
        benchmark,
        "stt_completed",
        backend="local:faster_whisper",
        model=getattr(backend, "model_size", "unknown"),
        model_load_ms=model_load_ms,
        model_preloaded=getattr(backend, "last_model_preloaded", None),
    )
    return result.text


def _transcribe_remote_with_wake_gate(audio_data, benchmark):
    """Legacy path: cheap local wake gate, then remote whisper-1 transcription.

    Returns the transcript text, or ``None`` if the wake gate rejected the clip
    (already logged/emitted).
    """
    benchmark.set_dimension("stt_backend", "hosted:openai-whisper-1")
    if WAKE_WORD_MODE == "local":
        benchmark.mark_stage("local_wake_send")
        wake_detected = _local_wake_word_detect(audio_data)
        benchmark.mark_stage("local_wake_done")
    else:
        wake_detected = True

    if not wake_detected:
        print("Wake word not detected locally; skipping Whisper API call.")
        benchmark.add_note("Local wake-word check rejected the clip before remote STT.")
        benchmark.emit_summary_once("wake_word_rejected")
        return None

    wav_bytes = audio_data.get_wav_data()
    audio_file = io.BytesIO(wav_bytes)
    audio_file.name = "speech.wav"

    benchmark.mark_stage("stt_send")
    whisper_result = _get_remote_stt_client().audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
        response_format="verbose_json",
        language="en",
        temperature=0,
        timestamp_granularities=["word"],
    )
    benchmark.mark_stage("stt_done")
    _emit_voice_pipeline_event(
        benchmark,
        "stt_completed",
        backend="hosted:openai-whisper-1",
        fallback_used=bool(LOCAL_STT_ENABLED),
    )

    text = _extract_transcription_text(whisper_result).strip().lower()
    if text:
        benchmark.note_wake_word_offsets(_extract_transcription_words(whisper_result))
    return text


def _request_llm_preramp() -> None:
    """Nudge the agent's GPU awake while this clip is still being transcribed.

    Fired before STT rather than after the wake word is confirmed, because by the
    time the transcript exists the window this is trying to use has already
    passed. That means it also fires for speech that turns out not to be
    addressed to us; the request is a single token against an already-cached
    prompt, and the agent debounces repeats, so the cost of guessing wrong is far
    below the latency it removes when the guess is right.

    Always off the caller's thread: this runs on the audio path.
    """
    if not PRERAMP_ENABLED:
        return
    threading.Thread(
        target=send_prewarm,
        kwargs={"base_url": VOICE_AGENT_URL, "token": VOICE_AGENT_TOKEN},
        name="talos-voice-preramp",
        daemon=True,
    ).start()


def _acquire_transcript(audio_data, benchmark):
    """Return the transcript text (or ``None`` if the clip was already handled).

    Prefers the single local STT pass. A remote whisper-1 fallback is used only
    when ``TALOS_REMOTE_STT_FALLBACK=1`` is explicitly configured.
    """
    global _stt_unavailable
    if LOCAL_STT_ENABLED and not _stt_unavailable:
        try:
            return _transcribe_local(audio_data, benchmark)
        except Exception as exc:
            print(f"Local STT failed: {exc}")
            benchmark.add_error(f"Local STT error: {exc}")
            _stt_unavailable = True
            if not REMOTE_STT_FALLBACK_ENABLED:
                raise RuntimeError(
                    "Local STT is unavailable and remote STT fallback is disabled."
                ) from exc
            print("Falling back to explicitly enabled remote STT.")
    if not REMOTE_STT_FALLBACK_ENABLED:
        raise RuntimeError(
            "Local STT is disabled or unavailable and remote STT fallback is disabled."
        )
    return _transcribe_remote_with_wake_gate(audio_data, benchmark)


def recognition_callback(recognizer, audio_data):
    """Enqueue an idle command without blocking microphone consumption."""
    queued = _asr_queue.put_nowait(
        kind="idle",
        payload=audio_data,
        priority=AsrPriority.IDLE_COMMAND,
    )
    if not queued:
        print("Dropping idle utterance: bounded ASR queue is full.")


def _process_recognition_audio(audio_data):
    print("Recognition callback triggered.")
    benchmark = VoiceBenchmarkSession(wake_word=WAKE_WORD, wake_word_mode=WAKE_WORD_MODE)
    try:
        print("Trying recognition with Whisper...")
        raw_audio = audio_data.get_raw_data()
        sample_width = audio_data.sample_width or 2
        sample_rate = audio_data.sample_rate or 16000

        rms = audioop.rms(raw_audio, sample_width)
        duration = len(raw_audio) / float(sample_rate * sample_width) if sample_rate and sample_width else 0
        benchmark.note_recording_ready(duration)
        benchmark.set_metric("input_rms", rms)
        if rms < 300 or duration < 0.35:
            print(f"Skipping low-energy audio (rms={rms}, dur={duration:.2f}s)")
            benchmark.add_note("Skipped low-energy or too-short audio clip.")
            benchmark.emit_summary_once("discarded_audio")
            return

        # Past the energy gate this is real speech, so a turn is plausibly a few
        # hundred milliseconds away. Start the GPU ramp under STT.
        _request_llm_preramp()

        transcript = _acquire_transcript(audio_data, benchmark)
        if transcript is None:
            return

        text_spoken = transcript.strip().lower()
        if not text_spoken:
            print("No transcription returned.")
            benchmark.add_note("STT returned an empty transcript.")
            benchmark.emit_summary_once("empty_transcript")
            return

        benchmark.set_transcript(text_spoken)
        print(f"User said: {text_spoken}")

        if text_spoken.startswith(WAKE_WORD):
            # Intentional, wake-word-directed interaction: this is the only path
            # (besides barge-in) that should ever be benchmarked/logged.
            # Acknowledge audibly before anything else on this branch, so the
            # cue is not queued behind the dispatch it is meant to announce.
            _signal_wake_cue()
            benchmark.mark_wake_word_detected()
            # A wake word is a timestamped observation that a person is in the
            # room, from a source of known reliability -- structurally the same
            # kind of fact as a pin status, and previously discarded. force=True
            # because a deliberate wake is always worth recording, even inside
            # the presence rate-limit window.
            awareness_signals.record_presence(
                modality="wake_word", confidence=0.95, force=True
            )
            command = _WAKE_WORD_PREFIX.sub("", text_spoken, count=1).strip()
            print(f"Command received: {command}")
            if command:
                benchmark.set_command(command)
                _command_executor.submit(handle_command, command, benchmark)
                return

            benchmark.add_note("Wake word detected but no command followed it.")
            benchmark.emit_summary_once("wake_word_without_command")
            return

        # Speech the system overheard but that was never directed at it. Not an
        # interaction -- deliberately not logged (filtered by _is_meaningful).
        benchmark.add_note("Transcript did not begin with the configured wake word.")
        benchmark.emit_summary_once("wake_word_missing_in_transcript")
    except sr.UnknownValueError:
        print("Could not understand the audio.")
        benchmark.add_error("Speech recognition callback could not understand the audio.")
        benchmark.emit_summary_once("speech_recognition_unknown_value")
    except sr.RequestError as exc:
        print(f"Speech Recognition API error: {exc}")
        benchmark.add_error(f"Speech recognition request error: {exc}")
        benchmark.emit_summary_once("speech_recognition_request_error")
    except Exception as exc:
        print(f"Unexpected Error: {exc}")
        benchmark.add_error(f"Recognition callback error: {exc}")
        benchmark.emit_summary_once("recognition_callback_error")


def speak_text(text: str) -> None:
    """Vocalize already-composed text through the same Polly + audio-output path
    used for spoken replies, WITHOUT invoking the LLM.

    Proactive speech (reminders, awareness alerts, scheduled reports) is phrased
    upstream in the main-agent router and handed here only to be heard, so the
    system can speak without the user prompting it. Serialized on a lock so two
    utterances never fight over the output device, and interruptible on the same
    terms as a reply -- an alert the user does not want to sit through is exactly
    the case barge-in exists for.
    """
    text = " ".join((text or "").split())
    if not text:
        return
    session = SpeechSession(
        session_id=PROACTIVE_SESSION_ID,
        duck_gain=_BARGE_IN_CONFIG.duck_gain,
    )
    with _playback_lock:
        print("Speaking proactively.")
        stream = _open_speech_stream()

        def synth(chunk):
            with contextlib.closing(
                polly_client.synthesize_speech(
                    VoiceId="Brian",
                    OutputFormat="pcm",
                    SampleRate="16000",
                    Text=chunk,
                    Engine="neural",
                ).get("AudioStream")
            ) as audio_stream:
                return [audio_stream.read()]

        def sink(pcm):
            return _write_pcm(stream, session, pcm)

        _arm_playback(session)
        try:
            StreamingSpeaker(
                synth,
                sink,
                on_chunk_playing=session.note_playing,
                on_chunk_partial=session.note_partial,
                should_stop=lambda: session.is_cancelled,
            ).speak_stream([text])
        finally:
            _disarm_playback(session)
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
    if session.is_cancelled:
        print("Proactive speech was interrupted by the user.")


def run_speak_server(port: int | None = None):
    """Serve a loopback endpoint the rest of TALOS uses to make the assistant
    speak proactively: ``POST /speak {"text": "..."}`` enqueues one utterance on
    the voice worker (the only process with TTS + the audio device). Returns the
    already-serving ``ThreadingHTTPServer`` so the caller can shut it down.
    """
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if port is None:
        port = int(os.getenv("TALOS_VOICE_SPEAK_PORT", "8610"))

    class _SpeakHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/speak":
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                text = str(body.get("text", "")).strip()
            except Exception:
                self.send_response(400)
                self.end_headers()
                return
            if text:
                # Off the HTTP thread: return immediately, play in the background.
                _command_executor.submit(speak_text, text)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):  # silence default request logging
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), _SpeakHandler)
    threading.Thread(
        target=server.serve_forever, name="talos-voice-speak", daemon=True
    ).start()
    print(f"Voice speak server listening on 127.0.0.1:{port}")
    return server


def _asr_loop() -> None:
    while True:
        item = _asr_queue.get()
        if item.kind == "idle":
            try:
                _process_recognition_audio(item.payload)
            except Exception as exc:
                print(f"Idle recognition worker error: {exc}")
            continue

        if item.kind == "barge_in":
            session, pcm, evidence = item.payload
            try:
                _confirm_barge_in(pcm, session, evidence)
            except Exception as exc:
                print(f"Barge-in confirmation error: {exc}")
                _barge_in.record_rejection("asr_error")
                _barge_in.resume_after_rejection()


def _ensure_asr_worker() -> None:
    global _asr_worker_started
    with _asr_worker_lock:
        if _asr_worker_started:
            return
        threading.Thread(
            target=_asr_loop,
            name="talos-asr",
            daemon=True,
        ).start()
        _asr_worker_started = True


def _dispatch_idle_utterance(pcm: bytes) -> None:
    recognition_callback(None, sr.AudioData(pcm, 16000, 2))


def _on_barge_vad_candidate(probability: float) -> None:
    session = _barge_in.session
    if session is None or session.is_cancelled:
        return
    session.duck()
    _barge_in.record_vad_candidate(probability)


def _on_idle_vad_utterance(pcm: bytes, evidence: dict[str, float]) -> None:
    _dispatch_idle_utterance(pcm)


def _on_barge_vad_utterance(pcm: bytes, evidence: dict[str, float]) -> None:
    session = _barge_in.session
    if session is None or session.is_cancelled:
        # Drop it. This gate's audio only carries ``preroll_ms`` of lead-in, the
        # tail of which is already inside the first word, so transcribing it as
        # an ordinary command clips the wake word ("butler" -> "but there").
        # Idle turns belong to SpeechRecognition, which keeps its own lead-in;
        # it is listening on the same source and will capture the utterance.
        _barge_in.record_rejection("stale_session")
        return
    _barge_in.record_vad_capture(evidence)
    queued = _asr_queue.put_nowait(
        kind="barge_in",
        payload=(session, pcm, evidence),
        priority=AsrPriority.BARGE_IN_CONFIRMATION,
    )
    if not queued:
        _barge_in.record_rejection("asr_queue_overflow")
        session.unduck()


def _on_clean_aec_frame(frame: bytes) -> None:
    global _vad_lane
    _fixture_recorder.record_capture(frame)
    if not frame:
        return
    _barge_in.record_aec_residual(float(audioop.rms(frame, 2)))
    if _barge_vad_gate is None:
        return

    speaking = _duplex is not None and _duplex.speaking
    desired_lane = select_vad_lane(
        _vad_lane,
        barge_pending=_barge_vad_gate.has_pending_speech,
        idle_pending=(
            _idle_vad_gate is not None and _idle_vad_gate.has_pending_speech
        ),
        speaking=speaking,
        idle_enabled=_vad_endpointing_active,
    )

    if desired_lane != _vad_lane:
        if desired_lane == "barge_in":
            _barge_vad_gate.reset()
            _barge_probability_vad.reset()
        elif desired_lane == "idle":
            if _idle_vad_gate is None or _idle_probability_vad is None:
                return
            # Keep the lead-in this gate buffered while barge-in owned
            # detection. A follow-up command spoken shortly after a reply ends
            # arrives right at this switch, and against an emptied ring its
            # capture would start at the trigger frame -- mid-wake-word, which
            # fails the startswith(WAKE_WORD) check and silently drops the turn.
            _idle_vad_gate.reset(preserve_preroll=True)
            _idle_probability_vad.reset()
        else:
            if _vad_lane == "barge_in":
                _barge_vad_gate.reset()
                _barge_probability_vad.reset()
            elif _vad_lane == "idle":
                if _idle_vad_gate is not None:
                    _idle_vad_gate.reset()
                if _idle_probability_vad is not None:
                    _idle_probability_vad.reset()
        _vad_lane = desired_lane

    if _vad_lane == "barge_in":
        _barge_vad_gate.observe(frame)
        if _idle_vad_gate is not None:
            # Buffering costs no inference, so the idle ring stays warm through
            # the whole reply and is full the moment the lane switches back.
            _idle_vad_gate.buffer_only(frame)
    elif _vad_lane == "idle" and _idle_vad_gate is not None:
        _idle_vad_gate.observe(frame)


def _start_aec_duplex():
    global _duplex, _barge_vad_gate, _idle_vad_gate
    global _barge_probability_vad, _idle_probability_vad
    global _barge_in_runtime_ready, _vad_endpointing_active, _vad_lane
    from talos.voice.streaming.duplex import build_windows_duplex_pipeline
    from talos.voice.streaming.vad import (
        BargeInVadGate,
        SileroProbabilityVAD,
        VadGateConfig,
    )
    from talos.voice.streaming.windows_audio import (
        WindowsAudioEndpoints,
        get_default_windows_audio_endpoints,
    )

    if not MICROPHONE_PROFILE.windows_aec:
        raise RuntimeError(
            f"Windows AEC is not enabled for microphone profile "
            f"'{MICROPHONE_PROFILE.name}'."
        )
    defaults = get_default_windows_audio_endpoints()
    capture_id = (
        os.getenv(MICROPHONE_PROFILE.capture_endpoint_env, "").strip()
        or os.getenv("TALOS_AUDIO_CAPTURE_ENDPOINT_ID", "").strip()
    )
    render_id = os.getenv("TALOS_AUDIO_RENDER_ENDPOINT_ID", "").strip()
    if not capture_id or not render_id:
        raise RuntimeError(
            "AEC barge-in requires pinned TALOS_AUDIO_CAPTURE_ENDPOINT_ID and "
            "TALOS_AUDIO_RENDER_ENDPOINT_ID values."
        )
    endpoints = WindowsAudioEndpoints(capture_id=capture_id, render_id=render_id)
    if endpoints != defaults:
        raise RuntimeError(
            "Pinned Windows audio endpoint identity does not match the current "
            "default capture/render pair."
        )
    _barge_probability_vad = SileroProbabilityVAD()
    _barge_vad_gate = BargeInVadGate(
        _barge_probability_vad.probability,
        _on_barge_vad_utterance,
        on_candidate=_on_barge_vad_candidate,
        config=VadGateConfig(
            start_probability=env_float("TALOS_BARGE_IN_VAD_START", 0.65),
            end_probability=env_float("TALOS_BARGE_IN_VAD_END", 0.35),
            start_frames=env_int("TALOS_BARGE_IN_VAD_START_FRAMES", 3),
            end_silence_ms=env_float("TALOS_BARGE_IN_ENDPOINT_SILENCE_MS", 480.0),
            min_speech_ms=env_float("TALOS_BARGE_IN_MIN_SPEECH_MS", 160.0),
            preroll_ms=env_float("TALOS_BARGE_IN_PREROLL_MS", 500.0),
            max_utterance_ms=env_float(
                "TALOS_BARGE_IN_MAX_UTTERANCE_MS", 9000.0
            ),
        ),
    )
    if IDLE_VAD_ENDPOINTING_ENABLED:
        _idle_probability_vad = SileroProbabilityVAD()
        _idle_vad_gate = BargeInVadGate(
            _idle_probability_vad.probability,
            _on_idle_vad_utterance,
            config=VadGateConfig(
                start_probability=env_float("TALOS_IDLE_VAD_START", 0.50),
                end_probability=env_float("TALOS_IDLE_VAD_END", 0.35),
                start_frames=env_int("TALOS_IDLE_VAD_START_FRAMES", 2),
                end_silence_ms=env_float(
                    "TALOS_IDLE_VAD_ENDPOINT_SILENCE_MS", 480.0
                ),
                min_speech_ms=env_float("TALOS_IDLE_VAD_MIN_SPEECH_MS", 160.0),
                preroll_ms=env_float("TALOS_IDLE_VAD_PREROLL_MS", 640.0),
                max_utterance_ms=env_float(
                    "TALOS_IDLE_VAD_MAX_UTTERANCE_MS",
                    env_float("TALOS_VAD_MAX_UTTERANCE_MS", 15000.0),
                ),
            ),
        )
    else:
        _idle_probability_vad = None
        _idle_vad_gate = None
    _duplex = build_windows_duplex_pipeline(
        endpoints,
        on_clean_frame=_on_clean_aec_frame,
    )
    _duplex.start()
    _barge_in_runtime_ready = True
    _vad_lane = None
    _vad_endpointing_active = IDLE_VAD_ENDPOINTING_ENABLED
    print("AEC duplex capture started on the pinned Windows endpoints.")
    return _duplex


def run_voice_recognition():
    global _barge_in_runtime_ready, _vad_endpointing_active
    _ensure_asr_worker()
    _ensure_stt_preload_worker()
    # Ahead of both return paths below, and before any listening starts: the
    # decode and the device open are the whole cost of the cue, and neither
    # belongs on the first utterance. Opening the stream emits no audio, so it
    # cannot colour the ambient-noise calibration further down.
    _ensure_wake_cue_worker()
    if IDLE_VAD_ENDPOINTING_REQUESTED and not IDLE_VAD_CORPUS_ACCEPTED:
        print(
            "Idle VAD endpointing was requested but remains disabled until "
            "TALOS_IDLE_VAD_CORPUS_ACCEPTED=1. Using SpeechRecognition."
        )
    if not MICROPHONE_PROFILE.windows_aec:
        _barge_in_runtime_ready = False
        _vad_endpointing_active = False
        if BARGE_IN_ENABLED:
            print(
                f"Barge-in is disabled for microphone profile "
                f"'{MICROPHONE_PROFILE.name}': its far-end/AEC contract has not "
                "been measured on this host. Run "
                "'python -m talos.voice.diagnostics.windows_aec_probe'."
            )
        mic = _build_profile_microphone()
    elif BARGE_IN_ENABLED and BARGE_IN_BACKEND == "aec":
        try:
            pipeline = _start_aec_duplex()
            if _vad_endpointing_active:
                # The independently configured idle Silero gate has passed its
                # explicit corpus-acceptance gate, so a second endpointer would
                # dispatch every command twice.
                print(
                    "Corpus-accepted idle VAD endpointing active; SpeechRecognition "
                    "listening is not used."
                )
                _ensure_polly_keepalive_worker()
                _ensure_boot_phrase_worker()
                return _stop_vad_endpointing

            from talos.voice.streaming.duplex import DuplexRecognizerAudioSource

            mic = DuplexRecognizerAudioSource(pipeline)
        except Exception as exc:
            _barge_in_runtime_ready = False
            _vad_endpointing_active = False
            print(
                "AEC barge-in is unavailable and has been disabled; ordinary "
                f"wake-word capture remains active: {exc}"
            )
            mic = _build_profile_microphone()
    elif (
        BARGE_IN_ENABLED and BARGE_IN_BACKEND == "heuristic_diagnostic"
    ) or _FIXTURE_RECORDING_ENABLED:
        mic = _TappedMicrophone()
    else:
        mic = _build_profile_microphone()
    print("Microphone initialized.")
    with mic as source:
        r.adjust_for_ambient_noise(source, duration=1.0)
        r.dynamic_energy_threshold = False
        r.energy_threshold = resolve_energy_threshold(
            MICROPHONE_PROFILE,
            os.getenv("TALOS_RECOGNIZER_ENERGY_THRESHOLD"),
            r.energy_threshold,
        )
        r.pause_threshold = 0.6
        r.non_speaking_duration = 0.3
        device = getattr(source, "device", None)
        description = (
            f"{device.name} via {device.host_api} (index={device.index})"
            if device is not None
            else MICROPHONE_PROFILE.label
        )
        print(
            f"Adjusted for ambient noise on {description}; "
            f"energy threshold={r.energy_threshold:.1f}."
        )

    stop_listening = r.listen_in_background(mic, recognition_callback)
    print("Background listening started.")
    # Deliberately after adjust_for_ambient_noise: boot audio playing during that
    # calibration window would be measured as room noise and raise the energy
    # threshold for the whole run.
    _ensure_polly_keepalive_worker()
    _ensure_boot_phrase_worker()
    return stop_listening


def _stop_vad_endpointing(wait_for_stop: bool = True) -> None:
    """Stopper matching the ``listen_in_background`` contract the worker uses.

    Capture itself is owned by the duplex pipeline, so stopping the listener
    only means detaching the gate from it; ``shutdown`` tears the rest down.
    """
    global _vad_endpointing_active, _vad_lane
    _vad_endpointing_active = False
    if _idle_vad_gate is not None:
        _idle_vad_gate.reset()
    if _idle_probability_vad is not None:
        _idle_probability_vad.reset()
    _vad_lane = None


def handle_command(command, benchmark=None, session_id=None):
    print(f"Handling voice command: {command}")
    if benchmark:
        benchmark.mark_stage("command_start")
        benchmark.set_dimension("llm_fallback_used", False)
    try:
        if VOICE_STREAMING:
            progress = {"spoke": False}
            try:
                _handle_command_streaming(command, benchmark, progress, session_id)
                return
            except Exception as exc:
                print(f"Streaming voice path error: {exc}")
                if benchmark:
                    benchmark.add_error(f"Streaming path error: {exc}")
                    _emit_voice_pipeline_event(
                        benchmark,
                        "streaming_path_failed",
                        error_type=type(exc).__name__,
                    )
                if progress["spoke"]:
                    # Audio already started; do not replay via the fallback path.
                    if benchmark:
                        benchmark.emit_summary_once("voice_worker_error")
                        benchmark.mark_stage("pipeline_done")
                        _emit_voice_pipeline_event(
                            benchmark,
                            "voice_pipeline_failed",
                            error_type=type(exc).__name__,
                        )
                    return
                if not REMOTE_LLM_FALLBACK_ENABLED:
                    print("Remote LLM fallback is disabled; voice command failed locally.")
                    if benchmark:
                        benchmark.emit_summary_once("voice_worker_error")
                        benchmark.mark_stage("pipeline_done")
                        _emit_voice_pipeline_event(
                            benchmark,
                            "voice_pipeline_failed",
                            error_type=type(exc).__name__,
                        )
                    return
                print("Falling back to explicitly enabled non-streaming voice path.")
                if benchmark:
                    benchmark.set_dimension("llm_fallback_used", True)
        _handle_command_legacy(command, benchmark, session_id)
    finally:
        # Guarantee the command is recorded exactly once. emit_summary_once is
        # idempotent, so a prior "first_audio"/error emit wins; this only catches
        # commands that completed without ever producing audio (empty/tool-only
        # responses, silent failures) which previously went unlogged.
        if benchmark:
            benchmark.emit_summary_once("command_complete")


def _handle_command_streaming(command, benchmark, progress, session_id=None):
    """Stream the agent response and speak it sentence-by-sentence as it arrives."""
    agent_session_id = session_id or VOICE_SESSION_ID
    session = SpeechSession(
        request_id=benchmark.session_id if benchmark else None,
        command=command,
        session_id=agent_session_id,
        duck_gain=_BARGE_IN_CONFIG.duck_gain,
    )
    # Waits out a reply the user has just interrupted, so its output stream is
    # closed before this one opens. That reply is already unwinding.
    with _playback_lock:
        _speak_streamed_response(
            command, benchmark, progress, session, agent_session_id
        )


def _speak_streamed_response(
    command, benchmark, progress, session, agent_session_id=None
):
    if benchmark:
        benchmark.mark_stage("audio_open_start")
    stream = _open_speech_stream()
    if benchmark:
        benchmark.mark_stage("audio_stream_ready")
    marks = {"polly": False}

    def synth(text):
        synth_started = time.perf_counter()
        first_synth = benchmark is not None and not marks["polly"]
        if benchmark and not marks["polly"]:
            marks["polly"] = True
            benchmark.mark_stage("polly_send")
        with contextlib.closing(
            polly_client.synthesize_speech(
                VoiceId="Brian",
                OutputFormat="pcm",
                SampleRate="16000",
                Text=text,
                Engine="neural",
            ).get("AudioStream")
        ) as audio_stream:
            pcm = audio_stream.read()
        if benchmark:
            duration_ms = round((time.perf_counter() - synth_started) * 1000.0, 1)
            benchmark.add_metric("polly_total_ms", duration_ms)
            benchmark.add_metric("polly_request_count", 1)
            if first_synth:
                # First speakable chunk synthesized -- boundary for the
                # first_synth_ms / first_audio_playback_ms breakdown.
                benchmark.mark_stage("first_synth_done")
            if marks["polly"]:
                benchmark.mark_stage("polly_done")
        return [pcm]

    def sink(pcm):
        write_started = time.perf_counter()
        completed = _write_pcm(stream, session, pcm)
        if benchmark:
            benchmark.add_metric(
                "audio_write_total_ms",
                round((time.perf_counter() - write_started) * 1000.0, 1),
            )
        return completed

    def on_first_audio():
        progress["spoke"] = True
        if benchmark:
            # Timestamp live; defer the CSV flush to handle_command's finally
            # (after speak_stream returns) to keep disk I/O off the audio path.
            benchmark.mark_stage("first_audio")

    def tracked_deltas():
        if benchmark:
            benchmark.mark_stage("llm_send")
        seen_first = False
        try:
            for delta in stream_message(
                command,
                session_id=agent_session_id or VOICE_SESSION_ID,
                source="voice",
                base_url=VOICE_AGENT_URL,
                token=VOICE_AGENT_TOKEN,
                timeout=None,
                request_id=benchmark.session_id if benchmark else None,
                telemetry_callback=(
                    benchmark.apply_pipeline_telemetry if benchmark else None
                ),
            ):
                if not seen_first:
                    seen_first = True
                    if benchmark:
                        benchmark.mark_stage("llm_first_done")
                yield delta
        finally:
            if benchmark:
                benchmark.mark_stage("llm_done")

    speaker = StreamingSpeaker(
        synth,
        sink,
        on_first_audio=on_first_audio,
        # What the user actually heard, which lags what the model has produced.
        on_chunk_playing=session.note_playing,
        on_chunk_partial=session.note_partial,
        should_stop=lambda: session.is_cancelled,
    )
    _arm_playback(session)
    try:
        response_text = speaker.speak_stream(tracked_deltas())
    finally:
        _disarm_playback(session)
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass

    if session.is_cancelled:
        spoken = session.spoken_text
        print(f"Reply interrupted by the user after: {spoken!r}")
        if benchmark:
            benchmark.set_response_text(spoken)
            benchmark.set_dimension("interrupted", True)
            benchmark.add_note("The user interrupted this reply.")
            benchmark.emit_summary_once("barge_in")
            benchmark.mark_stage("pipeline_done")
            _emit_voice_pipeline_event(benchmark, "voice_pipeline_interrupted")
        return

    if benchmark:
        benchmark.set_response_text(response_text)
        benchmark.mark_stage("pipeline_done")
        _emit_voice_pipeline_event(benchmark, "voice_pipeline_completed")
    print(f"Bot response: {response_text}")


def _handle_command_legacy(command, benchmark=None, session_id=None):
    print(f"Handling voice command (legacy path): {command}")
    try:
        if benchmark:
            benchmark.set_dimension("llm_backend", "openai_responses")
            benchmark.set_dimension("llm_backend_location", "hosted")
            benchmark.set_dimension("llm_model", os.getenv("OPENAI_VOICE_MODEL", "gpt-4o-mini"))
            benchmark.mark_stage("llm_send")
        response_text = send_message(
            command,
            session_id=session_id or VOICE_SESSION_ID,
            source="voice",
            base_url=VOICE_AGENT_URL,
            token=VOICE_AGENT_TOKEN,
            timeout=VOICE_AGENT_TIMEOUT,
        )
        if benchmark:
            benchmark.mark_stage("llm_done")
            benchmark.set_response_text(response_text)
        print(f"Bot response: {response_text}")

        if benchmark:
            benchmark.mark_stage("polly_send")
        with contextlib.closing(
            polly_client.synthesize_speech(
                VoiceId="Brian",
                OutputFormat="pcm",
                SampleRate="16000",
                Text=response_text,
                Engine="neural",
            ).get("AudioStream")
        ) as stream:
            pcm_data = stream.read()
            if benchmark:
                benchmark.mark_stage("polly_done")
            print("Speech synthesized successfully.")

        with tempfile.NamedTemporaryFile(prefix="talos_speech_", suffix=".wav", delete=False) as tmp_file:
            filename = tmp_file.name

        with wave.open(filename, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframesraw(pcm_data)

        print(f"Wrote synthesized speech to temporary file '{filename}'.")
        audio_thread = threading.Thread(target=play_audio, args=(filename, benchmark))
        print("Starting audio playback thread.")
        audio_thread.start()
        # Wait for playback to finish so the benchmark flush (in handle_command's
        # finally) lands after the interaction, not mid-playback. The command
        # future is fire-and-forget, so blocking this worker here is harmless.
        audio_thread.join()
    except openai.OpenAIError as exc:
        if benchmark:
            benchmark.add_error(f"OpenAI API error: {exc}")
            benchmark.emit_summary_once("openai_error")
            benchmark.mark_stage("pipeline_done")
            _emit_voice_pipeline_event(
                benchmark, "voice_pipeline_failed", error_type=type(exc).__name__
            )
        print(f"OpenAI API Error: {exc}")
    except boto3.exceptions.Boto3Error as exc:
        if benchmark:
            benchmark.add_error(f"AWS Polly error: {exc}")
            benchmark.emit_summary_once("polly_error")
            benchmark.mark_stage("pipeline_done")
            _emit_voice_pipeline_event(
                benchmark, "voice_pipeline_failed", error_type=type(exc).__name__
            )
        print(f"AWS Polly Error: {exc}")
    except Exception as exc:
        if benchmark:
            benchmark.add_error(f"Voice worker command error: {exc}")
            benchmark.emit_summary_once("voice_worker_error")
            benchmark.mark_stage("pipeline_done")
            _emit_voice_pipeline_event(
                benchmark, "voice_pipeline_failed", error_type=type(exc).__name__
            )
        print(f"Unexpected Error: {exc}")


def shutdown() -> None:
    _fixture_recorder.close()
    _command_executor.shutdown(wait=False, cancel_futures=True)
    audio_interface.terminate()
