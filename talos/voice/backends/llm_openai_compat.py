"""LLM backend over any OpenAI-compatible Chat Completions endpoint.

This is the seam that lets the agent run on a local model with no hosted API:

- macOS dev:  Ollama        (``base_url=http://127.0.0.1:11434/v1``)
- CUDA deploy: vLLM         (``base_url=http://<host>:8000/v1``)
- fallback:    OpenAI/etc.  (any Chat Completions provider)

It streams text deltas (so TTS can start early) and accumulates tool calls into
the provider-agnostic :class:`~talos.voice.backends.base.LLMCompletion` the agent
loop consumes. The client is injected for testability.
"""

from __future__ import annotations

from typing import Any, Iterator

from talos.voice.backends.base import (
    LLMBackend,
    LLMCompletion,
    LLMTextDelta,
    _ToolCallAccumulator,
    responses_tools_to_chat_tools,
)


class OpenAICompatibleChatBackend(LLMBackend):
    def __init__(
        self,
        *,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.5,
        max_tokens: int = 400,
        max_tokens_param: str = "max_tokens",
        client: Any | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        if not model:
            raise ValueError("OpenAICompatibleChatBackend requires a model name.")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # OpenAI's newer models require ``max_completion_tokens`` on Chat
        # Completions; local servers (Ollama/vLLM) use the classic ``max_tokens``.
        self.max_tokens_param = max_tokens_param or "max_tokens"
        self._extra_body = dict(extra_body or {})
        self._client = client if client is not None else self._build_client(base_url, api_key)

    @staticmethod
    def _build_client(base_url: str | None, api_key: str | None) -> Any:
        import openai

        if api_key:
            return openai.OpenAI(base_url=base_url or None, api_key=api_key)
        if base_url:
            # Local servers (Ollama/vLLM) ignore the key but the SDK requires a
            # non-empty value, so use a harmless placeholder.
            return openai.OpenAI(base_url=base_url, api_key="not-needed")
        # OpenAI with no explicit key: let the SDK read OPENAI_API_KEY from env.
        return openai.OpenAI()

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[LLMTextDelta | LLMCompletion]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": self.temperature if temperature is None else temperature,
        }
        request[self.max_tokens_param] = self.max_tokens if max_tokens is None else max_tokens
        chat_tools = responses_tools_to_chat_tools(tools)
        if chat_tools:
            request["tools"] = chat_tools
        if self._extra_body:
            request["extra_body"] = self._extra_body

        accumulator = _ToolCallAccumulator()
        text_parts: list[str] = []
        finish_reason = "stop"

        for chunk in self._client.chat.completions.create(**request):
            choice = _first_choice(chunk)
            if choice is None:
                continue
            delta = getattr(choice, "delta", None)

            content = getattr(delta, "content", None) if delta is not None else None
            if content:
                text_parts.append(content)
                yield LLMTextDelta(text=content)

            tool_calls = getattr(delta, "tool_calls", None) if delta is not None else None
            for tc in tool_calls or []:
                fn = getattr(tc, "function", None)
                accumulator.add(
                    int(getattr(tc, "index", 0) or 0),
                    call_id=getattr(tc, "id", None),
                    name=getattr(fn, "name", None) if fn is not None else None,
                    arguments=getattr(fn, "arguments", None) if fn is not None else None,
                )

            reason = getattr(choice, "finish_reason", None)
            if reason:
                finish_reason = reason

        yield LLMCompletion(
            text="".join(text_parts).strip(),
            tool_calls=accumulator.finalize(),
            finish_reason=finish_reason,
        )


def _first_choice(chunk: Any) -> Any | None:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return choices[0]
