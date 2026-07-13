"""Pluggable backends for the streaming voice pipeline.

The voice loop (wake word -> VAD -> STT -> LLM -> TTS -> playback) is written
against the interfaces in :mod:`talos.voice.backends.base` so the same
orchestration runs on different hardware:

- macOS dev box (Metal / MLX / CPU, Ollama for the LLM)
- NVIDIA deploy box (CUDA: vLLM for the LLM, faster-whisper, local TTS)

Concrete implementations live in sibling modules and are selected at runtime by
:mod:`talos.voice.backends.factory` based on environment configuration.
"""

from __future__ import annotations

from talos.voice.backends.base import (
    AudioChunk,
    LLMBackend,
    LLMCompletion,
    LLMTextDelta,
    LLMToolCall,
    STTBackend,
    TranscriptResult,
    TTSBackend,
    VoiceActivityDetector,
    WakeWordDetector,
    chat_messages_to_tool_result,
    responses_tools_to_chat_tools,
)

__all__ = [
    "AudioChunk",
    "LLMBackend",
    "LLMCompletion",
    "LLMTextDelta",
    "LLMToolCall",
    "STTBackend",
    "TTSBackend",
    "TranscriptResult",
    "VoiceActivityDetector",
    "WakeWordDetector",
    "chat_messages_to_tool_result",
    "responses_tools_to_chat_tools",
]
