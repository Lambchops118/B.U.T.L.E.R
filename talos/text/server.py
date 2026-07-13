from __future__ import annotations

import ipaddress
import json
import os
import queue
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from talos.agent import runtime as agent_runtime
from talos.config import env_bool, load_environment
from talos.jobs import get_default_job_store
from talos.messages import Message, TextPayload


DEFAULT_ALLOWED_NETWORKS = [
    "127.0.0.1/32",
    "::1/128",
    "100.64.0.0/10",
    "fd7a:115c:a1e0::/48",
]


def _requested_mode_from_body(body: dict[str, Any]) -> str:
    if body.get("background") is True:
        return "background"
    if body.get("background") is False:
        return "foreground"
    return str(body.get("mode") or "auto").strip() or "auto"


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int((query.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class TextServerConfig:
    enabled: bool
    host: str
    port: int
    api_token: str
    request_timeout: float
    terminal_request_timeout: float
    allowed_networks: tuple[Any, ...]

    @classmethod
    def from_env(cls) -> "TextServerConfig":
        load_environment()
        enabled = env_bool("TEXT_AGENT_ENABLED", True)
        host = os.getenv("TEXT_AGENT_HOST", "0.0.0.0").strip() or "0.0.0.0"
        port = int(os.getenv("TEXT_AGENT_PORT", "8420"))
        api_token = os.getenv("TEXT_AGENT_API_TOKEN", "").strip()
        request_timeout = float(os.getenv("TEXT_AGENT_TIMEOUT", "90"))
        terminal_request_timeout = float(os.getenv("TEXT_AGENT_TERMINAL_TIMEOUT", "0"))
        raw_networks = os.getenv(
            "TEXT_AGENT_ALLOWED_NETWORKS",
            ",".join(DEFAULT_ALLOWED_NETWORKS),
        )
        allowed_networks = tuple(
            ipaddress.ip_network(entry.strip(), strict=False)
            for entry in raw_networks.split(",")
            if entry.strip()
        )
        return cls(
            enabled=enabled,
            host=host,
            port=port,
            api_token=api_token,
            request_timeout=request_timeout,
            terminal_request_timeout=terminal_request_timeout,
            allowed_networks=allowed_networks,
        )


class TextAgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        central_queue: queue.Queue,
        config: TextServerConfig,
    ) -> None:
        super().__init__(server_address, TextAgentRequestHandler)
        self.central_queue = central_queue
        self.config = config


class TextAgentRequestHandler(BaseHTTPRequestHandler):
    server: TextAgentHTTPServer
    server_version = "TalosTextAgent/0.1"

    def do_GET(self) -> None:
        if not self._authorize_request(require_token=False):
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._write_html(HTTPStatus.OK, self._chat_page())
            return
        if path == "/health":
            self._write_json(HTTPStatus.OK, {"ok": True, "status": "healthy"})
            return
        if path.startswith("/jobs/"):
            if not self._authorize_request(require_token=True):
                return
            self._handle_get_job(unquote(path.removeprefix("/jobs/")))
            return
        if path.startswith("/sessions/") and path.endswith("/jobs"):
            if not self._authorize_request(require_token=True):
                return
            session_id = unquote(path.removeprefix("/sessions/")[: -len("/jobs")])
            query = parse_qs(parsed.query)
            self._handle_get_session_jobs(session_id, query)
            return
        if path.startswith("/sessions/") and path.endswith("/events"):
            if not self._authorize_request(require_token=True):
                return
            session_id = unquote(path.removeprefix("/sessions/")[: -len("/events")])
            query = parse_qs(parsed.query)
            self._handle_get_session_events(session_id, query)
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        if not self._authorize_request(require_token=True):
            return

        if self.path == "/chat":
            self._handle_chat()
            return
        if self.path == "/chat/stream":
            self._handle_chat_stream()
            return
        if self.path == "/sessions/reset":
            self._handle_reset_session()
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def log_message(self, fmt: str, *args: Any) -> None:
        path = urlparse(self.path).path
        if self.command == "GET" and path.startswith("/sessions/") and path.endswith("/events"):
            return
        print(f"[text-agent] {self.address_string()} - {fmt % args}")

    def _authorize_request(self, *, require_token: bool) -> bool:
        if not self._client_is_allowed():
            self._write_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Client is not on an allowed network."})
            return False

        api_token = self.server.config.api_token
        if not require_token or not api_token:
            return True

        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided = auth_header[7:].strip()
        else:
            provided = self.headers.get("X-API-Key", "").strip()

        if provided != api_token:
            self._send_bytes(
                HTTPStatus.UNAUTHORIZED,
                json.dumps({"ok": False, "error": "Unauthorized"}).encode("utf-8"),
                "application/json",
                extra_headers={"WWW-Authenticate": "Bearer"},
            )
            return False

        return True

    def _client_is_allowed(self) -> bool:
        try:
            client_ip = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return any(client_ip in network for network in self.server.config.allowed_networks)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        payload = self.rfile.read(content_length)
        if not payload:
            return {}
        body = json.loads(payload.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object.")
        return body

    def _handle_chat(self) -> None:
        try:
            body = self._read_json_body()
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        command = str(body.get("message") or body.get("command") or "").strip()
        if not command:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing 'message'."})
            return

        session_id = str(body.get("session_id") or f"text:{self.client_address[0]}").strip()
        source = str(body.get("source") or "http").strip()
        requested_mode = _requested_mode_from_body(body)

        reply_queue: queue.Queue = queue.Queue(maxsize=1)
        self.server.central_queue.put(
            Message(
                type="text_cmd",
                payload=TextPayload(
                    command=command,
                    session_id=session_id,
                    source=source,
                    reply_queue=reply_queue,
                    requested_mode=requested_mode,
                ),
            )
        )

        wait_timeout: float | None = self.server.config.request_timeout
        if source == "terminal":
            terminal_timeout = self.server.config.terminal_request_timeout
            wait_timeout = None if terminal_timeout <= 0 else terminal_timeout

        try:
            if wait_timeout is None:
                result = reply_queue.get()
            else:
                result = reply_queue.get(timeout=wait_timeout)
        except queue.Empty:
            self._write_json(
                HTTPStatus.GATEWAY_TIMEOUT,
                {"ok": False, "error": "Agent request timed out.", "session_id": session_id},
            )
            return

        status = HTTPStatus.OK if result.get("ok") else HTTPStatus.INTERNAL_SERVER_ERROR
        self._write_json(status, result)

    def _handle_chat_stream(self) -> None:
        """Foreground streaming turn as Server-Sent Events.

        Emits ``{"type": "delta", "text": ...}`` events as the model generates,
        then a terminal ``{"type": "done", "text": <full>}`` (or ``"error"``).
        Runs the agent directly in the foreground lane (bypassing the background
        job machinery) so the latency-sensitive voice path streams end to end.
        """
        try:
            body = self._read_json_body()
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        command = str(body.get("message") or body.get("command") or "").strip()
        if not command:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing 'message'."})
            return

        session_id = str(body.get("session_id") or "voice").strip() or "voice"
        source = str(body.get("source") or "voice").strip()
        snapshot = str(body.get("state_snapshot") or "no recent status")

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return

        full_parts: list[str] = []
        try:
            for delta in agent_runtime.run_command_stream(
                command,
                snapshot,
                session_id=session_id,
                interaction_mode="voice",
                runtime_lane="foreground",
            ):
                if not delta:
                    continue
                full_parts.append(delta)
                self._send_sse({"type": "delta", "text": delta})
            self._send_sse({"type": "done", "text": "".join(full_parts).strip()})
        except (BrokenPipeError, ConnectionResetError):
            print("[text-agent] stream client disconnected")
        except Exception as exc:  # noqa: BLE001 - report to client then end stream
            print(f"[text-agent] stream error: {exc}")
            self._send_sse({"type": "error", "error": str(exc)})

    def _send_sse(self, payload: dict[str, Any]) -> None:
        data = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(data)
        self.wfile.flush()

    def _handle_get_job(self, job_id: str) -> None:
        if not job_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing job id."})
            return
        job = get_default_job_store().get_job(job_id)
        if job is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Job not found.", "job_id": job_id})
            return
        self._write_json(HTTPStatus.OK, {"ok": True, "job": job.to_dict()})

    def _handle_get_session_jobs(self, session_id: str, query: dict[str, list[str]]) -> None:
        if not session_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing session id."})
            return
        limit = _query_int(query, "limit", 25)
        jobs = get_default_job_store().list_session_jobs(session_id, limit=limit)
        self._write_json(
            HTTPStatus.OK,
            {"ok": True, "session_id": session_id, "jobs": [job.to_dict() for job in jobs]},
        )

    def _handle_get_session_events(self, session_id: str, query: dict[str, list[str]]) -> None:
        if not session_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing session id."})
            return
        after_id = _query_int(query, "after_id", 0)
        limit = _query_int(query, "limit", 50)
        events = get_default_job_store().list_session_events(
            session_id,
            after_id=after_id,
            limit=limit,
        )
        latest_event_id = get_default_job_store().latest_session_event_id(session_id)
        self._write_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "session_id": session_id,
                "latest_event_id": latest_event_id,
                "events": [event.to_dict() for event in events],
            },
        )

    def _handle_reset_session(self) -> None:
        try:
            body = self._read_json_body()
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            self._write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Missing 'session_id'."})
            return

        agent_runtime.reset_session(session_id)
        self._write_json(HTTPStatus.OK, {"ok": True, "session_id": session_id})

    def _send_bytes(
        self,
        status: HTTPStatus,
        encoded: bytes,
        content_type: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> bool:
        try:
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)
            return True
        except (BrokenPipeError, ConnectionResetError):
            print("[text-agent] client disconnected before response could be delivered")
            return False

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self._send_bytes(status, encoded, "application/json")

    def _write_html(self, status: HTTPStatus, html: str) -> None:
        encoded = html.encode("utf-8")
        self._send_bytes(status, encoded, "text/html")

    @staticmethod
    def _chat_page() -> str:
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TALOS Text Agent</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #09110d;
      --panel: #102017;
      --panel-2: #15271d;
      --line: #244533;
      --text: #d9f6e2;
      --muted: #87ad93;
      --accent: #6bf7a3;
      --danger: #ff7a7a;
      --font: "Segoe UI", Tahoma, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--font);
      background:
        radial-gradient(circle at top, rgba(107, 247, 163, 0.08), transparent 35%),
        linear-gradient(180deg, #08100c 0%, #050805 100%);
      color: var(--text);
      min-height: 100vh;
    }
    .wrap {
      width: min(900px, calc(100vw - 32px));
      margin: 24px auto;
      padding: 20px;
      border: 1px solid var(--line);
      background: rgba(16, 32, 23, 0.92);
      backdrop-filter: blur(8px);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 24px;
      letter-spacing: 0.08em;
    }
    p { color: var(--muted); }
    .row {
      display: grid;
      gap: 12px;
      margin-bottom: 12px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }
    label {
      display: block;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    input, textarea, button {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
      padding: 12px;
      font: inherit;
    }
    textarea {
      min-height: 120px;
      resize: vertical;
    }
    button {
      cursor: pointer;
      background: linear-gradient(180deg, #193323 0%, #102217 100%);
    }
    button:hover { border-color: var(--accent); }
    #messages {
      margin-top: 18px;
      display: grid;
      gap: 12px;
    }
    .msg {
      border: 1px solid var(--line);
      padding: 12px;
      background: rgba(9, 17, 13, 0.8);
      white-space: pre-wrap;
    }
    .msg.user { border-left: 4px solid var(--accent); }
    .msg.agent { border-left: 4px solid #5eb8ff; }
    .msg.error { border-left: 4px solid var(--danger); }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>TALOS Text Agent</h1>
    <p>Designed to be reachable over Tailscale. Set a shared session ID if you want conversation continuity.</p>
    <div class="row">
      <div>
        <label for="session">Session ID</label>
        <input id="session" value="browser-session">
      </div>
      <div>
        <label for="token">API Token</label>
        <input id="token" type="password" placeholder="Optional if server requires it">
      </div>
    </div>
    <label for="prompt">Message</label>
    <textarea id="prompt" placeholder="Type a command or question for Monkey Butler."></textarea>
    <div class="row">
      <button id="send">Send</button>
      <button id="reset">Reset Session</button>
    </div>
    <div id="messages"></div>
  </div>
  <script>
    const messages = document.getElementById("messages");
    const prompt = document.getElementById("prompt");
    const session = document.getElementById("session");
    const token = document.getElementById("token");

    function addMessage(kind, text) {
      const node = document.createElement("div");
      node.className = `msg ${kind}`;
      node.textContent = text;
      messages.prepend(node);
    }

    async function postJson(path, payload) {
      const headers = {"Content-Type": "application/json"};
      if (token.value.trim()) {
        headers["Authorization"] = `Bearer ${token.value.trim()}`;
      }
      const response = await fetch(path, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    document.getElementById("send").addEventListener("click", async () => {
      const text = prompt.value.trim();
      if (!text) return;
      addMessage("user", text);
      prompt.value = "";
      try {
        const data = await postJson("/chat", {
          message: text,
          session_id: session.value.trim() || "browser-session",
          source: "browser",
        });
        const suffix = data.mode === "background" && data.job_id ? `\nJob ID: ${data.job_id}` : "";
        addMessage("agent", `${data.response || ""}${suffix}`);
      } catch (error) {
        addMessage("error", error.message);
      }
    });

    document.getElementById("reset").addEventListener("click", async () => {
      try {
        await postJson("/sessions/reset", {
          session_id: session.value.trim() || "browser-session",
        });
        addMessage("agent", "Session reset.");
      } catch (error) {
        addMessage("error", error.message);
      }
    });
  </script>
</body>
</html>
"""


def start_text_agent_server(central_queue: queue.Queue) -> TextAgentHTTPServer | None:
    try:
        config = TextServerConfig.from_env()
        if not config.enabled:
            print("Text agent server disabled by configuration.")
            return None

        print(f"Starting text agent server on {config.host}:{config.port}...")
        server = TextAgentHTTPServer((config.host, config.port), central_queue, config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        print(
            "Text agent server listening on "
            f"http://{config.host}:{config.port} "
            f"(allowed networks: {', '.join(str(n) for n in config.allowed_networks)})"
        )
        if not config.api_token:
            print("Text agent server has no API token configured; access is limited by the network allowlist only.")

        return server
    except Exception as exc:
        print(f"Failed to start text agent server: {exc}")
        return None


def shutdown_text_agent_server(server: TextAgentHTTPServer | None) -> None:
    if server is None:
        return
    server.shutdown()
    server.server_close()
