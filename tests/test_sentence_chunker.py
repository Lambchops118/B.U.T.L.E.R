from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.voice.streaming.sentence_chunker import SentenceChunker


def _drain(chunker: SentenceChunker, deltas: list[str]) -> list[str]:
    chunks: list[str] = []
    for delta in deltas:
        chunks.extend(chunker.push(delta))
    tail = chunker.flush()
    if tail:
        chunks.append(tail)
    return chunks


class SentenceChunkerTests(unittest.TestCase):
    def test_emits_on_sentence_boundary(self):
        chunker = SentenceChunker(min_chars=1)
        chunks = _drain(chunker, ["Hello there. ", "How are you?"])
        self.assertEqual(chunks, ["Hello there.", "How are you?"])

    def test_token_by_token_stream(self):
        chunker = SentenceChunker(min_chars=1)
        tokens = list("The light is on. ") + ["Done", "!"]
        chunks = _drain(chunker, tokens)
        self.assertEqual(chunks, ["The light is on.", "Done!"])

    def test_does_not_split_decimals_or_domains(self):
        chunker = SentenceChunker(min_chars=1)
        chunks = _drain(chunker, ["Pi is 3.14 and the site is talos.io for info."])
        self.assertEqual(chunks, ["Pi is 3.14 and the site is talos.io for info."])

    def test_does_not_split_on_abbreviations(self):
        chunker = SentenceChunker(min_chars=1)
        chunks = _drain(chunker, ["Dr. Smith called Mr. Lee. ", "OK."])
        self.assertEqual(chunks, ["Dr. Smith called Mr. Lee.", "OK."])

    def test_newline_forces_flush(self):
        chunker = SentenceChunker(min_chars=200)
        chunks = _drain(chunker, ["item one\nitem two\n"])
        self.assertEqual(chunks, ["item one", "item two"])

    def test_min_chars_holds_short_fragments_together(self):
        chunker = SentenceChunker(min_chars=20)
        # "Hi. " is shorter than min_chars, so it should merge with the next.
        chunks = _drain(chunker, ["Hi. ", "This is a longer sentence now."])
        self.assertEqual(chunks, ["Hi. This is a longer sentence now."])

    def test_max_chars_safety_valve(self):
        chunker = SentenceChunker(min_chars=10, max_chars=30)
        long_run = "word " * 20  # no sentence punctuation
        chunks = _drain(chunker, [long_run])
        self.assertTrue(len(chunks) >= 2)
        self.assertTrue(all(len(c) <= 30 for c in chunks))

    def test_flush_returns_remainder_without_punctuation(self):
        chunker = SentenceChunker(min_chars=1)
        chunks = _drain(chunker, ["No trailing period here"])
        self.assertEqual(chunks, ["No trailing period here"])

    def test_waits_for_more_when_boundary_at_buffer_end(self):
        chunker = SentenceChunker(min_chars=1)
        # A period at the very end could be a decimal; chunker should wait.
        self.assertEqual(chunker.push("Ready."), [])
        self.assertEqual(chunker.push(" Go."), ["Ready."])


if __name__ == "__main__":
    unittest.main()
