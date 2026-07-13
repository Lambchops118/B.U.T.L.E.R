"""Interface contracts shared by every voice-pipeline backend.

These are intentionally small and dependency-free so they can be imported on any
platform (the heavy ML libraries live only in the concrete implementations).

The data model deliberately mirrors the shapes the existing agent runtime already
uses:

- Tool definitions are the flat OpenAI *Responses* shape used throughout
  :mod:`talos.agent.runtime` (``{"type": "function", "name": ..., "parameters": ...}``).
  :func:`responses_tools_to_chat_tools` converts them to the nested *Chat
  Completions* shape that local servers (Ollama / vLLM) expect.
- A tool call surfaces ``call_id`` / ``name`` / ``arguments`` so the existing tool
  dispatch in the runtime can be reused without change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator


# --------------------------------------------------------------------------- #
# Audio / transcription data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AudioChunk:
    """A buffer of little-endian 16-bit mono PCM samples."""

    pcm: bytes
    sample_rate: int = 16000

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        # 2 bytes per sample, mono.
        return len(self.pcm) / float(self.sample_rate * 2)


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    language: str | None = None
    confidence: float | None = None
    raw: Any = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


# --------------------------------------------------------------------------- #
# LLM streaming data
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LLMToolCall:
    call_id: str
    name: str
    arguments: str  # raw JSON string, parsed by the caller


@dataclass(frozen=True)
class LLMTextDelta:
    """Incremental assistant text emitted while streaming."""

    text: str


@dataclass(frozen=True)
class LLMCompletion:
    """Terminal event of a streamed turn.

    ``text`` is the full assistant message accumulated across the stream.
    ``tool_calls`` is non-empty when the model wants tools executed before it can
    answer; the caller runs them and streams another turn with the results.
    """

    text: str
    tool_calls: tuple[LLMToolCall, ...] = ()
    finish_reason: str = "stop"
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


# --------------------------------------------------------------------------- #
# Backend interfaces
# --------------------------------------------------------------------------- #
class LLMBackend(ABC):
    """A streaming, tool-calling chat model.

    Implementations talk to an OpenAI-compatible endpoint (Ollama on macOS, vLLM
    on CUDA) or a hosted API. The contract is provider-agnostic so the agent loop
    never depends on a specific runtime.
    """

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[LLMTextDelta | LLMCompletion]:
        """Yield :class:`LLMTextDelta` items, then exactly one terminal
        :class:`LLMCompletion`."""
        raise NotImplementedError

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        """Convenience non-streaming wrapper that drains :meth:`stream`."""
        final: LLMCompletion | None = None
        for event in self.stream(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if isinstance(event, LLMCompletion):
                final = event
        if final is None:
            raise RuntimeError("LLM stream ended without a completion event.")
        return final


class STTBackend(ABC):
    """Speech-to-text over a finished utterance of PCM audio."""

    @abstractmethod
    def transcribe(self, audio: AudioChunk) -> TranscriptResult:
        raise NotImplementedError


class TTSBackend(ABC):
    """Text-to-speech that streams PCM so playback can start on the first chunk."""

    sample_rate: int = 16000

    @abstractmethod
    def synthesize(self, text: str) -> Iterator[bytes]:
        """Yield little-endian 16-bit mono PCM chunks for ``text``."""
        raise NotImplementedError


class WakeWordDetector(ABC):
    """Lightweight always-on wake-word gate (e.g. openWakeWord / Porcupine)."""

    @abstractmethod
    def detect(self, audio: AudioChunk) -> bool:
        raise NotImplementedError

    def reset(self) -> None:  # pragma: no cover - optional hook
        """Clear any streaming detector state between utterances."""
        return None


class VoiceActivityDetector(ABC):
    """Frame-level speech detection used for endpointing."""

    @abstractmethod
    def is_speech(self, frame: AudioChunk) -> bool:
        raise NotImplementedError

    def reset(self) -> None:  # pragma: no cover - optional hook
        return None


# --------------------------------------------------------------------------- #
# Tool-definition adapters (Responses shape <-> Chat Completions shape)
# --------------------------------------------------------------------------- #
def responses_tools_to_chat_tools(
    tools: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Convert the flat Responses tool defs used by the runtime into the nested
    Chat Completions ``tools`` format.

    Input shape (per :mod:`talos.agent.runtime`)::

        {"type": "function", "name": "x", "description": "...", "parameters": {...}}

    Output shape::

        {"type": "function", "function": {"name": "x", "description": "...",
                                          "parameters": {...}}}

    Tool defs already in the nested shape are passed through unchanged.
    """
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        # Already nested -> pass through.
        if isinstance(tool.get("function"), dict):
            converted.append(tool)
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        function: dict[str, Any] = {"name": name}
        if tool.get("description") is not None:
            function["description"] = tool["description"]
        function["parameters"] = tool.get("parameters") or {
            "type": "object",
            "properties": {},
        }
        converted.append({"type": "function", "function": function})
    return converted


def chat_messages_to_tool_result(call: LLMToolCall, output: str) -> dict[str, Any]:
    """Build the Chat Completions ``tool`` role message that returns a tool's
    output to the model for the next streamed turn."""
    return {
        "role": "tool",
        "tool_call_id": call.call_id,
        "content": output,
    }


def tool_calls_to_assistant_message(
    text: str,
    tool_calls: Iterable[LLMToolCall],
) -> dict[str, Any]:
    """Build the assistant message that records a tool-calling turn so it can be
    appended to history before the tool results."""
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    serialized = [
        {
            "id": call.call_id,
            "type": "function",
            "function": {"name": call.name, "arguments": call.arguments},
        }
        for call in tool_calls
    ]
    if serialized:
        message["tool_calls"] = serialized
    return message


@dataclass
class _ToolCallAccumulator:
    """Accumulates streamed Chat Completions tool-call deltas keyed by index."""

    _by_index: dict[int, dict[str, str]] = field(default_factory=dict)

    def add(self, index: int, *, call_id: str | None, name: str | None, arguments: str | None) -> None:
        slot = self._by_index.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if call_id:
            slot["id"] = call_id
        if name:
            slot["name"] = name
        if arguments:
            slot["arguments"] += arguments

    def finalize(self) -> tuple[LLMToolCall, ...]:
        calls: list[LLMToolCall] = []
        for index in sorted(self._by_index):
            slot = self._by_index[index]
            name = slot["name"].strip()
            if not name:
                continue
            call_id = slot["id"].strip() or f"call_{index}"
            calls.append(
                LLMToolCall(call_id=call_id, name=name, arguments=slot["arguments"] or "{}")
            )
        return tuple(calls)
