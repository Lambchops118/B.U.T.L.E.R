"""Experimental first-pass barge-in detector and downstream decision helpers.

The room microphone and speakers are open at the same time, and this stack has
no acoustic echo canceller.  The energy heuristic below cannot reliably
distinguish a person from loudspeaker echo and is disabled by default.  It is
retained only for bounded diagnostic comparison while the AEC/VAD redesign in
``docs/voice/BARGE_IN_REDESIGN_PLAN.md`` is implemented.

The legacy path has two stages:

1. :class:`BargeInDetector` runs on every captured microphone frame (~64 ms) and
   only while TALOS is speaking. On a sustained rise above the expected echo
   level it ducks the output and starts capturing. This is a candidate signal,
   not evidence that speech occurred.

2. The caller transcribes that recording with the local STT backend and confirms
   with :func:`looks_like_echo` / :func:`is_stop_command`. This text policy can
   reject obvious echo but is not a reliable speech detector; Whisper may
   generate plausible text for non-speech.

The expected echo level is learned rather than configured: ``echo_peak`` is a
decaying maximum of the microphone RMS seen while speaking and not capturing,
which makes it a per-room, per-volume constant that survives across utterances.
When the aligned output frame was silent (between sentences, or while ducked),
the threshold drops to the plain ambient floor.

This module is intentionally free of PyAudio / SpeechRecognition / model
imports so the state machine can be unit-tested on any platform, matching the
rest of :mod:`talos.voice.streaming`.
"""

from __future__ import annotations

import audioop
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from talos.voice.streaming.barge_in_observability import BargeInMetrics


# Phrases that mean "stop talking" and nothing else. These end the current
# utterance without spending an LLM turn on a reply the user did not ask for.
_STOP_PHRASES = frozenset(
    {
        "stop",
        "stop it",
        "stop please",
        "please stop",
        "stop talking",
        "shut up",
        "be quiet",
        "quiet",
        "quiet please",
        "silence",
        "hush",
        "shush",
        "enough",
        "thats enough",
        "ok stop",
        "okay stop",
        "never mind",
        "nevermind",
        "forget it",
        "cancel that",
    }
)

_PUNCTUATION = ".,!?;:()[]-_"
# Dropped rather than spaced, so "that's" stays one token.
_ELIDED = "'’\""

# What the assistant's own memory of the turn ends with once the user cuts in.
INTERRUPTION_MARKER = (
    "... [The user interrupted here. Nothing after this point was heard.]"
)
NOTHING_HEARD_MARKER = "[The user interrupted before anything was heard.]"
PARTIALLY_HEARD_MARKER = "[The current sentence was only partially heard.]"


@dataclass(frozen=True)
class BargeInConfig:
    """Tuning for :class:`BargeInDetector`.

    Defaults target a desk/room setup with the speakers a metre or two from the
    microphone at conversational volume. Every field is overridable from
    ``settings.env`` (see ``TALOS_BARGE_IN_*``).
    """

    sample_rate: int = 16000
    sample_width: int = 2
    # Absolute ambient gate, used whenever we are not currently making noise.
    # Matches the recognizer's own energy_threshold so both agree on "silence".
    floor_rms: int = 550
    # How far above the learned echo level a frame must sit to count as the user.
    echo_margin: float = 2.2
    # Per-frame decay of the learned echo level (~ -0.5% per 64 ms frame).
    echo_peak_decay: float = 0.995
    # Output RMS below which we treat ourselves as silent.
    output_silence_rms: int = 120
    # Speaker buffer + acoustic flight time between writing PCM and hearing it.
    output_delay_ms: float = 180.0
    output_jitter_ms: float = 120.0
    # Consecutive hot frames required before ducking (~190 ms at 64 ms/frame).
    trigger_frames: int = 3
    # Frames of our own audio measured before the gate goes live, so the very
    # first syllables of a reply cannot be mistaken for the user in a room whose
    # echo level has not been observed yet (~0.5 s at 64 ms/frame).
    calibration_frames: int = 8
    # Silence that ends the captured utterance.
    endpoint_silence_ms: float = 550.0
    # Captures with less voiced audio than this are transients, not speech.
    min_speech_ms: float = 260.0
    max_capture_ms: float = 9000.0
    # Audio retained before the trigger so the utterance onset is not clipped.
    preroll_ms: float = 400.0
    # Settling time after a rejected capture, so the un-duck transient and the
    # tail of our own sentence cannot immediately re-trigger.
    refractory_ms: float = 500.0
    duck_gain: float = 0.12


