from __future__ import annotations

import json
import os
import queue
import re
import time
import urllib.request
from typing import Optional

from talos.agent import runtime as agent_runtime
from talos.config import env_bool
from talos.jobs import TERMINAL_STATUSES, JobManager, JobRecord, get_default_job_store
from talos.messages import AnnouncementPayload, Message, StatusPayload, TextPayload, VoicePayload
from talos.request_classifier import RequestClassification, classify_request
from talos.services import awareness_client, awareness_signals, sleep_mode
from talos.state_store import StateStore


BACKGROUND_ACK = "I can do that. I'm working on it now."
# The voice worker owns TTS and the audio device. Announcements send their
# already-rendered text; user commands send the agent's response. Enqueue is
# best-effort and is not playback acknowledgement.
VOICE_SPEAK_URL = os.getenv("TALOS_VOICE_SPEAK_URL", "http://127.0.0.1:8610")
VOICE_SPEAK_ENABLED = env_bool("TALOS_VOICE_SPEAK_ENABLED", True)


def _speak_via_voice_worker(text: str) -> None:
    text = (text or "").strip()
    if not text or not VOICE_SPEAK_ENABLED:
        return
    try:
        request = urllib.request.Request(
            VOICE_SPEAK_URL.rstrip("/") + "/speak",
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=2)
    except Exception:
        # Audio is a best-effort add-on to the GUI banner; never break the
        # router loop because the voice worker is down or slow.
        pass
# The voice lane is latency-sensitive: skip the extra model-based route call and
# rely on the local heuristic classifier. Set TALOS_VOICE_MODEL_ROUTING=1 to
# restore the LLM route decision for voice commands.
VOICE_MODEL_ROUTING_ENABLED = env_bool("TALOS_VOICE_MODEL_ROUTING", False)
PHONE_FOREGROUND_PATTERNS = (
    re.compile(r"\bplace_phone_call\b"),
    re.compile(r"\bcall now\b"),
    re.compile(r"\bplace (?:the )?call\b"),
    re.compile(r"\bmake (?:the |a )?call\b"),
    re.compile(r"\btry (?:the )?call\b"),
    re.compile(r"\bdial\b"),
    re.compile(r"^phone\b"),
    re.compile(r"\bring\b"),
    re.compile(r"^call\b"),
    re.compile(r"\bgive\b.{0,40}\ba call\b"),
)
CODE_CALL_EXCLUSIONS = {
    "function",
    "functions",
    "method",
    "methods",
    "api",
    "tool",
    "tools",
    "script",
    "class",
    "endpoint",
    "number",
    "numbers",
}


_SEVERITY_TAG = re.compile(r"^\[([A-Za-z]+)\]")


def _announcement_severity(title: str) -> str:
    """Severity carried by an announcement title, e.g. "[CRITICAL] Pump down".

    Producers tag the title before it reaches the router; an untagged title is
    treated as routine, which is the safe default while the house is asleep.
    """
    match = _SEVERITY_TAG.match(str(title or "").strip())
    return match.group(1).lower() if match else "notice"


def _run_agent_command(
    command: str,
    gui_queue: queue.Queue,
    snapshot: str,
    *,
    session_id: str,
    interaction_mode: str = "text",
    runtime_lane: str = "foreground",
    extra_context: str | None = None,
) -> str:
    response_text = agent_runtime.run_command(
        command,
        snapshot,
        session_id=session_id,
        interaction_mode=interaction_mode,
        runtime_lane=runtime_lane,
        extra_context=extra_context,
    )
    gui_queue.put(("VOICE_CMD", command, response_text))
    return response_text


def _interaction_mode_for_source(source: str, session_id: str) -> str:
    normalized_source = str(source or "").strip().lower()
    normalized_session = str(session_id or "").strip().lower()
    if normalized_source.startswith("voice") or normalized_session.startswith("voice"):
        return "voice"
    return "text"


def _job_response(job: JobRecord, *, source: str, response_text: str = "") -> dict:
    return {
        "ok": True,
        "mode": "background",
        "session_id": job.session_id,
        "source": source,
        "job_id": job.job_id,
        "status": job.status,
        "response": response_text.strip() or BACKGROUND_ACK,
    }


