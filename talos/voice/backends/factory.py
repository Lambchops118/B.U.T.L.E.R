"""Build voice backends from environment configuration.

The goal of this module is that the *same code* runs on the macOS dev box and the
CUDA deploy box, with only environment variables changing. Recommended profiles:

Local Ollama (default, no hosted APIs)::

    TALOS_LLM_BACKEND=ollama
    TALOS_LLM_BASE_URL=http://127.0.0.1:11434/v1   # Ollama
    TALOS_LLM_MODEL=mb-core-v1:latest

CUDA deploy (5080 runs vLLM)::

    TALOS_LLM_BACKEND=openai_chat
    TALOS_LLM_BASE_URL=http://127.0.0.1:8000/v1     # vLLM OpenAI server
    TALOS_LLM_MODEL=Qwen/Qwen2.5-14B-Instruct-AWQ

Only the LLM backend is wired here for now; STT/TTS factories are added alongside
their concrete implementations.
"""

from __future__ import annotations

import os

from talos.config import env_float, env_int, load_environment
from talos.voice.backends.base import LLMBackend, STTBackend
from talos.voice.backends.llm_openai_compat import OpenAICompatibleChatBackend


def get_llm_backend() -> LLMBackend:
    """Construct the configured streaming LLM backend.

    ``TALOS_LLM_BACKEND`` selects the implementation; currently only the
    OpenAI-compatible Chat Completions backend (``openai_chat``) is available,
    which covers Ollama, vLLM, and hosted OpenAI-compatible providers.
    """
    load_environment()
    backend = os.getenv("TALOS_LLM_BACKEND", "ollama").strip().lower()

    if backend in {"openai_chat", "chat", "ollama", "vllm"}:
        base_url = os.getenv("TALOS_LLM_BASE_URL", "").strip() or None
        if backend == "ollama" and base_url is None:
            base_url = "http://127.0.0.1:11434/v1"
        max_tokens_param = os.getenv("TALOS_LLM_MAX_TOKENS_PARAM", "").strip()
        if not max_tokens_param:
            is_openai = base_url is None or "openai.com" in base_url
            max_tokens_param = "max_completion_tokens" if is_openai else "max_tokens"
        return OpenAICompatibleChatBackend(
            model=_required("TALOS_LLM_MODEL"),
            base_url=base_url,
            api_key=os.getenv("TALOS_LLM_API_KEY", "").strip() or None,
            temperature=env_float("TALOS_LLM_TEMPERATURE", 0.5),
            max_tokens=env_int(
                "TALOS_LLM_MAX_TOKENS",
                env_int("TALOS_AGENT_MAX_OUTPUT_TOKENS", 400),
            ),
            max_tokens_param=max_tokens_param,
        )

    raise RuntimeError(
        f"Unsupported TALOS_LLM_BACKEND '{backend}'. Expected one of: "
        "openai_chat, ollama, vllm."
    )


def get_stt_backend() -> STTBackend:
    """Construct the configured local speech-to-text backend.

    ``TALOS_STT_BACKEND`` selects the implementation (default ``faster_whisper``).
    Device/compute default to CUDA on the deploy box and CPU on the Mac; override
    with ``TALOS_STT_DEVICE`` / ``TALOS_STT_COMPUTE_TYPE`` if needed.
    """
    load_environment()
    backend = os.getenv("TALOS_STT_BACKEND", "faster_whisper").strip().lower()

    if backend in {"faster_whisper", "faster-whisper", "local", "whisper"}:
        from talos.voice.backends.stt_faster_whisper import FasterWhisperSTT

        return FasterWhisperSTT(
            model_size=os.getenv("TALOS_STT_MODEL", "distil-large-v3").strip() or "distil-large-v3",
            device=os.getenv("TALOS_STT_DEVICE", "").strip() or None,
            compute_type=os.getenv("TALOS_STT_COMPUTE_TYPE", "").strip() or None,
            language=os.getenv("TALOS_STT_LANGUAGE", "en").strip() or None,
            beam_size=env_int("TALOS_STT_BEAM_SIZE", 1),
        )

    raise RuntimeError(
        f"Unsupported TALOS_STT_BACKEND '{backend}'. Expected 'faster_whisper'."
    )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Configure the local model name "
            "(e.g. TALOS_LLM_MODEL=qwen2.5:7b-instruct for Ollama)."
        )
    return value
