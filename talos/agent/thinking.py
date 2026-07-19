"""Dynamic reasoning ("thinking") control for the local streaming LLM.

Qwen3 models -- including the ``mb-core-v1`` finetune this deployment runs on
Ollama -- support a per-message *soft switch*: appending ``/no_think`` to the
user turn suppresses the hidden ``<think>`` reasoning block, and ``/think``
forces it on. For a latency-sensitive voice / home-automation assistant that
reasoning block is pure overhead on quick commands ("turn on the kitchen
lights") but genuinely useful on analytical requests ("why does the furnace
keep short-cycling?"). Measured on the deploy box, suppressing it cut a trivial
command from ~272 generated tokens to ~19 at the same tokens/second, i.e. the
dominant source of local-model latency.

``TALOS_LLM_THINK_MODE`` selects the policy:

    auto   (default) heuristic -- reason only on complex requests
    always force reasoning on every turn
    never  suppress reasoning on every turn
    off    inject nothing (use for non-Qwen models that would otherwise receive
           a literal, meaningless ``/no_think`` token)

Only a soft token is appended to the *outgoing* user turn. Callers persist the
original, undecorated command to memory, so stored conversation history stays
clean and this control is invisible across turns.
"""

from __future__ import annotations

import os

NO_THINK_TOKEN = "/no_think"
THINK_TOKEN = "/think"

_VALID_MODES = {"auto", "always", "never", "off"}

# Requests whose full text (normalized, lowercased) contains one of these cues
# are treated as analytical and get the reasoning block. Kept phrase-based and
# deliberately conservative: when a request is ambiguous we favour speed and
# leave thinking off, because latency is the whole reason this control exists.
_COMPLEX_CUES = (
    "why ",
    "why?",
    "how do",
    "how does",
    "how can",
    "how would",
    "how should",
    "how to",
    "explain",
    "analyze",
    "analyse",
    "debug",
    "troubleshoot",
    "diagnose",
    "root cause",
    "figure out",
    "work out",
    "think through",
    "step by step",
    "step-by-step",
    "compare",
    "difference between",
    "pros and cons",
    "trade-off",
    "tradeoff",
    "what if",
    "should i",
    "should we",
    "plan ",
    "design ",
    "strategy",
    "estimate",
    "calculate",
    "derive",
    "evaluate",
    "assess",
    "investigate",
    "research",
    "recommend",
    "optimize",
    "optimise",
    # Coding / engineering requests benefit from reasoning even when short.
    "code",
    "a function",
    "a script",
    "a program",
    "implement",
    "refactor",
    "algorithm",
    "regex",
    "stack trace",
    "traceback",
    "exception",
    "compile",
    "python",
    "pygame",
    "javascript",
    "typescript",
    "rust",
    "golang",
    "powershell",
    "bash script",
    "sql",
)

# A long request is usually multi-constraint work; mirror the request
# classifier's long-request threshold and let it reason.
_LONG_REQUEST_CHARS = 200


def resolve_think_mode() -> str:
    """Return the configured think-mode policy (defaults to ``auto``)."""
    value = os.getenv("TALOS_LLM_THINK_MODE", "auto").strip().lower()
    return value if value in _VALID_MODES else "auto"


def wants_thinking(command: str, *, runtime_lane: str = "foreground") -> bool:
    """Heuristic: should this request get the reasoning block? (``auto`` mode).

    Complex/background work reasons; quick conversational or device commands do
    not. Errs toward ``False`` so the fast path stays the default.
    """
    if str(runtime_lane or "").strip().lower() == "background":
        return True
    normalized = " ".join(str(command or "").lower().split())
    if not normalized:
        return False
    if len(normalized) >= _LONG_REQUEST_CHARS:
        return True
    return any(cue in normalized for cue in _COMPLEX_CUES)


def thinking_suffix(
    command: str,
    *,
    runtime_lane: str = "foreground",
    mode: str | None = None,
) -> str:
    """Return the soft-switch suffix to append to the outgoing user turn.

    One of ``" /think"``, ``" /no_think"``, or ``""`` (append nothing). The
    leading space keeps the token separated from the command text.
    """
    resolved = (mode or resolve_think_mode()).strip().lower()
    if resolved not in _VALID_MODES:
        resolved = "auto"

    if resolved == "off":
        return ""
    if resolved == "always":
        return f" {THINK_TOKEN}"
    if resolved == "never":
        return f" {NO_THINK_TOKEN}"

    think = wants_thinking(command, runtime_lane=runtime_lane)
    return f" {THINK_TOKEN}" if think else f" {NO_THINK_TOKEN}"
