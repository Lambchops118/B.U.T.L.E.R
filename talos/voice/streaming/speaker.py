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
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Iterable, Iterator

from talos.voice.streaming.sentence_chunker import SentenceChunker

# Injected callables.
SynthFn = Callable[[str], Iterable[bytes]]  # text -> PCM chunks
SinkFn = Callable[[bytes], None]  # play/consume one PCM chunk

_CLOSE = object()  # queue sentinel


class StreamingSpeaker:
    def __init__(
        self,
        synth: SynthFn,
        sink: SinkFn,
        *,
        chunker: SentenceChunker | None = None,
        on_first_audio: Callable[[], None] | None = None,
        max_queue: int = 64,
    ) -> None:
        self._synth = synth
        self._sink = sink
        self._chunker = chunker or SentenceChunker()
        self._on_first_audio = on_first_audio
        self._max_queue = max_queue

    def speak_stream(self, text_deltas: Iterable[str]) -> str:
        """Speak a streamed response and return the full text that was spoken.

        Blocks until all audio has been played. Exceptions raised by the synth
        function or the sink propagate to the caller after workers are drained.
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
                    for pcm in self._synth(chunk):  # type: ignore[arg-type]
                        if pcm:
                            pcm_q.put(pcm)
            except BaseException as exc:  # noqa: BLE001 - surfaced to caller
                errors.append(exc)
            finally:
                pcm_q.put(_CLOSE)

        def playback_worker() -> None:
            try:
                while True:
                    pcm = pcm_q.get()
                    if pcm is _CLOSE:
                        break
                    if not first_audio_done.is_set():
                        first_audio_done.set()
                        if self._on_first_audio is not None:
                            self._on_first_audio()
                    self._sink(pcm)  # type: ignore[arg-type]
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
                if not delta:
                    continue
                full_text_parts.append(delta)
                for chunk in self._chunker.push(delta):
                    text_q.put(chunk)
            tail = self._chunker.flush()
            if tail:
                text_q.put(tail)
        finally:
            text_q.put(_CLOSE)
            synth_thread.join()
            playback_thread.join()

        if errors:
            raise errors[0]
        return "".join(full_text_parts).strip()


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
