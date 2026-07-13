"""Streaming voice-pipeline orchestration.

This package overlaps speech-to-text, the LLM, and text-to-speech so audio can
start playing while the model is still generating. The first piece is
:class:`talos.voice.streaming.sentence_chunker.SentenceChunker`, which turns a
stream of LLM text deltas into speakable sentence-sized chunks for incremental
synthesis.
"""

from __future__ import annotations

from talos.voice.streaming.sentence_chunker import SentenceChunker

__all__ = ["SentenceChunker"]
