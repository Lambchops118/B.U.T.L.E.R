"""Inbound phone calls: Twilio -> Talos gate -> ElevenLabs voice agent <-> Talos.

ElevenLabs runs the conversation (its own LLM, STT and TTS); Talos decides who
gets through and supplies what it knows. Everything is served by this listener:

1. ``/phone/twilio/voice`` -- the Twilio number's voice webhook. Talos admits a
   call only when the caller is allowlisted *and* the carrier vouched for the
   number (SHAKEN/STIR ``StirVerstat`` of ``TN-Validation-Passed-A`` by
   default); everything else, including spoofed and most foreign calls, is
   rejected before ElevenLabs is involved. Admitted calls are handed to the
   agent through ElevenLabs' register-call API with a briefing (memory plus
   house situation) and a per-call token as dynamic variables.
2. ``/phone/tools/<name>`` -- ElevenLabs server tools called mid-call:
   ``recall``, ``house_status``, ``ask_talos``. Each request carries the call
   token, filled in by ElevenLabs from ``{{talos_call_token}}`` rather than by
   its LLM, so tools only answer for calls that passed the gate.
3. ``/phone/postcall`` -- the post-call webhook. The transcript is stored and
   written to memory, so the main agent learns what was said.

Like the SMS webhook, the listener binds locally and is exposed through one
public ingress (Tailscale Funnel); the text-agent server stays private. Twilio
requests need a valid Twilio signature; the rest need the shared bearer token
(or ElevenLabs' HMAC signature for the post-call webhook).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import secrets
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from butler.config import env_bool, env_int, load_environment
from butler.messages import EventPayload, Message, TextPayload
from butler.phone.provider import _parse_allowlist
from butler.sms.twilio import is_valid_signature


PHONE_TURN_CONTEXT = (
    "This request was relayed from a live phone call: a verified caller is talking "
    "to Talos's phone voice agent, which asked you this mid-call and will read your "
    "answer aloud. Reply in one to three short spoken sentences, plain text, no "
    "markdown or lists. Lead with the answer. Nothing is spoken in the room."
)
# The voice agent once explained "present via text" as phone-signal tracking.
PRESENCE_NOTE = (
    "(Presence lines come from recent interactions with Talos: 'via text' means "
    "a typed message, 'via voice' or 'via wake_word' means speech in the room. "
    "Talos does not track phones or location.)"
)
DEFAULT_ACCEPTED_ATTESTATIONS = "TN-Validation-Passed-A"
REJECT_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response><Reject/></Response>'
UNAVAILABLE_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?><Response>'
    "<Say>Sorry, Talos is unavailable right now.</Say><Hangup/></Response>"
)
MAX_BODY_BYTES = 256 * 1024
_CALL_TTL_SECONDS = 2 * 60 * 60
_SIGNATURE_TOLERANCE_SECONDS = 30 * 60

ToolResult = tuple[HTTPStatus, dict[str, Any]]
RegisterFn = Callable[[str, str, dict[str, str]], str]


@dataclass(frozen=True)
class PhoneInboundConfig:
    enabled: bool
    host: str
    port: int
    token: str
    webhook_secret: str
    allowed_callers: tuple[str, ...]
    accepted_attestations: tuple[str, ...]
    twilio_auth_token: str
    voice_public_url: str
    ask_timeout: float
    briefing_max_chars: int

    @classmethod
    def from_env(cls) -> "PhoneInboundConfig":
        load_environment()
        raw_attestations = os.getenv("BUTLER_PHONE_ACCEPTED_ATTESTATIONS", DEFAULT_ACCEPTED_ATTESTATIONS)
        return cls(
            enabled=env_bool("BUTLER_PHONE_INBOUND_ENABLED", False),
            host=os.getenv("BUTLER_PHONE_INBOUND_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=env_int("BUTLER_PHONE_INBOUND_PORT", 8431),
            token=os.getenv("BUTLER_PHONE_INBOUND_TOKEN", "").strip(),
            webhook_secret=os.getenv("BUTLER_PHONE_WEBHOOK_SECRET", "").strip(),
            allowed_callers=tuple(_parse_allowlist(os.getenv("BUTLER_PHONE_INBOUND_ALLOWED_CALLERS", ""))),
            accepted_attestations=tuple(item.strip() for item in raw_attestations.split(",") if item.strip()),
            twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
            voice_public_url=os.getenv("BUTLER_PHONE_VOICE_PUBLIC_URL", "").strip(),
            ask_timeout=float(os.getenv("BUTLER_PHONE_ASK_TIMEOUT", "15")),
            briefing_max_chars=max(400, env_int("BUTLER_PHONE_BRIEFING_MAX_CHARS", 2400)),
        )

    def missing_requirements(self) -> list[str]:
        """Settings without which the listener must not be exposed."""
        missing: list[str] = []
        if not self.token:
            missing.append("BUTLER_PHONE_INBOUND_TOKEN")
        if not self.allowed_callers:
            missing.append("BUTLER_PHONE_INBOUND_ALLOWED_CALLERS")
        if not self.twilio_auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.voice_public_url:
            missing.append("BUTLER_PHONE_VOICE_PUBLIC_URL")
        return missing


class _AdmittedCalls:
    """Call tokens issued at the gate. In memory: a restart ends tool access for live calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, tuple[str, float]] = {}

    def admit(self, caller: str) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._prune()
            self._calls[token] = (caller, time.monotonic())
        return token

    def caller_for(self, token: str) -> str | None:
        if not token:
            return None
        with self._lock:
            self._prune()
            entry = self._calls.get(token)
        return entry[0] if entry else None

    def _prune(self) -> None:
        now = time.monotonic()
        for key in [k for k, (_, admitted) in self._calls.items() if now - admitted > _CALL_TTL_SECONDS]:
            del self._calls[key]


class PhoneInboundServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        central_queue: queue.Queue,
        config: PhoneInboundConfig,
        *,
        register_fn: RegisterFn | None = None,
        situation_fn: Callable[[], str | None] | None = None,
        memory_store: Any = None,
    ) -> None:
        self.central_queue = central_queue
        self.config = config
        self.admitted = _AdmittedCalls()
        self._register_fn = register_fn
        self._situation_fn = situation_fn
        self._memory_store = memory_store
        super().__init__(server_address, PhoneInboundHandler)

    def register_call(self, from_number: str, to_number: str, dynamic_variables: dict[str, str]) -> str:
        if self._register_fn is not None:
            return self._register_fn(from_number, to_number, dynamic_variables)
        from butler.phone.service import get_phone_provider

        return get_phone_provider().register_inbound_call(  # type: ignore[attr-defined]
            from_number=from_number, to_number=to_number, dynamic_variables=dynamic_variables
        )

    def situation(self) -> str | None:
        if self._situation_fn is not None:
            return self._situation_fn()
        from butler.services import awareness_client

        try:
            return awareness_client.fetch_situation_text()
        except Exception as exc:
            print(f"[phone-inbound] awareness situation unavailable: {exc}")
            return None

    def memory(self) -> Any:
        if self._memory_store is not None:
            return self._memory_store
        from butler.memory import get_default_memory_store

        return get_default_memory_store()


