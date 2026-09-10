from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestClassification:
    mode: str
    reason: str
    response: str = ""


STATUS_TERMS = {
    "active jobs",
    "any updates",
    "did it finish",
    "done yet",
    "progress",
    "running jobs",
    "status",
    "updates",
    "what are you working on",
    "what happened",
    "what is going on",
    "what is running",
    "what's going on",
    "what's running",
    "whats going on",
    "whats running",
    "what specifically",
}

BACKGROUND_TERMS = {
    "long-running",
    "long running",
    "take your time",
    "work on this",
    "make the changes",
    "implement issue",
    "fix issue",
    "open a pr",
    "create a pr",
}

KICAD_TERMS = {
    "kicad",
    "pcb",
    "board",
    "schematic",
    "cad",
    "footprint",
    "trace",
    "routing",
    "route",
    "netlist",
}

WORK_VERBS = {
    "add",
    "analyze",
    "build",
    "change",
    "create",
    "debug",
    "design",
    "draw",
    "fix",
    "generate",
    "implement",
    "inspect",
    "queue",
    "make",
    "modify",
    "place",
    "plan",
    "research",
    "review",
    "route",
    "run",
    "schedule",
    "set",
    "update",
    "wire",
    "work",
    "write",
}

MULTI_STEP_TERMS = {
    "multi-step",
    "multi step",
    "research",
    "investigate",
    "plan",
    "generate",
    "write a report",
    "build me",
    "set up",
    "look through",
    "go through",
}

FOREGROUND_QUESTION_PREFIXES = (
    "am i ",
    "are there ",
    "are you ",
    "can you ",
    "did ",
    "do you ",
    "does ",
    "has ",
    "have ",
    "is ",
    "what is ",
    "what are ",
    "what jobs ",
    "what specifically ",
    "what's ",
    "whats ",
    "who is ",
    "who are ",
    "when is ",
    "where is ",
    "why is ",
    "how do i ",
    "how does ",
    "explain ",
)


def classify_request(
    text: str,
    *,
    source: str = "http",
    requested_mode: str | None = None,
) -> RequestClassification:
    mode = _normalize_requested_mode(requested_mode)
    if mode in {"foreground", "background"}:
        return RequestClassification(mode=mode, reason=f"explicit {mode} request")

    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return RequestClassification(mode="foreground", reason="empty request")

    if _is_status_request(normalized):
        return RequestClassification(mode="status", reason="job/status question")

    if _looks_like_simple_question(normalized):
        return RequestClassification(mode="foreground", reason="ordinary question")

    if any(term in normalized for term in BACKGROUND_TERMS):
        return RequestClassification(mode="background", reason="explicit long-running work cue")

    tokens = _tokens(normalized)
    if "background" in tokens and tokens & WORK_VERBS:
        return RequestClassification(mode="background", reason="explicit background work request")

    if tokens & KICAD_TERMS and tokens & WORK_VERBS:
        return RequestClassification(mode="background", reason="KiCad/CAD work is tool-heavy")

    if any(term in normalized for term in MULTI_STEP_TERMS):
        return RequestClassification(mode="background", reason="multi-step work cue")

    if len(normalized) >= 220 and tokens & WORK_VERBS:
        return RequestClassification(mode="background", reason="long work-style request")

    return RequestClassification(mode="foreground", reason="lightweight conversational request")


def _normalize_requested_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"background", "bg", "async", "job"}:
        return "background"
    if normalized in {"foreground", "fg", "sync", "chat"}:
        return "foreground"
    return "auto"


def _tokens(value: str) -> set[str]:
    normalized = []
    for char in value:
        normalized.append(char if char.isalnum() else " ")
    return {token for token in "".join(normalized).split() if token}


def _looks_like_simple_question(value: str) -> bool:
    if value.endswith("?"):
        return True
    return any(value.startswith(prefix) for prefix in FOREGROUND_QUESTION_PREFIXES)


def _is_status_request(value: str) -> bool:
    if "job_" in value:
        return True
    tokens = _tokens(value)
    if any(term in value for term in STATUS_TERMS):
        return True
    if tokens & {"status", "updates", "progress"}:
        return True
    if "stuck" in tokens and (tokens & KICAD_TERMS or "loading" in tokens):
        return True
    if tokens & {"job", "jobs"} and tokens & {"active", "running", "status", "updates", "progress"}:
        return True
    return False
