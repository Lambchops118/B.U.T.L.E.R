"""Inbound SMS webhook: Twilio -> agent runtime -> SMS reply.

The listener binds locally and is exposed to Twilio through a single public
ingress (e.g. Tailscale Funnel). Only this webhook is public; the text-agent
server stays private. Every request must carry a valid Twilio signature and
come from an allowlisted sender before anything reaches the agent.

Texts go through the same central queue as typed chat, so they get the same
routing, tools, and action safeguards. Replies are sent asynchronously through
the Twilio REST API because agent turns routinely outlast Twilio's webhook
timeout. Nothing here speaks aloud.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs

from butler.messages import Message, TextPayload
from butler.sms.twilio import SmsConfig, is_valid_signature, send_sms


SMS_TURN_CONTEXT = (
    "This request arrived as an SMS text message from the owner's phone; the reply "
    "is sent back as a text. Keep it short and plain text: no markdown, headings, "
    "or code blocks. Lead with the outcome. Nothing is spoken aloud in the room."
)
EMPTY_TWIML = b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
MAX_BODY_BYTES = 64 * 1024
_SEEN_SID_LIMIT = 500
_JOB_POLL_SECONDS = 2.0

SendFn = Callable[[str, str], Any]


class SmsWebhookServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        central_queue: queue.Queue,
        config: SmsConfig,
        *,
        send: SendFn | None = None,
    ) -> None:
        self.central_queue = central_queue
        self.config = config
        self.send = send or (lambda to, body: send_sms(config, to, body))
        self._seen_sids: OrderedDict[str, None] = OrderedDict()
        self._seen_lock = threading.Lock()
        super().__init__(server_address, SmsWebhookHandler)

    def claim_message_sid(self, sid: str) -> bool:
        """False when this MessageSid was already accepted (Twilio retry)."""
        if not sid:
            return True
        with self._seen_lock:
            if sid in self._seen_sids:
                return False
            self._seen_sids[sid] = None
            while len(self._seen_sids) > _SEEN_SID_LIMIT:
                self._seen_sids.popitem(last=False)
            return True


class SmsWebhookHandler(BaseHTTPRequestHandler):
    server: SmsWebhookServer

    def do_GET(self) -> None:
        self._reply(HTTPStatus.NOT_FOUND, b"")

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0].rstrip("/") not in ("", "/sms"):
            self._reply(HTTPStatus.NOT_FOUND, b"")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(HTTPStatus.BAD_REQUEST, b"")
            return
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        params = parse_qs(raw, keep_blank_values=True)

        config = self.server.config
        signature = self.headers.get("X-Twilio-Signature", "")
        if not is_valid_signature(config.auth_token, config.public_url, params, signature):
            print("[sms] rejected webhook: invalid Twilio signature")
            self._reply(HTTPStatus.FORBIDDEN, b"")
            return

        sender = _first(params, "From")
        body = _first(params, "Body").strip()
        sid = _first(params, "MessageSid")
        if sender not in config.allowed_senders:
            # No reply: answering unknown numbers costs money and confirms the
            # number is live.
            print(f"[sms] ignored message from non-allowlisted sender {_mask(sender)}")
            self._twiml_ok()
            return
        if not self.server.claim_message_sid(sid):
            self._twiml_ok()
            return
        self._twiml_ok()
        if not body:
            return
        threading.Thread(
            target=handle_inbound_text,
            args=(self.server, sender, body),
            name=f"sms-{sid or 'message'}",
            daemon=True,
        ).start()

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _twiml_ok(self) -> None:
        self._reply(HTTPStatus.OK, EMPTY_TWIML, content_type="text/xml")

    def _reply(self, status: HTTPStatus, body: bytes, *, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


def handle_inbound_text(server: SmsWebhookServer, sender: str, body: str) -> None:
    """Run one texted command through the agent and text back the result."""
    session_id = f"sms:{sender}"
    print(f"[sms] command from {_mask(sender)}: {body[:80]!r}")
    reply_queue: queue.Queue = queue.Queue(maxsize=1)
    server.central_queue.put(
        Message(
            type="text_cmd",
            payload=TextPayload(
                command=body,
                session_id=session_id,
                source="sms",
                reply_queue=reply_queue,
                extra_context=SMS_TURN_CONTEXT,
            ),
        )
    )
    try:
        result = reply_queue.get(timeout=server.config.reply_timeout)
    except queue.Empty:
        _safe_send(server, sender, "Sorry, that took too long and I stopped waiting. It may still finish.")
        return

    if not result.get("ok"):
        _safe_send(server, sender, f"That failed: {result.get('error') or 'unknown error'}")
        return
    _safe_send(server, sender, str(result.get("response") or ""))

    job_id = str(result.get("job_id") or "") if result.get("mode") == "background" else ""
    if job_id:
        _follow_background_job(server, sender, job_id)


def _follow_background_job(server: SmsWebhookServer, sender: str, job_id: str) -> None:
    from butler.jobs import TERMINAL_STATUSES, get_default_job_store

    deadline = time.monotonic() + server.config.job_follow_up_timeout
    store = get_default_job_store()
    while time.monotonic() < deadline:
        job = store.get_job(job_id)
        if job is not None and job.status in TERMINAL_STATUSES:
            if job.status == "succeeded":
                response = (job.result_payload or {}).get("response") or job.result_summary or "Done."
                _safe_send(server, sender, str(response))
            else:
                detail = job.error_message or job.status
                _safe_send(server, sender, f"That didn't finish ({job.status}): {detail}")
            return
        time.sleep(_JOB_POLL_SECONDS)
    _safe_send(server, sender, "Still working on that; I'll stop tracking it by text.")


def _safe_send(server: SmsWebhookServer, to_number: str, body: str) -> None:
    try:
        server.send(to_number, body)
    except Exception as exc:
        print(f"[sms] reply to {_mask(to_number)} failed: {exc}")


def _first(params: dict[str, list[str]], key: str) -> str:
    values = params.get(key) or [""]
    return str(values[0] or "")


def _mask(number: str) -> str:
    return f"...{number[-4:]}" if len(number) > 4 else "unknown"


def start_sms_server(central_queue: queue.Queue) -> SmsWebhookServer | None:
    try:
        config = SmsConfig.from_env()
    except Exception as exc:
        print(f"SMS webhook not started: {exc}")
        return None
    if not config.enabled:
        return None
    missing = config.missing_requirements()
    if missing:
        print(f"SMS webhook not started; missing settings: {', '.join(missing)}")
        return None
    try:
        server = SmsWebhookServer((config.host, config.port), central_queue, config)
    except Exception as exc:
        print(f"Failed to start SMS webhook: {exc}")
        return None
    threading.Thread(target=server.serve_forever, name="sms-webhook", daemon=True).start()
    print(
        f"SMS webhook listening on http://{config.host}:{config.port} "
        f"(public URL {config.public_url}, {len(config.allowed_senders)} allowed sender(s))"
    )
    return server


def shutdown_sms_server(server: SmsWebhookServer | None) -> None:
    if server is None:
        return
    server.shutdown()
    server.server_close()