class SpeechSession:
    """One in-progress spoken utterance, cancellable from another thread.

    The playback path reads :attr:`gain` and :attr:`is_cancelled` between small
    PCM slices; the microphone path calls :meth:`duck` / :meth:`unduck` /
    :meth:`cancel`. :attr:`spoken_text` is the authoritative record of what the
    user actually *heard* -- speech synthesis runs ahead of playback, so the text
    already streamed from the model overstates it by whole sentences.
    """

    def __init__(
        self,
        *,
        request_id: str | None = None,
        command: str | None = None,
        session_id: str | None = None,
        duck_gain: float = BargeInConfig.duck_gain,
    ) -> None:
        self.request_id = request_id
        self.command = command
        self.session_id = session_id
        self._duck_gain = float(duck_gain)
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._cancel_reason: str | None = None
        self._ducked = False
        self._spoken_parts: list[str] = []
        self._partial = False

    # -- what the user heard ------------------------------------------------ #
    def note_playing(self, text: str) -> None:
        """Record a text chunk only after all of its PCM was emitted."""
        if not text:
            return
        with self._lock:
            self._spoken_parts.append(text)
            self._partial = False

    def note_partial(self, text: str = "") -> None:
        """Mark an interrupted in-flight chunk without claiming future words."""
        with self._lock:
            self._partial = True

    @property
    def spoken_text(self) -> str:
        with self._lock:
            parts = [part.strip() for part in self._spoken_parts if part.strip()]
            if self._partial:
                parts.append(PARTIALLY_HEARD_MARKER)
            return " ".join(parts)

    # -- ducking ------------------------------------------------------------ #
    def duck(self) -> None:
        with self._lock:
            self._ducked = True

    def unduck(self) -> None:
        with self._lock:
            self._ducked = False

    @property
    def is_ducked(self) -> bool:
        with self._lock:
            return self._ducked

    @property
    def gain(self) -> float:
        with self._lock:
            return self._duck_gain if self._ducked else 1.0

    # -- cancellation ------------------------------------------------------- #
    def cancel(self, reason: str = "barge_in") -> None:
        with self._lock:
            if self._cancel_reason is None:
                self._cancel_reason = reason
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def cancel_reason(self) -> str | None:
        with self._lock:
            return self._cancel_reason