class PhoneInboundHandler(BaseHTTPRequestHandler):
    server: PhoneInboundServer

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/phone/health":
            self._write_json(HTTPStatus.OK, {"ok": True})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Bad body length."})
            return
        raw = self.rfile.read(length)

        if path == "/phone/twilio/voice":
            self._handle_twilio_voice(raw)
            return

        if not self._authorized(parsed.query, raw):
            print(f"[phone-inbound] rejected unauthorized request to {path}")
            self._write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON."})
            return
        if not isinstance(body, dict):
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Body must be a JSON object."})
            return

        if path == "/phone/postcall":
            status, payload = handle_postcall(self.server, body)
        elif path.startswith("/phone/tools/"):
            status, payload = handle_tool(self.server, path.removeprefix("/phone/tools/"), body)
        else:
            status, payload = HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"}
        self._write_json(status, payload)

    def _handle_twilio_voice(self, raw: bytes) -> None:
        params = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        config = self.server.config
        signature = self.headers.get("X-Twilio-Signature", "")
        if not is_valid_signature(config.twilio_auth_token, config.voice_public_url, params, signature):
            print("[phone-inbound] rejected voice webhook: invalid Twilio signature")
            self._write_bytes(HTTPStatus.FORBIDDEN, b"", "text/plain")
            return
        twiml = handle_incoming_call(self.server, {key: values[0] for key, values in params.items() if values})
        self._write_bytes(HTTPStatus.OK, twiml.encode("utf-8"), "text/xml")

    def _authorized(self, query: str, raw: bytes) -> bool:
        config = self.server.config
        auth = self.headers.get("Authorization", "").strip()
        if config.token and auth.startswith("Bearer ") and hmac.compare_digest(
            auth.removeprefix("Bearer ").strip(), config.token
        ):
            return True
        query_token = str((parse_qs(query).get("token") or [""])[0]).strip()
        if config.token and query_token and hmac.compare_digest(query_token, config.token):
            return True
        signature = self.headers.get("ElevenLabs-Signature", "").strip()
        return bool(signature) and is_valid_elevenlabs_signature(config.webhook_secret, raw, signature)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._write_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _write_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def is_valid_elevenlabs_signature(secret: str, raw: bytes, header: str, *, now: float | None = None) -> bool:
    """Verify ElevenLabs' ``t=<unix>,v0=<hex hmac-sha256 of "t.body">`` header."""
    if not secret:
        return False
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    timestamp, provided = parts.get("t", ""), parts.get("v0", "")
    try:
        age = (now if now is not None else time.time()) - int(timestamp)
    except ValueError:
        return False
    if abs(age) > _SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def phone_session_id(caller: str) -> str:
    return f"phone:{caller}"


def build_briefing(server: PhoneInboundServer, caller: str) -> str:
    sections: list[str] = []
    try:
        memory_text = server.memory().get_prompt_memory(
            phone_session_id(caller), max_chars=server.config.briefing_max_chars
        )
    except Exception as exc:
        print(f"[phone-inbound] memory briefing failed: {exc}")
        memory_text = ""
    if memory_text:
        sections.append(memory_text)
    situation = server.situation()
    if situation:
        sections.append(f"Current house situation:\n{situation}\n{PRESENCE_NOTE}")
    briefing = "\n\n".join(sections).strip()
    limit = server.config.briefing_max_chars
    return briefing if len(briefing) <= limit else briefing[: limit - 3].rstrip() + "..."


def handle_incoming_call(server: PhoneInboundServer, params: dict[str, str]) -> str:
    """Gate one incoming call; returns the TwiML Twilio should run."""
    caller = str(params.get("From") or "").strip()
    called = str(params.get("To") or "").strip()
    attestation = str(params.get("StirVerstat") or "").strip()
    allowed = caller in set(server.config.allowed_callers)
    attested = attestation in set(server.config.accepted_attestations)
    # Logged for every call so the owner can see what their carriers actually send.
    print(
        f"[phone-inbound] incoming call from {_mask(caller)} "
        f"StirVerstat={attestation or 'none'} allowlisted={allowed}"
    )
    if not (allowed and attested):
        return REJECT_TWIML

    call_token = server.admitted.admit(caller)
    dynamic_variables = {
        "talos_call_token": call_token,
        "talos_session_id": phone_session_id(caller),
        "talos_briefing": build_briefing(server, caller),
    }
    try:
        return server.register_call(caller, called, dynamic_variables)
    except Exception as exc:
        print(f"[phone-inbound] ElevenLabs register-call failed: {exc}")
        return UNAVAILABLE_TWIML


