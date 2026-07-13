"""Turn a stream of LLM text deltas into speakable chunks.

The streaming voice pipeline feeds the LLM's token stream into TTS as soon as a
complete, natural-sounding chunk is available, instead of waiting for the whole
response. That collapses perceived latency to "time to first sentence + first
audio chunk".

Design goals:

- Emit on sentence-ending punctuation (``.``, ``!``, ``?``) and hard newlines.
- Never split mid-number ("3.14"), mid-decimal, or after a common abbreviation
  ("Mr.", "e.g.").
- Avoid emitting tiny fragments: hold a chunk until it reaches ``min_chars``
  unless a hard boundary (newline / very long buffer) forces a flush.
- Be a pure, synchronous state machine so it is trivially unit-testable and has
  no dependency on the audio or model layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_SENTENCE_ENDINGS = frozenset(".!?")
# Lower-cased tokens (without the trailing dot) that should NOT end a chunk.
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "inc",
        "ltd",
        "co",
        "e.g",
        "i.e",
        "a.m",
        "p.m",
        "u.s",
        "u.k",
        "approx",
        "no",
        "fig",
    }
)


@dataclass
class SentenceChunker:
    """Incremental sentence segmenter.

    Usage::

        chunker = SentenceChunker()
        for delta in llm_text_deltas:
            for chunk in chunker.push(delta):
                tts.synthesize(chunk)
        tail = chunker.flush()
        if tail:
            tts.synthesize(tail)
    """

    min_chars: int = 32
    max_chars: int = 240
    _buffer: str = field(default="", repr=False)

    def push(self, text: str) -> list[str]:
        """Add streamed text; return any newly-complete speakable chunks."""
        if not text:
            return []
        self._buffer += text
        chunks: list[str] = []
        while True:
            split_at = self._find_split()
            if split_at is None:
                break
            chunk = self._buffer[:split_at].strip()
            self._buffer = self._buffer[split_at:].lstrip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def flush(self) -> str | None:
        """Return any remaining buffered text (call once the stream ends)."""
        remainder = self._buffer.strip()
        self._buffer = ""
        return remainder or None

    # ------------------------------------------------------------------ #
    def _find_split(self) -> int | None:
        """Return an index to split the buffer at, or ``None`` if not ready.

        The returned index is exclusive of trailing whitespace already, i.e. it
        points just past the boundary character.
        """
        buffer = self._buffer

        # Hard boundary: newline always ends a chunk (lists, paragraphs).
        newline_idx = buffer.find("\n")
        if newline_idx != -1:
            return newline_idx + 1

        # Safety valve: an over-long buffer with no punctuation (rare) gets
        # split at the last word boundary so TTS never stalls.
        if len(buffer) >= self.max_chars:
            cut = buffer.rfind(" ", 0, self.max_chars)
            return (cut + 1) if cut > 0 else self.max_chars

        for idx, char in enumerate(buffer):
            if char not in _SENTENCE_ENDINGS:
                continue
            # Need a following character to confirm the sentence has ended; if we
            # are at the end of the buffer, wait for more (more tokens may come).
            if idx == len(buffer) - 1:
                return None
            nxt = buffer[idx + 1]
            # Only break when punctuation is followed by whitespace. "3.14" and
            # "talos.io" are not breaks.
            if not nxt.isspace():
                continue
            if char == "." and self._ends_with_abbreviation(buffer[: idx + 1]):
                continue
            if idx + 1 < self.min_chars:
                # Too short to be worth a separate utterance; keep accumulating.
                continue
            return idx + 1
        return None

    @staticmethod
    def _ends_with_abbreviation(text_through_dot: str) -> bool:
        # Grab the token immediately before the dot.
        stripped = text_through_dot[:-1]
        token = ""
        for char in reversed(stripped):
            if char.isspace():
                break
            token = char + token
        token = token.lower()
        if not token:
            return False
        if token in _ABBREVIATIONS:
            return True
        # Single-letter "A." style initials.
        if len(token) == 1 and token.isalpha():
            return True
        return False