class BargeInDetector:
    """Frame-level gate deciding when the user has started talking over us.

    ``arm``/``disarm`` bracket one spoken utterance. While armed,
    :meth:`observe_input` is fed every microphone frame and
    :meth:`observe_output` every PCM slice actually written to the speakers.
    """

    _IDLE = "idle"
    _LISTENING = "listening"
    _CAPTURING = "capturing"

    def __init__(
        self,
        config: BargeInConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        metrics: BargeInMetrics | None = None,
    ) -> None:
        self.config = config or BargeInConfig()
        self._clock = clock
        self._metrics = metrics or BargeInMetrics()
        self._lock = threading.Lock()
        self._session: SpeechSession | None = None
        self._state = self._IDLE
        # (timestamp, rms) of PCM handed to the speakers, for echo alignment.
        self._output: deque[tuple[float, float]] = deque()
        self._preroll: deque[bytes] = deque()
        self._preroll_ms = 0.0
        self._capture: list[bytes] = []
        self._capture_ms = 0.0
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._hot_frames = 0
        self._hot_started_at: float | None = None
        self._refractory_until = 0.0
        self._calibration_frames = 0
        # Learned per-room echo level; deliberately NOT reset between utterances.
        self._echo_peak = 0.0

    # -- lifecycle ---------------------------------------------------------- #
    def arm(self, session: SpeechSession) -> None:
        with self._lock:
            self._session = session
            self._state = self._LISTENING
            self._reset_capture_locked()
            self._output.clear()
            self._refractory_until = 0.0
            self._calibration_frames = 0

    def disarm(self, session: SpeechSession | None = None) -> None:
        """Stop watching. Pass the session to disarm only if it is still current,
        so a reply tearing down cannot silence the reply that replaced it."""
        with self._lock:
            if session is not None and self._session is not session:
                return
            self._session = None
            self._state = self._IDLE
            self._reset_capture_locked()
            self._output.clear()

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._state != self._IDLE

    @property
    def session(self) -> SpeechSession | None:
        with self._lock:
            return self._session

    @property
    def echo_peak(self) -> float:
        with self._lock:
            return self._echo_peak

    def metrics_snapshot(self) -> dict[str, object]:
        """Return aggregate numeric evidence without transcripts or room audio."""
        return self._metrics.snapshot()

    def record_rejection(
        self,
        reason: str,
        *,
        asr_latency_ms: float | None = None,
        asr_confidence: float | None = None,
    ) -> None:
        """Record a confirmation-stage rejection using bounded reason labels."""
        self._metrics.candidate_rejected(
            reason,
            asr_latency_ms=asr_latency_ms,
            asr_confidence=asr_confidence,
        )

    def record_acceptance(
        self,
        *,
        asr_latency_ms: float,
        asr_confidence: float | None = None,
    ) -> None:
        self._metrics.accepted(
            asr_latency_ms=asr_latency_ms,
            asr_confidence=asr_confidence,
        )

    def record_vad_candidate(self, probability: float) -> None:
        self._metrics.vad_candidate_started(probability=probability)

    def record_vad_capture(self, evidence: dict[str, float]) -> None:
        self._metrics.vad_capture_completed(
            duration_ms=evidence["duration_ms"],
            speech_ms=evidence["speech_ms"],
            average_probability=evidence["average_probability"],
            max_probability=evidence["max_probability"],
        )

    def record_aec_residual(self, rms: float) -> None:
        self._metrics.observe_aec_residual_rms(rms)

    def should_mute_recognizer(self) -> bool:
        """True while barge-in owns the microphone.

        The background recognizer is fed silence for these frames so echo can
        never start a phrase in it; this detector is capturing instead.
        """
        return self.armed

    # -- signals ------------------------------------------------------------ #
    def observe_output(self, pcm: bytes) -> None:
        """Record PCM that was just written to the speakers."""
        if not pcm:
            return
        rms = float(audioop.rms(pcm, self.config.sample_width))
        now = self._clock()
        with self._lock:
            self._output.append((now, rms))
            cutoff = now - 3.0
            while self._output and self._output[0][0] < cutoff:
                self._output.popleft()

    def observe_input(self, frame: bytes) -> bytes | None:
        """Feed one microphone frame.

        Returns the captured utterance (16-bit mono PCM, pre-roll included) once
        the user has stopped speaking, otherwise ``None``. Ducking is applied to
        the session as a side effect at the moment of the trigger.
        """
        if not frame:
            return None
        config = self.config
        frame_ms = (
            len(frame) / float(config.sample_rate * config.sample_width) * 1000.0
        )
        now = self._clock()
        rms = float(audioop.rms(frame, config.sample_width))

        with self._lock:
            if self._state == self._IDLE or self._session is None:
                return None

            output_rms = self._aligned_output_rms_locked(now)
            self._metrics.observe_levels(capture_rms=rms, render_rms=output_rms)
            speaking = output_rms >= config.output_silence_rms
            if speaking:
                threshold = max(
                    float(config.floor_rms), self._echo_peak * config.echo_margin
                )
            else:
                threshold = float(config.floor_rms)

            if self._state == self._LISTENING:
                self._push_preroll_locked(frame, frame_ms)
                hot = rms > threshold

                if speaking and self._calibration_frames < config.calibration_frames:
                    # First frames of an utterance in a room we have not measured
                    # yet: everything we hear is our own voice by definition, so
                    # take it as the reference and refuse to trigger on it.
                    self._echo_peak = max(rms, self._echo_peak)
                    self._calibration_frames += 1
                    self._hot_frames = 0
                    self._hot_started_at = None
                    return None

                # Learn the echo level only from frames that look like echo, so
                # the user talking can never raise the bar against themselves.
                if speaking and not hot:
                    self._echo_peak = max(rms, self._echo_peak * config.echo_peak_decay)

                if now < self._refractory_until:
                    self._hot_frames = 0
                    self._hot_started_at = None
                    return None

                if hot:
                    if self._hot_frames == 0:
                        self._hot_started_at = now
                    self._hot_frames += 1
                else:
                    self._hot_frames = 0
                    self._hot_started_at = None
                if self._hot_frames >= config.trigger_frames:
                    hot_started_at = (
                        now if self._hot_started_at is None else self._hot_started_at
                    )
                    pause_latency_ms = (
                        max(0.0, now - hot_started_at) * 1000.0
                        + frame_ms
                    )
                    self._begin_capture_locked(
                        capture_rms=rms,
                        render_rms=output_rms,
                        threshold_rms=threshold,
                        pause_latency_ms=pause_latency_ms,
                    )
                return None

            # _CAPTURING
            self._capture.append(frame)
            self._capture_ms += frame_ms
            # A lower bar to keep counting speech than to start it, so normal
            # pauses inside a sentence do not end the capture early.
            if rms > threshold * 0.6:
                self._speech_ms += frame_ms
                self._silence_ms = 0.0
            else:
                self._silence_ms += frame_ms

            ended = self._silence_ms >= config.endpoint_silence_ms
            overlong = self._capture_ms >= config.max_capture_ms
            if not (ended or overlong):
                return None

            captured = b"".join(self._capture)
            capture_duration_ms = self._capture_ms
            heuristic_speech_ms = self._speech_ms
            enough_speech = self._speech_ms >= config.min_speech_ms
            self._state = self._LISTENING
            self._reset_capture_locked()
            self._metrics.capture_completed(
                capture_duration_ms=capture_duration_ms,
                heuristic_speech_ms=heuristic_speech_ms,
            )
            if not enough_speech:
                # A door slam or a cough. Hand nothing to STT and settle.
                self._metrics.candidate_rejected("insufficient_speech")
                self._refractory_until = now + config.refractory_ms / 1000.0
                session = self._session
                if session is not None:
                    session.unduck()
                return None
            return captured

    def resume_after_rejection(self) -> None:
        """Called when the confirm stage decided the capture was not the user."""
        session = self.session
        if session is not None:
            session.unduck()
        with self._lock:
            self._refractory_until = self._clock() + self.config.refractory_ms / 1000.0
            self._hot_frames = 0
            self._hot_started_at = None

    # -- internals ---------------------------------------------------------- #
    def _reset_capture_locked(self) -> None:
        self._preroll.clear()
        self._preroll_ms = 0.0
        self._capture = []
        self._capture_ms = 0.0
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._hot_frames = 0
        self._hot_started_at = None

    def _push_preroll_locked(self, frame: bytes, frame_ms: float) -> None:
        self._preroll.append(frame)
        self._preroll_ms += frame_ms
        while self._preroll and self._preroll_ms > self.config.preroll_ms:
            dropped = self._preroll.popleft()
            self._preroll_ms -= (
                len(dropped)
                / float(self.config.sample_rate * self.config.sample_width)
                * 1000.0
            )

    def _begin_capture_locked(
        self,
        *,
        capture_rms: float,
        render_rms: float,
        threshold_rms: float,
        pause_latency_ms: float,
    ) -> None:
        self._state = self._CAPTURING
        self._capture = list(self._preroll)
        self._capture_ms = self._preroll_ms
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._hot_frames = 0
        self._preroll.clear()
        self._preroll_ms = 0.0
        self._metrics.candidate_started(
            capture_rms=capture_rms,
            render_rms=render_rms,
            threshold_rms=threshold_rms,
            pause_latency_ms=pause_latency_ms,
        )
        session = self._session
        if session is not None:
            session.duck()

    def _aligned_output_rms_locked(self, now: float) -> float:
        """Loudest thing we sent to the speakers during the window this frame
        would have picked up, accounting for buffer + flight time."""
        config = self.config
        newest = now - (config.output_delay_ms - config.output_jitter_ms) / 1000.0
        oldest = now - (config.output_delay_ms + config.output_jitter_ms) / 1000.0
        best = 0.0
        for timestamp, rms in self._output:
            if oldest <= timestamp <= newest and rms > best:
                best = rms
        return best