def handle_tool(server: PhoneInboundServer, name: str, body: dict[str, Any]) -> ToolResult:
    caller = server.admitted.caller_for(str(body.get("call_token") or "").strip())
    if caller is None:
        print(f"[phone-inbound] tool {name} refused: unknown call token")
        return HTTPStatus.OK, _spoken(False, "I can't help with that on this line.", error="call_not_admitted")

    if name == "recall":
        return HTTPStatus.OK, _tool_recall(server, caller, str(body.get("query") or ""))
    if name == "house_status":
        situation = server.situation()
        if not situation:
            return HTTPStatus.OK, _spoken(False, "I can't reach the house status right now.", error="unavailable")
        return HTTPStatus.OK, {"ok": True, "status": situation}
    if name == "ask_talos":
        request = str(body.get("request") or "").strip()
        if not request:
            return HTTPStatus.OK, _spoken(False, "What should I ask Talos?", error="missing_request")
        print(f"[phone-inbound] ask_talos from {_mask(caller)}: {request[:80]!r}")
        return HTTPStatus.OK, _ask_main_agent(server, caller, request)
    return HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Unknown tool '{name}'."}


def _tool_recall(server: PhoneInboundServer, caller: str, query: str) -> dict[str, Any]:
    try:
        text = server.memory().get_prompt_memory(
            phone_session_id(caller), query, max_chars=server.config.briefing_max_chars
        )
    except Exception as exc:
        return _spoken(False, "My memory isn't available right now.", error=str(exc))
    if not text:
        return {"ok": True, "found": False, "memory": "", "message": "Nothing relevant in memory."}
    return {"ok": True, "found": True, "memory": text}


def _ask_main_agent(server: PhoneInboundServer, caller: str, request: str) -> dict[str, Any]:
    reply_queue: queue.Queue = queue.Queue(maxsize=1)
    server.central_queue.put(
        Message(
            type="text_cmd",
            payload=TextPayload(
                command=request,
                session_id=phone_session_id(caller),
                source="phone",
                reply_queue=reply_queue,
                requested_mode="foreground",
                extra_context=PHONE_TURN_CONTEXT,
            ),
        )
    )
    try:
        result = reply_queue.get(timeout=server.config.ask_timeout)
    except queue.Empty:
        # The turn keeps running and lands in the caller's session memory.
        return _spoken(
            False,
            "Talos is still working on that. Tell the caller it is in progress and they can ask again shortly.",
            error="timeout",
        )
    if not result.get("ok"):
        return _spoken(False, f"That failed: {result.get('error') or 'unknown error'}", error="agent_error")
    return {"ok": True, "answer": str(result.get("response") or "")}


def handle_postcall(server: PhoneInboundServer, body: dict[str, Any]) -> ToolResult:
    from butler.phone.service import ingest_phone_webhook_event

    try:
        record = ingest_phone_webhook_event(body)
    except Exception as exc:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)}
    if str(body.get("type") or "") == "post_call_transcription":
        server.central_queue.put(
            Message(
                type="event",
                payload=EventPayload(
                    name="phone_call_completed",
                    data={
                        "call_id": record.call_id,
                        "session_id": record.session_id or "phone:inbound",
                        "direction": record.direction,
                        "summary": record.summary or "",
                    },
                ),
                needs_llm=True,
            )
        )
    return HTTPStatus.OK, {"ok": True, "call_id": record.call_id}


def _spoken(ok: bool, message: str, *, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": ok, "message": message}
    if error:
        payload["error"] = error
    return payload


def _mask(number: str) -> str:
    return f"...{number[-4:]}" if len(number) > 4 else "unknown"


def start_phone_inbound_server(central_queue: queue.Queue) -> PhoneInboundServer | None:
    try:
        config = PhoneInboundConfig.from_env()
    except Exception as exc:
        print(f"Phone inbound listener not started: {exc}")
        return None
    if not config.enabled:
        return None
    missing = config.missing_requirements()
    if missing:
        print(f"Phone inbound listener not started; missing settings: {', '.join(missing)}")
        return None
    try:
        server = PhoneInboundServer((config.host, config.port), central_queue, config)
    except Exception as exc:
        print(f"Failed to start phone inbound listener: {exc}")
        return None
    threading.Thread(target=server.serve_forever, name="phone-inbound", daemon=True).start()
    print(
        f"Phone inbound listener on http://{config.host}:{config.port}/phone "
        f"({len(config.allowed_callers)} allowed caller(s); accepting {', '.join(config.accepted_attestations)})"
    )
    return server


def shutdown_phone_inbound_server(server: PhoneInboundServer | None) -> None:
    if server is None:
        return
    server.shutdown()
    server.server_close()
