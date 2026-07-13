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


if __name__ == "__main__":
    unittest.main()