def _format_job_status(job: JobRecord, *, include_request: bool = False) -> str:
    lines = [f"{job.job_id}: {job.status}"]
    if include_request:
        lines.append(f"Request: {_compact_text(job.request_text, 180)}")
    if job.progress_message:
        lines.append(f"Progress: {job.progress_message}")
    if job.status == "succeeded" and job.result_summary:
        lines.append(f"Result: {job.result_summary}")
    elif job.status in {"failed", "interrupted", "cancelled"} and job.error_message:
        lines.append(f"Error: {job.error_message}")
    elif job.started_at:
        lines.append(f"Started: {job.started_at}")
    return "\n".join(lines)


def _runtime_context_for_session(session_id: str, *, limit: int = 6) -> str:
    store = get_default_job_store()
    jobs = store.list_session_jobs(session_id, limit=limit)
    active_jobs = [job for job in jobs if job.status not in TERMINAL_STATUSES]
    lines = [
        "TALOS runtime/job context:",
        "- The foreground lane is for ordinary conversation, definitions, explanations, lightweight questions, and short direct answers.",
        "- The background lane is for long-running user-requested work that can continue after an immediate acknowledgement.",
        "- If the user asks about job status, answer from the job snapshot below rather than guessing.",
    ]
    if not jobs:
        lines.append("- No background jobs are recorded for this session.")
        return "\n".join(lines)

    if active_jobs:
        lines.append("- Active jobs:")
        lines.extend(f"  - {_format_job_status(job, include_request=True)}" for job in active_jobs[:3])
    else:
        lines.append("- No background jobs are currently active.")

    lines.append("- Recent jobs:")
    lines.extend(f"  - {_format_job_status(job, include_request=True)}" for job in jobs[:limit])
    return "\n".join(lines)


def _classify_with_context(
    command: str,
    *,
    source: str,
    session_id: str,
    runtime_context: str,
    requested_mode: str = "auto",
    allow_model_route: bool = True,
) -> RequestClassification:
    heuristic_decision = classify_request(command, source=source, requested_mode=requested_mode)
    if heuristic_decision.reason.startswith("explicit "):
        return heuristic_decision

    # Voice (and any lane opting out of model routing) uses the local heuristic
    # directly to avoid an extra LLM round-trip in the hot path.
    if not allow_model_route:
        return heuristic_decision

    try:
        payload = agent_runtime.classify_request_route(
            command,
            source=source,
            session_id=session_id,
            runtime_context=runtime_context,
        )
        return RequestClassification(
            mode=payload.get("mode", "foreground"),
            reason=payload.get("reason", "model route decision"),
            response=payload.get("response", ""),
        )
    except Exception as exc:
        print(f"TALOS route decision failed; falling back to heuristic classifier: {exc}")
        return classify_request(command, source=source, requested_mode=requested_mode)


def _must_run_in_foreground(command: str) -> bool:
    normalized = " ".join(str(command or "").lower().split())
    if not normalized:
        return False
    if normalized.startswith("call "):
        tokens = set(normalized.split())
        if tokens & CODE_CALL_EXCLUSIONS:
            return False
    if normalized.startswith("phone "):
        tokens = set(normalized.split())
        if tokens & CODE_CALL_EXCLUSIONS:
            return False
    return any(pattern.search(normalized) for pattern in PHONE_FOREGROUND_PATTERNS)


def _enforce_foreground_for_sensitive_actions(
    command: str,
    decision: RequestClassification,
) -> RequestClassification:
    if decision.mode != "background":
        return decision
    if _must_run_in_foreground(command):
        return RequestClassification(
            mode="foreground",
            reason="phone call actions must stay in the active foreground session",
            response="",
        )
    return decision


def _event_session_id(data: dict | None) -> str:
    return str((data or {}).get("session_id") or "").strip() or "events"


def _compact_text(value: str, limit: int) -> str:
    compacted = " ".join(str(value or "").split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 3].rsplit(" ", 1)[0] + "..."


