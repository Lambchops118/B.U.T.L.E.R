from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PHONE_DB_PATH = REPO_ROOT / "db" / "talos_phone.sqlite3"


@dataclass(frozen=True)
class PhoneCallRecord:
    call_id: str
    provider: str
    provider_call_id: str | None
    conversation_id: str | None
    agent_id: str | None
    session_id: str | None
    direction: str
    remote_number: str
    contact_name: str | None
    purpose: str | None
    brief_context: str | None
    status: str
    outcome: str | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    ended_at: str | None = None
    transcript: list[dict[str, Any]] | None = None
    summary: str | None = None
    last_error: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "provider": self.provider,
            "provider_call_id": self.provider_call_id,
            "conversation_id": self.conversation_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "direction": self.direction,
            "remote_number": self.remote_number,
            "contact_name": self.contact_name,
            "purpose": self.purpose,
            "brief_context": self.brief_context,
            "status": self.status,
            "outcome": self.outcome,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "transcript": self.transcript or [],
            "summary": self.summary,
            "last_error": self.last_error,
            "metadata": self.metadata or {},
        }


@dataclass(frozen=True)
class PhoneCallEvent:
    id: int
    call_id: str
    event_type: str
    message: str
    created_at: str
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "call_id": self.call_id,
            "event_type": self.event_type,
            "message": self.message,
            "created_at": self.created_at,
            "payload": self.payload or {},
        }


class PhoneCallStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        selected_path = db_path or os.getenv("TALOS_PHONE_DB_PATH") or DEFAULT_PHONE_DB_PATH
        self.db_path = Path(selected_path) if str(selected_path) != ":memory:" else None
        db_locator = ":memory:" if self.db_path is None else str(self.db_path)
        if self.db_path is not None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_locator, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.db_path is not None:
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_call(
        self,
        *,
        call_id: str,
        provider: str,
        direction: str,
        remote_number: str,
        status: str,
        provider_call_id: str | None = None,
        conversation_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        contact_name: str | None = None,
        purpose: str | None = None,
        brief_context: str | None = None,
        outcome: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        transcript: list[dict[str, Any]] | None = None,
        summary: str | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PhoneCallRecord:
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO phone_calls (
                    call_id, provider, provider_call_id, conversation_id, agent_id,
                    session_id, direction, remote_number, contact_name, purpose,
                    brief_context, status, outcome, created_at, updated_at, started_at,
                    ended_at, transcript_json, summary, last_error, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required_text(call_id, "call_id"),
                    _required_text(provider, "provider"),
                    _nullable_text(provider_call_id),
                    _nullable_text(conversation_id),
                    _nullable_text(agent_id),
                    _nullable_text(session_id),
                    _required_text(direction, "direction"),
                    _required_text(remote_number, "remote_number"),
                    _nullable_text(contact_name),
                    _nullable_text(purpose),
                    _nullable_text(brief_context),
                    _required_text(status, "status"),
                    _nullable_text(outcome),
                    now,
                    now,
                    _nullable_text(started_at),
                    _nullable_text(ended_at),
                    _json_dumps(transcript or []),
                    _nullable_text(summary),
                    _nullable_text(last_error),
                    _json_dumps(metadata or {}),
                ),
            )
            self._conn.commit()
        record = self.get_call(call_id)
        if record is None:
            raise KeyError(call_id)
        return record

    def update_call(self, call_id: str, **fields: Any) -> PhoneCallRecord:
        if not fields:
            record = self.get_call(call_id)
            if record is None:
                raise KeyError(call_id)
            return record

        allowed = {
            "provider",
            "provider_call_id",
            "conversation_id",
            "agent_id",
            "session_id",
            "direction",
            "remote_number",
            "contact_name",
            "purpose",
            "brief_context",
            "status",
            "outcome",
            "started_at",
            "ended_at",
            "transcript",
            "summary",
            "last_error",
            "metadata",
        }
        assignments = ["updated_at = ?"]
        params: list[Any] = [_utc_now()]
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unsupported phone call field: {key}")
            column = key
            if key == "transcript":
                column = "transcript_json"
                value = _json_dumps(value or [])
            elif key == "metadata":
                column = "metadata_json"
                value = _json_dumps(value or {})
            elif key in {"provider", "direction", "remote_number", "status"}:
                value = _required_text(str(value or ""), key)
            else:
                value = _nullable_text(value)
            assignments.append(f"{column} = ?")
            params.append(value)

        params.append(_required_text(call_id, "call_id"))
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE phone_calls SET {', '.join(assignments)} WHERE call_id = ?",
                params,
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(call_id)

        record = self.get_call(call_id)
        if record is None:
            raise KeyError(call_id)
        return record

    def upsert_snapshot(self, snapshot: dict[str, Any]) -> PhoneCallRecord:
        call_id = _required_text(str(snapshot.get("call_id") or snapshot.get("conversation_id") or ""), "call_id")
        existing = self.get_call(call_id)
        if existing is None:
            return self.create_call(
                call_id=call_id,
                provider=str(snapshot.get("provider") or "elevenlabs_twilio"),
                provider_call_id=_nullable_text(snapshot.get("provider_call_id")),
                conversation_id=_nullable_text(snapshot.get("conversation_id") or call_id),
                agent_id=_nullable_text(snapshot.get("agent_id")),
                session_id=_nullable_text(snapshot.get("session_id")),
                direction=str(snapshot.get("direction") or "unknown"),
                remote_number=str(snapshot.get("remote_number") or "unknown"),
                contact_name=_nullable_text(snapshot.get("contact_name")),
                purpose=_nullable_text(snapshot.get("purpose")),
                brief_context=_nullable_text(snapshot.get("brief_context")),
                status=str(snapshot.get("status") or "received"),
                outcome=_nullable_text(snapshot.get("outcome")),
                started_at=_nullable_text(snapshot.get("started_at")),
                ended_at=_nullable_text(snapshot.get("ended_at")),
                transcript=_normalize_transcript(snapshot.get("transcript")),
                summary=_nullable_text(snapshot.get("summary")),
                last_error=_nullable_text(snapshot.get("last_error")),
                metadata=_normalize_json_object(snapshot.get("metadata")),
            )
        return self.update_call(
            call_id,
            provider=str(snapshot.get("provider") or existing.provider),
            provider_call_id=snapshot.get("provider_call_id", existing.provider_call_id),
            conversation_id=snapshot.get("conversation_id", existing.conversation_id),
            agent_id=snapshot.get("agent_id", existing.agent_id),
            session_id=snapshot.get("session_id", existing.session_id),
            direction=str(snapshot.get("direction") or existing.direction),
            remote_number=str(snapshot.get("remote_number") or existing.remote_number),
            contact_name=snapshot.get("contact_name", existing.contact_name),
            purpose=snapshot.get("purpose", existing.purpose),
            brief_context=snapshot.get("brief_context", existing.brief_context),
            status=str(snapshot.get("status") or existing.status),
            outcome=snapshot.get("outcome", existing.outcome),
            started_at=snapshot.get("started_at", existing.started_at),
            ended_at=snapshot.get("ended_at", existing.ended_at),
            transcript=snapshot.get("transcript", existing.transcript or []),
            summary=snapshot.get("summary", existing.summary),
            last_error=snapshot.get("last_error", existing.last_error),
            metadata=snapshot.get("metadata", existing.metadata or {}),
        )

    def add_event(
        self,
        *,
        call_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> PhoneCallEvent:
        now = _utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO phone_call_events (
                    call_id, event_type, message, created_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _required_text(call_id, "call_id"),
                    _required_text(event_type, "event_type"),
                    _required_text(message, "message"),
                    now,
                    _json_dumps(payload or {}),
                ),
            )
            self._conn.commit()
            event_id = int(cursor.lastrowid)
        return PhoneCallEvent(
            id=event_id,
            call_id=call_id,
            event_type=event_type,
            message=message,
            created_at=now,
            payload=payload or {},
        )

    def get_call(self, call_id: str) -> PhoneCallRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM phone_calls WHERE call_id = ?",
                (_required_text(call_id, "call_id"),),
            ).fetchone()
        return _call_from_row(row) if row else None

    def find_call_by_provider_call_id(self, provider_call_id: str) -> PhoneCallRecord | None:
        provider_call_id = _required_text(provider_call_id, "provider_call_id")
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM phone_calls WHERE provider_call_id = ?",
                (provider_call_id,),
            ).fetchone()
        return _call_from_row(row) if row else None

    def list_recent_calls(self, *, limit: int = 25) -> list[PhoneCallRecord]:
        limit = max(1, int(limit))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM phone_calls
                ORDER BY updated_at DESC, call_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_call_from_row(row) for row in rows]

    def list_calls_updated_after(
        self,
        updated_after: str | None = None,
        *,
        limit: int = 50,
    ) -> list[PhoneCallRecord]:
        limit = max(1, int(limit))
        with self._lock:
            if updated_after:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM phone_calls
                    WHERE updated_at > ?
                    ORDER BY updated_at ASC, call_id ASC
                    LIMIT ?
                    """,
                    (_required_text(updated_after, "updated_after"), limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM phone_calls
                    ORDER BY updated_at DESC, call_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [_call_from_row(row) for row in rows]

    def list_call_events(self, call_id: str, *, limit: int = 100) -> list[PhoneCallEvent]:
        limit = max(1, int(limit))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM phone_call_events
                WHERE call_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (_required_text(call_id, "call_id"), limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def latest_updated_at(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(updated_at) AS latest_updated_at FROM phone_calls"
            ).fetchone()
        if row is None:
            return None
        latest = str(row["latest_updated_at"] or "").strip()
        return latest or None

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS phone_calls (
                call_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_call_id TEXT,
                conversation_id TEXT,
                agent_id TEXT,
                session_id TEXT,
                direction TEXT NOT NULL,
                remote_number TEXT NOT NULL,
                contact_name TEXT,
                purpose TEXT,
                brief_context TEXT,
                status TEXT NOT NULL,
                outcome TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                transcript_json TEXT NOT NULL DEFAULT '[]',
                summary TEXT,
                last_error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_phone_calls_updated
            ON phone_calls(updated_at DESC, call_id DESC);

            CREATE INDEX IF NOT EXISTS idx_phone_calls_provider_call_id
            ON phone_calls(provider_call_id);

            CREATE INDEX IF NOT EXISTS idx_phone_calls_session_id
            ON phone_calls(session_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS phone_call_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(call_id) REFERENCES phone_calls(call_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_phone_call_events_call_id
            ON phone_call_events(call_id, id);
            """
        )
        self._conn.commit()


