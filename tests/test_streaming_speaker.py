from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.voice.streaming.speaker import StreamingSpeaker
from talos.voice.streaming.sentence_chunker import SentenceChunker


class StreamingSpeakerTests(unittest.TestCase):
    def _synth(self, text):
        # One PCM "chunk" per sentence: encode the text so we can assert order.
        return [text.encode("utf-8")]

    def test_speaks_sentences_in_order_and_returns_full_text(self):
        played: list[bytes] = []
        speaker = StreamingSpeaker(self._synth, played.append, chunker=SentenceChunker(min_chars=1))

        full = speaker.speak_stream(iter(["Hello there. ", "How are you? ", "Bye."]))

        self.assertEqual(
            [p.decode("utf-8") for p in played],
            ["Hello there.", "How are you?", "Bye."],
        )
        self.assertEqual(full, "Hello there. How are you? Bye.")

    def test_first_audio_callback_fires_once_before_playback(self):
        calls: list[int] = []
        speaker = StreamingSpeaker(
            self._synth,
            lambda pcm: None,
            chunker=SentenceChunker(min_chars=1),
            on_first_audio=lambda: calls.append(1),
        )
        speaker.speak_stream(iter(["One. ", "Two. ", "Three."]))
        self.assertEqual(sum(calls), 1)

    def test_flush_tail_without_punctuation_is_spoken(self):
        played: list[bytes] = []
        speaker = StreamingSpeaker(self._synth, played.append, chunker=SentenceChunker(min_chars=1))
        speaker.speak_stream(iter(["no terminal punctuation"]))
        self.assertEqual([p.decode("utf-8") for p in played], ["no terminal punctuation"])

    def test_synth_error_propagates(self):
        def bad_synth(text):
            raise RuntimeError("tts exploded")

        speaker = StreamingSpeaker(bad_synth, lambda pcm: None, chunker=SentenceChunker(min_chars=1))
        with self.assertRaises(RuntimeError):
            speaker.speak_stream(iter(["Hello."]))

    def test_sink_error_propagates_and_does_not_deadlock(self):
        def bad_sink(pcm):
            raise RuntimeError("audio device gone")

        speaker = StreamingSpeaker(self._synth, bad_sink, chunker=SentenceChunker(min_chars=1))
        with self.assertRaises(RuntimeError):
            # Many chunks: playback fails on the first, synth must not block on a
            # full PCM queue.
            speaker.speak_stream(iter([f"Sentence {i}. " for i in range(50)]))

    def test_synth_and_playback_overlap(self):
        # Playback of chunk N should be able to run while synth of chunk N+1 runs.
        order: list[str] = []
        lock = threading.Lock()

        def slow_synth(text):
            with lock:
                order.append(f"synth:{text}")
            return [text.encode("utf-8")]

        def sink(pcm):
            with lock:
                order.append(f"play:{pcm.decode('utf-8')}")

        speaker = StreamingSpeaker(slow_synth, sink, chunker=SentenceChunker(min_chars=1))
        speaker.speak_stream(iter(["One. ", "Two. ", "Three."]))

        # Every synth precedes its own playback.
        self.assertLess(order.index("synth:One."), order.index("play:One."))
        self.assertLess(order.index("synth:Two."), order.index("play:Two."))
        # Playback happened for all three, in order.
        plays = [o for o in order if o.startswith("play:")]
        self.assertEqual(plays, ["play:One.", "play:Two.", "play:Three."])


class StreamingSpeakerInterruptionTests(unittest.TestCase):
    """Barge-in: what the user heard, and unwinding once they cut in."""

    def _synth(self, text):
        return [text.encode("utf-8")]

    def test_chunk_playing_reports_only_what_reached_the_sink(self):
        playing: list[str] = []
        speaker = StreamingSpeaker(
            self._synth,
            lambda pcm: None,
            chunker=SentenceChunker(min_chars=1),
            on_chunk_playing=playing.append,
        )
        speaker.speak_stream(iter(["One. ", "Two. ", "Three."]))
        self.assertEqual(playing, ["One.", "Two.", "Three."])

    def test_chunk_playing_fires_once_per_chunk_not_per_pcm_buffer(self):
        playing: list[str] = []
        speaker = StreamingSpeaker(
            lambda text: [b"a", b"b", b"c"],
            lambda pcm: None,
            chunker=SentenceChunker(min_chars=1),
            on_chunk_playing=playing.append,
        )
        speaker.speak_stream(iter(["One. ", "Two."]))
        self.assertEqual(playing, ["One.", "Two."])

    def test_stopping_halts_synthesis_and_playback(self):
        stop = threading.Event()
        synthesized: list[str] = []
        played: list[str] = []

        def synth(text):
            synthesized.append(text)
            # The user cuts in while the first sentence is being synthesized.
            stop.set()
            return [text.encode("utf-8")]

        speaker = StreamingSpeaker(
            synth,
            lambda pcm: played.append(pcm.decode("utf-8")),
            chunker=SentenceChunker(min_chars=1),
            should_stop=stop.is_set,
        )
        speaker.speak_stream(iter([f"Sentence {i}. " for i in range(20)]))

        self.assertEqual(synthesized, ["Sentence 0."])
        self.assertEqual(played, [])

    def test_stopping_closes_the_upstream_generator(self):
        """Closing it is what tears down the SSE stream and stops the model."""
        stop = threading.Event()
        interrupted = threading.Event()
        closed: list[bool] = []

        def deltas():
            try:
                yield "First sentence. "
                # Hold the model's stream open until the user has cut in.
                interrupted.wait(timeout=5)
                for index in range(100):
                    yield f"Sentence {index}. "
            except GeneratorExit:
                closed.append(True)
                raise

        def sink(pcm):
            stop.set()
            interrupted.set()

        speaker = StreamingSpeaker(
            self._synth,
            sink,
            chunker=SentenceChunker(min_chars=1),
            should_stop=stop.is_set,
        )
        speaker.speak_stream(deltas())
        self.assertEqual(closed, [True])

    def test_stopping_does_not_deadlock_on_a_full_queue(self):
        stop = threading.Event()

        def sink(pcm):
            stop.set()

        speaker = StreamingSpeaker(
            self._synth,
            sink,
            chunker=SentenceChunker(min_chars=1),
            should_stop=stop.is_set,
            max_queue=2,
        )
        # Far more chunks than the queues hold: the producer must notice the stop
        # rather than block forever waiting on a consumer that has given up.
        finished = threading.Event()

        def run():
            speaker.speak_stream(iter([f"Sentence {i}. " for i in range(200)]))
            finished.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        self.assertTrue(finished.wait(timeout=10), "speak_stream did not unwind")


if __name__ == "__main__":
    unittest.main()
