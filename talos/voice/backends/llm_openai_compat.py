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

import json
import time
import urllib.error
import urllib.parse
import urllib.request
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
        backend_name: str = "openai_compatible",
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
        self.backend_name = (backend_name or "openai_compatible").strip().lower()
        self.base_url = base_url
        self.is_local = self.backend_name in {"ollama", "vllm"} or _is_loopback_url(
            base_url
        )
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

    def warmup(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> float:
        """Evaluate a representative prompt so the first real turn is warm.

        Local servers cache the evaluated KV prefix between requests, so sending
        the prompt once at startup moves that prompt evaluation off the first
        user turn. Generation is capped at a single token because only the
        prefill matters here.

        ``tools`` must carry the same surface a real turn sends: chat templates
        commonly branch on whether tools are present, so a tool-less warmup
        renders a different prompt and shares a far shorter prefix. Returns
        elapsed milliseconds.
        """
        started = time.perf_counter()
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": self.temperature,
            self.max_tokens_param: 1,
        }
        chat_tools = responses_tools_to_chat_tools(tools)
        if chat_tools:
            request["tools"] = chat_tools
        if self._extra_body:
            request["extra_body"] = self._extra_body
        self._client.chat.completions.create(**request)
        return round((time.perf_counter() - started) * 1000.0, 1)

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

        probe_started = time.perf_counter()
        before = (
            _ollama_model_status(self.base_url, self.model)
            if self.backend_name == "ollama"
            else None
        )
        probe_ms = round((time.perf_counter() - probe_started) * 1000.0, 1)
        request_started = time.perf_counter()
        first_chunk_at: float | None = None
        provider_prompt_tokens: int | None = None
        provider_completion_tokens: int | None = None
        provider_total_tokens: int | None = None
        accumulator = _ToolCallAccumulator()
        text_parts: list[str] = []
        finish_reason = "stop"

        for chunk in self._client.chat.completions.create(**request):
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            usage = getattr(chunk, "usage", None)
            provider_prompt_tokens = _usage_int(
                usage, "prompt_tokens", provider_prompt_tokens
            )
            provider_completion_tokens = _usage_int(
                usage, "completion_tokens", provider_completion_tokens
            )
            provider_total_tokens = _usage_int(
                usage, "total_tokens", provider_total_tokens
            )
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

        request_done = time.perf_counter()
        after = before
        if self.backend_name == "ollama" and not (before or {}).get("loaded"):
            after = _ollama_model_status(self.base_url, self.model)

        ttft_ms = (
            round((first_chunk_at - request_started) * 1000.0, 1)
            if first_chunk_at is not None
            else None
        )
        model_preloaded = (before or {}).get("loaded")
        if model_preloaded is True:
            model_load_ms = 0.0
            model_load_measurement = "already_loaded"
        elif model_preloaded is False and ttft_ms is not None:
            # The OpenAI-compatible Ollama API does not expose native
            # load_duration. For a cold model, time-to-first-server-chunk is a
            # truthful upper bound containing load + prompt evaluation.
            model_load_ms = ttft_ms
            model_load_measurement = "cold_start_ttft_upper_bound"
        else:
            model_load_ms = None
            model_load_measurement = "unavailable"

        yield LLMCompletion(
            text="".join(text_parts).strip(),
            tool_calls=accumulator.finalize(),
            finish_reason=finish_reason,
            telemetry={
                "backend": self.backend_name,
                "backend_location": "local" if self.is_local else "hosted",
                "model": self.model,
                "backend_probe_ms": probe_ms,
                "model_preloaded": model_preloaded,
                "model_load_ms": model_load_ms,
                "model_load_measurement": model_load_measurement,
                "ollama_context_length": (after or {}).get("context_length"),
                "llm_ttft_ms": ttft_ms,
                "llm_request_ms": round((request_done - request_started) * 1000.0, 1),
                "provider_prompt_tokens": provider_prompt_tokens,
                "provider_completion_tokens": provider_completion_tokens,
                "provider_total_tokens": provider_total_tokens,
            },
        )


def _first_choice(chunk: Any) -> Any | None:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return choices[0]


def _usage_int(usage: Any, name: str, fallback: int | None) -> int | None:
    value = getattr(usage, name, None) if usage is not None else None
    if isinstance(usage, dict):
        value = usage.get(name, value)
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _is_loopback_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    try:
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _ollama_model_status(
    base_url: str | None, model: str
) -> dict[str, Any] | None:
    """Return passive Ollama residency/context data without loading a model."""
    if not base_url:
        return None
    try:
        parsed = urllib.parse.urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, "/api/ps", "", "", "")
        )
        with urllib.request.urlopen(url, timeout=0.25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return None

    for item in payload.get("models") or []:
        candidate = str(item.get("model") or item.get("name") or "")
        if _same_model_name(candidate, model):
            return {
                "loaded": True,
                "context_length": item.get("context_length"),
            }
    return {"loaded": False, "context_length": None}


def _same_model_name(left: str, right: str) -> bool:
    def normalized(value: str) -> str:
        value = value.strip().lower()
        return value.removesuffix(":latest")

    return bool(normalized(left)) and normalized(left) == normalized(right)