_default_store: PhoneCallStore | None = None
_default_store_lock = threading.Lock()


def get_default_phone_store() -> PhoneCallStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = PhoneCallStore()
        return _default_store


def reset_default_phone_store() -> None:
    global _default_store
    with _default_store_lock:
        if _default_store is not None:
            _default_store.close()
        _default_store = None


def _call_from_row(row: sqlite3.Row) -> PhoneCallRecord:
    return PhoneCallRecord(
        call_id=str(row["call_id"]),
        provider=str(row["provider"]),
        provider_call_id=_nullable_text(row["provider_call_id"]),
        conversation_id=_nullable_text(row["conversation_id"]),
        agent_id=_nullable_text(row["agent_id"]),
        session_id=_nullable_text(row["session_id"]),
        direction=str(row["direction"]),
        remote_number=str(row["remote_number"]),
        contact_name=_nullable_text(row["contact_name"]),
        purpose=_nullable_text(row["purpose"]),
        brief_context=_nullable_text(row["brief_context"]),
        status=str(row["status"]),
        outcome=_nullable_text(row["outcome"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=_nullable_text(row["started_at"]),
        ended_at=_nullable_text(row["ended_at"]),
        transcript=_normalize_transcript(_json_loads(str(row["transcript_json"] or "[]"), [])),
        summary=_nullable_text(row["summary"]),
        last_error=_nullable_text(row["last_error"]),
        metadata=_normalize_json_object(_json_loads(str(row["metadata_json"] or "{}"), {})),
    )


def _event_from_row(row: sqlite3.Row) -> PhoneCallEvent:
    return PhoneCallEvent(
        id=int(row["id"]),
        call_id=str(row["call_id"]),
        event_type=str(row["event_type"]),
        message=str(row["message"]),
        created_at=str(row["created_at"]),
        payload=_normalize_json_object(_json_loads(str(row["payload_json"] or "{}"), {})),
    )


def _required_text(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty.")
    return text


def _nullable_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _normalize_transcript(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(dict(item))
    return normalized


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
