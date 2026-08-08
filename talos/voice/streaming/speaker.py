"""Overlapped text-stream -> speech playback.

:class:`StreamingSpeaker` consumes a stream of LLM text deltas and starts audio
playback as soon as the first sentence is ready, while the model is still
generating and later sentences are still synthesizing. This is what collapses
perceived latency from "whole response" to "first sentence + first audio chunk".

Three stages run concurrently:

1. the caller's generator produces text deltas (LLM generation),
2. a synth worker turns completed sentence chunks into PCM (TTS),
3. a playback worker writes PCM to the audio sink.

The TTS function and the audio sink are injected, so the engine is fully
backend-agnostic (Polly today, local cloned voice later) and unit-testable, and
the same engine drives the room mic and, later, the phone (Twilio) transport.

Because those stages are overlapped, the text pulled from the model runs ahead of
the text actually leaving the speakers. ``on_chunk_playing`` reports the latter,
which is the only thing the user has really heard -- barge-in needs that
distinction to tell the model where it was cut off. ``should_stop`` lets a
barge-in unwind all three stages promptly instead of finishing a response nobody
is listening to any more.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Iterable, Iterator

from talos.voice.streaming.sentence_chunker import SentenceChunker

# Injected callables.
SynthFn = Callable[[str], Iterable[bytes]]  # text -> PCM chunks
SinkFn = Callable[[bytes], bool | None]  # False means playback cut this PCM short

_CLOSE = object()  # queue sentinel
_CHUNK_DONE = object()
_PUT_POLL_SECONDS = 0.05


class StreamingSpeaker:
    def __init__(
        self,
        synth: SynthFn,
        sink: SinkFn,
        *,
        chunker: SentenceChunker | None = None,
        on_first_audio: Callable[[], None] | None = None,
        on_chunk_playing: Callable[[str], None] | None = None,
        on_chunk_partial: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        max_queue: int = 64,
    ) -> None:
        self._synth = synth
        self._sink = sink
        self._chunker = chunker or SentenceChunker()
        self._on_first_audio = on_first_audio
        self._on_chunk_playing = on_chunk_playing
        self._on_chunk_partial = on_chunk_partial
        self._should_stop = should_stop
        self._max_queue = max_queue

    def _stopped(self) -> bool:
        return self._should_stop is not None and self._should_stop()

    def speak_stream(self, text_deltas: Iterable[str]) -> str:
        """Speak a streamed response and return the text that was consumed.

        Blocks until all audio has been played, or returns early once
        ``should_stop`` reports that the utterance has been abandoned. Exceptions
        raised by the synth function or the sink propagate to the caller after
        workers are drained.
        """
        text_q: "queue.Queue[object]" = queue.Queue(maxsize=self._max_queue)
        pcm_q: "queue.Queue[object]" = queue.Queue(maxsize=self._max_queue)
        errors: list[BaseException] = []
        first_audio_done = threading.Event()

        def synth_worker() -> None:
            try:
                while True:
                    chunk = text_q.get()
                    if chunk is _CLOSE:
                        break
                    if self._stopped():
                        # Do not spend another TTS request on audio nobody will
                        # hear; keep draining so the producer never blocks.
                        continue
                    for pcm in self._synth(chunk):  # type: ignore[arg-type]
                        if not pcm:
                            continue
                        if not self._put(pcm_q, (chunk, pcm)):
                            break
                    self._put(pcm_q, (_CHUNK_DONE, chunk))
            except BaseException as exc:  # noqa: BLE001 - surfaced to caller
                errors.append(exc)
            finally:
                pcm_q.put(_CLOSE)

        def playback_worker() -> None:
            playing: object = None
            partial_reported = False
            try:
                while True:
                    item = pcm_q.get()
                    if item is _CLOSE:
                        break
                    first, second = item  # type: ignore[misc]
                    if first is _CHUNK_DONE:
                        if (
                            playing == second
                            and not partial_reported
                            and not self._stopped()
                            and self._on_chunk_playing is not None
                        ):
                            self._on_chunk_playing(second)
                        playing = None
                        partial_reported = False
                        continue
                    chunk_text, pcm = first, second
                    if chunk_text != playing:
                        playing = chunk_text
                        partial_reported = False
                    if self._stopped():
                        if not partial_reported and self._on_chunk_partial is not None:
                            self._on_chunk_partial(chunk_text)
                            partial_reported = True
                        continue
                    if not first_audio_done.is_set():
                        first_audio_done.set()
                        if self._on_first_audio is not None:
                            self._on_first_audio()
                    completed = self._sink(pcm)
                    if completed is False or self._stopped():
                        if not partial_reported and self._on_chunk_partial is not None:
                            self._on_chunk_partial(chunk_text)
                            partial_reported = True
            except BaseException as exc:  # noqa: BLE001 - surfaced to caller
                errors.append(exc)
                # Drain remaining PCM so the synth worker never blocks on a full
                # queue after a playback failure.
                _drain_until_close(pcm_q)

        synth_thread = threading.Thread(target=synth_worker, name="talos-tts-synth", daemon=True)
        playback_thread = threading.Thread(target=playback_worker, name="talos-tts-play", daemon=True)
        synth_thread.start()
        playback_thread.start()

        full_text_parts: list[str] = []
        try:
            for delta in text_deltas:
                if self._stopped():
                    break
                if not delta:
                    continue
                full_text_parts.append(delta)
                for chunk in self._chunker.push(delta):
                    if not self._put(text_q, chunk):
                        break
            if not self._stopped():
                tail = self._chunker.flush()
                if tail:
                    self._put(text_q, tail)
        finally:
            # Abandoning the response also abandons the upstream generator, which
            # is what closes the SSE connection and stops the model generating.
            close = getattr(text_deltas, "close", None)
            if self._stopped() and callable(close):
                try:
                    close()
                except Exception:
                    pass
            while True:
                try:
                    text_q.put(_CLOSE, timeout=_PUT_POLL_SECONDS)
                    break
                except queue.Full:
                    if not synth_thread.is_alive():
                        break
            synth_thread.join()
            playback_thread.join()

        if errors:
            raise errors[0]
        return "".join(full_text_parts).strip()

    def _put(self, q: "queue.Queue[object]", item: object) -> bool:
        """Enqueue, giving up if the utterance is abandoned mid-wait.

        A blocking ``put`` on a full queue would otherwise keep a barge-in
        waiting on a consumer that has already stopped consuming.
        """
        while True:
            if self._stopped():
                return False
            try:
                q.put(item, timeout=_PUT_POLL_SECONDS)
                return True
            except queue.Full:
                continue


def _drain_until_close(q: "queue.Queue[object]") -> None:
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            return
        if item is _CLOSE:
            return


def collect_pcm(text_deltas: Iterable[str], synth: SynthFn, **kwargs) -> Iterator[bytes]:
    """Helper for non-realtime callers (e.g. the phone transport) that want the
    synthesized PCM as an iterator instead of pushing to an audio device."""
    out: "queue.Queue[bytes]" = queue.Queue()

    def sink(pcm: bytes) -> None:
        out.put(pcm)

    speaker = StreamingSpeaker(synth, sink, **kwargs)
    speaker.speak_stream(text_deltas)
    while True:
        try:
            yield out.get_nowait()
        except queue.Empty:
            return