def router_loop(central_queue: queue.Queue, gui_queue: queue.Queue, stop_signal: Optional[object] = None):
    """
    Central dispatcher:
    - status/event updates refresh StateStore (no API calls)
    - voice/text commands trigger LLM handling with a small state snapshot
    - ui messages forward directly to the GUI queue
    """
    state = StateStore()
    job_manager = JobManager(
        lambda job: _run_agent_command(
            job.request_text,
            gui_queue,
            str((job.metadata or {}).get("state_snapshot") or "no recent status"),
            session_id=job.session_id,
            interaction_mode=str((job.metadata or {}).get("interaction_mode") or "text"),
            runtime_lane="background",
        )
    )
    try:
        while True:
            msg: Message = central_queue.get()
            if msg is None or (stop_signal is not None and msg is stop_signal):
                break

            if msg.type == "status":
                sp: StatusPayload = msg.payload
                state.update_status(sp.key, sp.value, sp.freshness)

            elif msg.type == "announcement":
                announcement: AnnouncementPayload = msg.payload
                # System output is neither a task request nor evidence that a
                # human spoke. Never classify it, run tools, or emit presence.
                gui_queue.put(("VOICE_CMD", announcement.title, announcement.text))
                # The morning wake-up ends sleep mode; everything else routine
                # stays silent until it does. The banner above is queued either
                # way, so the notification is still on the record -- on a panel
                # dimmed to 5%, that is a note to read in the morning, not an
                # interruption. Announcements arriving over /speak are already
                # gated at the text server; this covers in-process producers.
                if sleep_mode.is_wake_announcement(announcement.title):
                    try:
                        sleep_mode.wake(reason="morning wake-up announcement")
                    except RuntimeError as exc:
                        print(f"[router] could not clear sleep mode: {exc}")
                if sleep_mode.should_speak(_announcement_severity(announcement.title)):
                    _speak_via_voice_worker(announcement.text)

            elif msg.type == "voice_cmd":
                vp: VoicePayload = msg.payload
                # Awareness situation when the backend is up; legacy in-memory
                # snapshot otherwise (truthful fallback, Phase 5).
                snapshot = awareness_client.snapshot_with_fallback(state.snapshot())
                runtime_context = _runtime_context_for_session("voice")
                decision = _classify_with_context(
                    vp.command,
                    source="voice",
                    session_id="voice",
                    runtime_context=runtime_context,
                    allow_model_route=VOICE_MODEL_ROUTING_ENABLED,
                )
                decision = _enforce_foreground_for_sensitive_actions(vp.command, decision)
                # Someone spoke: that is an observation of presence and the
                # start of an interaction. Recorded as bounded facts (never
                # the utterance), non-blocking, and it cannot fail this turn.
                _interaction_start = time.monotonic()
                awareness_signals.record_presence(modality="voice")
                awareness_signals.record_interaction_started(
                    session_id="voice",
                    modality="voice",
                    source="voice",
                    routing_mode=decision.mode,
                )
                try:
                    _voice_ok = True
                    if decision.mode == "status":
                        response_text = _run_agent_command(
                            vp.command,
                            gui_queue,
                            snapshot,
                            session_id="voice",
                            interaction_mode="voice",
                            extra_context=runtime_context,
                        )
                        _speak_via_voice_worker(response_text)
                    elif decision.mode == "background":
                        job = job_manager.submit(
                            session_id="voice",
                            source="voice",
                            request_text=vp.command,
                            state_snapshot=snapshot,
                            interaction_mode="voice",
                            classification_reason=decision.reason,
                        )
                        ack_text = decision.response.strip() or BACKGROUND_ACK
                        gui_queue.put(("VOICE_CMD", vp.command, f"{ack_text} Job ID: {job.job_id}"))
                        _speak_via_voice_worker(ack_text)
                    else:
                        response_text = _run_agent_command(
                            vp.command,
                            gui_queue,
                            snapshot,
                            session_id="voice",
                            interaction_mode="voice",
                        )
                        _speak_via_voice_worker(response_text)
                except Exception:
                    _voice_ok = False
                    raise
                finally:
                    awareness_signals.record_interaction_ended(
                        session_id="voice",
                        modality="voice",
                        duration_seconds=time.monotonic() - _interaction_start,
                        ok=_voice_ok,
                    )

            elif msg.type == "text_cmd":
                tp: TextPayload = msg.payload
                snapshot = awareness_client.snapshot_with_fallback(state.snapshot())
                interaction_mode = _interaction_mode_for_source(tp.source, tp.session_id)
                runtime_context = _runtime_context_for_session(tp.session_id)
                if tp.extra_context:
                    # Ingress-supplied context for this turn only (e.g. sleep
                    # mode was just toggled before the model was invoked).
                    runtime_context = f"{runtime_context}\n\n{tp.extra_context}"
                decision = _classify_with_context(
                    tp.command,
                    source=tp.source,
                    session_id=tp.session_id,
                    runtime_context=runtime_context,
                    requested_mode=tp.requested_mode,
                    allow_model_route=VOICE_MODEL_ROUTING_ENABLED or interaction_mode != "voice",
                )
                decision = _enforce_foreground_for_sensitive_actions(tp.command, decision)
                # A typed command is presence evidence too, just a weaker one:
                # it proves someone is at a keyboard, not that they are in the
                # room, so it is reported with its own modality.
                _interaction_start = time.monotonic()
                _text_ok = True
                awareness_signals.record_presence(
                    modality="text", detail=f"source:{tp.source}"
                )
                awareness_signals.record_interaction_started(
                    session_id=tp.session_id,
                    modality="text",
                    source=tp.source,
                    routing_mode=decision.mode,
                )
                try:
                    if decision.mode == "status":
                        response_text = _run_agent_command(
                            tp.command,
                            gui_queue,
                            snapshot,
                            session_id=tp.session_id,
                            interaction_mode=interaction_mode,
                            extra_context=runtime_context,
                        )
                        if tp.reply_queue is not None:
                            tp.reply_queue.put(
                                {
                                    "ok": True,
                                    "mode": "foreground",
                                    "response": response_text,
                                    "session_id": tp.session_id,
                                    "source": tp.source,
                                }
                            )
                        continue

                    if decision.mode == "background":
                        job = job_manager.submit(
                            session_id=tp.session_id,
                            source=tp.source,
                            request_text=tp.command,
                            state_snapshot=snapshot,
                            interaction_mode=interaction_mode,
                            classification_reason=decision.reason,
                        )
                        if tp.reply_queue is not None:
                            tp.reply_queue.put(
                                _job_response(
                                    job,
                                    source=tp.source,
                                    response_text=decision.response,
                                )
                            )
                        continue

                    response_text = _run_agent_command(
                        tp.command,
                        gui_queue,
                        snapshot,
                        session_id=tp.session_id,
                        interaction_mode=interaction_mode,
                        extra_context=runtime_context,
                    )
                    if tp.reply_queue is not None:
                        tp.reply_queue.put(
                            {
                                "ok": True,
                                "mode": "foreground",
                                "response": response_text,
                                "session_id": tp.session_id,
                                "source": tp.source,
                            }
                        )
                except Exception as exc:
                    _text_ok = False
                    if tp.reply_queue is not None:
                        tp.reply_queue.put(
                            {
                                "ok": False,
                                "error": str(exc),
                                "session_id": tp.session_id,
                                "source": tp.source,
                            }
                        )
                finally:
                    awareness_signals.record_interaction_ended(
                        session_id=tp.session_id,
                        modality="text",
                        duration_seconds=time.monotonic() - _interaction_start,
                        ok=_text_ok,
                    )

            elif msg.type == "event":
                if msg.needs_llm:
                    snapshot = awareness_client.snapshot_with_fallback(state.snapshot())
                    _run_agent_command(
                        f"Event {msg.payload.name}: {msg.payload.data}",
                        gui_queue,
                        snapshot,
                        session_id=_event_session_id(msg.payload.data),
                        interaction_mode="text",
                    )

            elif msg.type == "ui":
                gui_queue.put(msg.payload)
    finally:
        job_manager.shutdown(wait=False)