# --------------------------------------------------------------------------- #
# Confirmation helpers (stage 2)
# --------------------------------------------------------------------------- #
def normalize_phrase(text: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    lowered = (text or "").lower()
    cleaned = "".join(
        "" if char in _ELIDED else " " if char in _PUNCTUATION else char
        for char in lowered
    )
    return " ".join(cleaned.split())


def is_stop_command(text: str) -> bool:
    """True when the utterance only asks TALOS to stop talking."""
    return normalize_phrase(text) in _STOP_PHRASES


def looks_like_echo(
    transcript: str,
    spoken_text: str,
    *,
    min_overlap: float = 0.6,
    tail_tokens: int = 60,
) -> bool:
    """True when the transcript is most likely our own voice coming back.

    Two signals: the transcript appearing verbatim inside what we just said, and
    a high token overlap with the tail of it. Single-token transcripts are left
    to the caller (``"stop"`` is a real command even if we also said "stop").
    """
    transcript_norm = normalize_phrase(transcript)
    spoken_norm = normalize_phrase(spoken_text)
    if not transcript_norm or not spoken_norm:
        return False

    transcript_tokens = transcript_norm.split()
    if len(transcript_tokens) < 2:
        return False

    if transcript_norm in spoken_norm:
        return True

    spoken_tokens = spoken_norm.split()[-max(1, tail_tokens):]
    spoken_set = set(spoken_tokens)
    matched = sum(1 for token in transcript_tokens if token in spoken_set)
    return (matched / len(transcript_tokens)) >= min_overlap


@dataclass(frozen=True)
class BargeInDecision:
    """Outcome of stage 2: is this really the user cutting in?"""

    accepted: bool
    reason: str
    is_stop_request: bool = False
    command: str = ""


def classify_barge_in(
    transcript: str,
    spoken_text: str,
    *,
    wake_word: str,
    require_wake_word: bool = False,
    reject_echo: bool = True,
) -> BargeInDecision:
    """Decide what a burst captured over our own speech actually was.

    Rejecting is cheap here -- the reply is only ducked at this point, so a wrong
    "no" costs a moment of quieter audio. Accepting stops the answer, so the bars
    are ordered from most to least certain: nothing said, our own voice coming
    back, and (in strict mode) speech that never names the wake word.

    ``require_wake_word`` is the fallback for a room where the speakers dominate
    the microphone badly enough that echo survives the checks above.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return BargeInDecision(False, "empty_transcript")

    if reject_echo and looks_like_echo(transcript, spoken_text):
        return BargeInDecision(False, "echo")

    stop_request = is_stop_command(transcript)
    normalized_wake = normalize_phrase(wake_word)
    names_wake_word = bool(normalized_wake) and normalized_wake in normalize_phrase(
        transcript
    )
    if require_wake_word and not (names_wake_word or stop_request):
        return BargeInDecision(False, "no_wake_word")

    return BargeInDecision(
        True,
        "accepted",
        is_stop_request=stop_request,
        command="" if stop_request else strip_wake_word(transcript, wake_word),
    )


def strip_wake_word(transcript: str, wake_word: str) -> str:
    """Drop a leading wake word so "butler, stop that" reaches the agent as a
    command rather than as an address."""
    lowered = (transcript or "").strip().lower()
    prefix = (wake_word or "").strip().lower()
    if prefix and lowered.startswith(prefix):
        return lowered[len(prefix) :].lstrip(" ,.:;!?-").strip()
    return lowered


def truncated_transcript(spoken_text: str) -> str:
    """What the assistant's turn becomes in memory once it was cut off."""
    spoken = " ".join((spoken_text or "").split())
    if not spoken:
        return NOTHING_HEARD_MARKER
    return f"{spoken.rstrip()} {INTERRUPTION_MARKER}"
