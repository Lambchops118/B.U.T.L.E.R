from __future__ import annotations

import argparse
import os
import sys
import threading

from butler.text.service_client import (
    check_health,
    get_job,
    list_session_events,
    list_session_jobs,
    reset_session,
    send_message_payload,
)

DEFAULT_URL = os.getenv("BUTLER_TEXT_AGENT_URL", "http://127.0.0.1:8420")
DEFAULT_TOKEN = os.getenv("BUTLER_TEXT_AGENT_TOKEN", os.getenv("TEXT_AGENT_API_TOKEN", ""))
DEFAULT_SESSION_ID = os.getenv("BUTLER_TEXT_AGENT_SESSION", "main-pc")
DEFAULT_TIMEOUT = float(os.getenv("BUTLER_TEXT_AGENT_CLIENT_TIMEOUT", "30"))
DEFAULT_MESSAGE_TIMEOUT = float(os.getenv("BUTLER_TEXT_AGENT_TERMINAL_CLIENT_TIMEOUT", "0"))
DEFAULT_EVENT_POLL_SECONDS = float(os.getenv("BUTLER_TEXT_AGENT_EVENT_POLL_SECONDS", "2"))


def run_repl(
    base_url: str,
    token: str,
    session_id: str,
    timeout: float,
    message_timeout: float,
) -> int:
    print(f"Connected target: {base_url}")
    print(f"Session ID: {session_id}")
    print("Commands: /reset, /health, /jobs, /job <job_id>, /exit")

    print(r"""
 __  __             _                ____        _   _           
|  \/  | ___  _ __ | | _____ _   _  | __ ) _   _| |_| | ___ _ __ 
| |\/| |/ _ \| '_ \| |/ / _ \ | | | |  _ \| | | | __| |/ _ \ '__|
| |  | | (_) | | | |   <  __/ |_| | | |_) | |_| | |_| |  __/ |   
|_|  |_|\___/|_| |_|_|\_\___|\__, | |____/ \__,_|\__|_|\___|_|   
                             |___/                             
          
""")

    stop_events = threading.Event()
    poll_thread = threading.Thread(
        target=_poll_session_events,
        args=(base_url, token, session_id, timeout, stop_events),
        daemon=True,
    )
    poll_thread.start()
    try:
        while True:
            try:
                user_input = input("butler> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if not user_input:
                continue
            if user_input in {"/exit", "/quit"}:
                return 0
            if user_input == "/reset":
                reset_session(session_id, base_url=base_url, token=token, timeout=timeout)
                print("Session reset.")
                continue
            if user_input == "/health":
                print(f"Health: {check_health(base_url=base_url, timeout=timeout)}")
                continue
            if user_input == "/jobs":
                print(_format_jobs_payload(list_session_jobs(session_id, base_url=base_url, token=token, timeout=timeout)))
                continue
            if user_input.startswith("/job "):
                job_id = user_input.split(maxsplit=1)[1].strip()
                print(_format_job_payload(get_job(job_id, base_url=base_url, token=token, timeout=timeout)))
                continue

            payload = send_message_payload(
                user_input,
                session_id=session_id,
                source="terminal",
                base_url=base_url,
                token=token,
                timeout=message_timeout,
            )
            print(_format_chat_payload(payload))
    finally:
        stop_events.set()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal client for the Butler text agent.")
    parser.add_argument("message", nargs="*", help="Optional one-shot message to send.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL of the Butler text agent.")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Bearer token for the Butler text agent.")
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID, help="Conversation session ID.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--message-timeout",
        type=float,
        default=DEFAULT_MESSAGE_TIMEOUT,
        help="HTTP timeout in seconds for terminal chat messages. Use 0 to wait indefinitely.",
    )
    parser.add_argument("--health", action="store_true", help="Check server health and exit.")
    parser.add_argument("--reset", action="store_true", help="Reset the session and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        if args.health:
            print(f"Health: {check_health(base_url=args.url, timeout=args.timeout)}")
            return 0

        if args.reset:
            reset_session(
                args.session_id,
                base_url=args.url,
                token=args.token,
                timeout=args.timeout,
            )
            print("Session reset.")
            return 0

        if args.message:
            payload = send_message_payload(
                " ".join(args.message).strip(),
                session_id=args.session_id,
                source="terminal",
                base_url=args.url,
                token=args.token,
                timeout=args.message_timeout,
            )
            print(_format_chat_payload(payload))
            return 0

        return run_repl(
            args.url,
            args.token,
            args.session_id,
            args.timeout,
            args.message_timeout,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _format_chat_payload(payload: dict) -> str:
    response = str(payload.get("response", "")).strip()
    if payload.get("mode") == "background" and payload.get("job_id"):
        return f"{response}\nJob ID: {payload['job_id']}"
    return response


def _format_jobs_payload(payload: dict) -> str:
    jobs = payload.get("jobs") or []
    if not jobs:
        return "No background jobs recorded for this session."
    return "\n\n".join(_format_job_dict(job) for job in jobs[:10])


def _format_job_payload(payload: dict) -> str:
    job = payload.get("job") or {}
    if not job:
        return "Job not found."
    return _format_job_dict(job, include_result_payload=True)


def _format_job_dict(job: dict, *, include_result_payload: bool = False) -> str:
    lines = [f"{job.get('job_id')}: {job.get('status')}"]
    request_text = str(job.get("request_text") or "").strip()
    if request_text:
        lines.append(f"Request: {_compact_text(request_text, 180)}")
    progress = str(job.get("progress_message") or "").strip()
    if progress:
        lines.append(f"Progress: {progress}")
    result_summary = str(job.get("result_summary") or "").strip()
    if result_summary:
        lines.append(f"Result: {result_summary}")
    error = str(job.get("error_message") or "").strip()
    if error:
        lines.append(f"Error: {error}")
    result_payload = job.get("result_payload") or {}
    response = str(result_payload.get("response") or "").strip() if isinstance(result_payload, dict) else ""
    if include_result_payload and response and response != result_summary:
        lines.append(f"Full response: {response}")
    return "\n".join(lines)


def _poll_session_events(
    base_url: str,
    token: str,
    session_id: str,
    timeout: float,
    stop_events: threading.Event,
) -> None:
    after_id = _initial_event_cursor(base_url, token, session_id, timeout)
    synced = after_id > 0
    while not stop_events.wait(max(0.5, DEFAULT_EVENT_POLL_SECONDS)):
        try:
            payload = list_session_events(
                session_id,
                after_id=after_id,
                base_url=base_url,
                token=token,
                timeout=timeout,
            )
        except Exception:
            continue

        events = payload.get("events") or []
        if not synced:
            after_id = _latest_event_cursor(payload, minimum=after_id)
            synced = True
            continue

        for event in events:
            try:
                after_id = max(after_id, int(event.get("id") or 0))
            except (TypeError, ValueError):
                pass
            if event.get("event_type") not in {"completed", "failed", "interrupted"}:
                continue
            print(f"\n[background] {event.get('job_id')} {event.get('event_type')}: {event.get('message')}\nbutler> ", end="", flush=True)


def _initial_event_cursor(base_url: str, token: str, session_id: str, timeout: float) -> int:
    try:
        payload = list_session_events(
            session_id,
            base_url=base_url,
            token=token,
            timeout=timeout,
        )
    except Exception:
        return 0
    return _latest_event_cursor(payload)


def _latest_event_cursor(payload: dict, *, minimum: int = 0) -> int:
    events = payload.get("events") or []
    cursor = _max_event_id(events, minimum=minimum)
    try:
        return max(cursor, int(payload.get("latest_event_id") or 0))
    except (TypeError, ValueError):
        return cursor


def _max_event_id(events: list[dict], *, minimum: int = 0) -> int:
    cursor = max(0, int(minimum))
    for event in events:
        try:
            cursor = max(cursor, int(event.get("id") or 0))
        except (TypeError, ValueError):
            continue
    return cursor


def _compact_text(value: str, limit: int) -> str:
    compacted = " ".join(str(value or "").split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 3].rsplit(" ", 1)[0] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
