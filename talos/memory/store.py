from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEMORY_DB_PATH = REPO_ROOT / "db" / "talos_memory.sqlite3"
DEFAULT_PROJECT_ID = "Talos"
MAX_QUERY_TOKENS = 8


@dataclass(frozen=True)
class MemoryFact:
    id: int
    scope: str
    key: str
    value: str
    salience: int
    updated_at: str


class MemoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        selected_path = db_path or os.getenv("TALOS_MEMORY_DB_PATH") or DEFAULT_MEMORY_DB_PATH
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

    def record_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session_id = _required_text(session_id, "session_id")
        now = _utc_now()
        metadata_json = _json_dumps(metadata or {})
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, title, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    title = COALESCE(excluded.title, sessions.title),
                    updated_at = excluded.updated_at,
                    metadata_json = CASE
                        WHEN excluded.metadata_json != '{}' THEN excluded.metadata_json
                        ELSE sessions.metadata_json
                    END
                """,
                (session_id, title, now, now, metadata_json),
            )
            self._conn.commit()

    def record_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        session_id = _required_text(session_id, "session_id")
        role = _required_text(role, "role")
        content = _required_text(content, "content")
        self.record_session(session_id)
        now = _utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO messages (session_id, role, content, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, content, now, _json_dumps(metadata or {})),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.record_message(session_id, "user", user_message, metadata=metadata)
        if assistant_message.strip():
            self.record_message(session_id, "assistant", assistant_message, metadata=metadata)
        self.refresh_session_summary(session_id)

    def refresh_session_summary(self, session_id: str, *, message_limit: int = 8) -> str:
        session_id = _required_text(session_id, "session_id")
        message_limit = max(1, int(message_limit))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT role, content
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, message_limit),
            ).fetchall()

        recent_rows = list(reversed(rows))
        if not recent_rows:
            return ""

        lines = ["Recent session turns:"]
        for row in recent_rows:
            role = str(row["role"]).strip().title() or "Message"
            content = _compact_text(str(row["content"]), limit=260)
            lines.append(f"- {role}: {content}")

        summary = "\n".join(lines)
        self.upsert_summary("session", session_id, summary)
        return summary

    def clear_session(self, session_id: str) -> None:
        """Clear conversational context while preserving durable facts.

        Session-scoped facts are explicit long-term memory and are therefore
        intentionally not deleted by a conversation reset.
        """
        session_id = _required_text(session_id, "session_id")
        with self._lock:
            self._conn.execute(
                "DELETE FROM summaries WHERE scope = 'session' AND scope_id = ?",
                (session_id,),
            )
            self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            self._conn.commit()

    def upsert_fact(
        self,
        scope: str,
        key: str,
        value: str,
        *,
        salience: int = 5,
        source_session_id: str | None = None,
    ) -> None:
        scope = _required_text(scope, "scope")
        key = _required_text(key, "key")
        value = _required_text(value, "value")
        salience = max(1, min(10, int(salience)))
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO facts (
                    scope, key, value, salience, source_session_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, key) DO UPDATE SET
                    value = excluded.value,
                    salience = excluded.salience,
                    source_session_id = COALESCE(excluded.source_session_id, facts.source_session_id),
                    updated_at = excluded.updated_at
                """,
                (scope, key, value, salience, source_session_id, now, now),
            )
            self._conn.commit()

    def delete_fact(self, scope: str, key: str) -> bool:
        scope = _required_text(scope, "scope")
        key = _required_text(key, "key")
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM facts WHERE scope = ? AND key = ?",
                (scope, key),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def upsert_summary(self, scope: str, scope_id: str, summary: str) -> None:
        scope = _required_text(scope, "scope")
        scope_id = _required_text(scope_id, "scope_id")
        summary = _required_text(summary, "summary")
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO summaries (scope, scope_id, summary, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope, scope_id) DO UPDATE SET
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """,
                (scope, scope_id, summary, now),
            )
            self._conn.commit()

    def search_facts(self, query_text: str = "", *, limit: int = 8) -> list[MemoryFact]:
        limit = max(1, int(limit))
        tokens = _query_tokens(query_text)
        with self._lock:
            rows: list[sqlite3.Row]
            if tokens:
                clauses = " OR ".join(["lower(key || ' ' || value) LIKE ?"] * len(tokens))
                params: list[Any] = [f"%{token}%" for token in tokens]
                params.append(limit)
                rows = self._conn.execute(
                    f"""
                    SELECT id, scope, key, value, salience, updated_at
                    FROM facts
                    WHERE {clauses}
                    ORDER BY salience DESC, updated_at DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
                if rows:
                    return [_fact_from_row(row) for row in rows]

            rows = self._conn.execute(
                """
                SELECT id, scope, key, value, salience, updated_at
                FROM facts
                ORDER BY salience DESC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [_fact_from_row(row) for row in rows]

    def get_prompt_memory(
        self,
        session_id: str,
        query_text: str = "",
        *,
        max_chars: int = 1600,
        project_id: str | None = None,
    ) -> str:
        session_id = _required_text(session_id, "session_id")
        project_id = (project_id or os.getenv("TALOS_MEMORY_PROJECT_ID") or DEFAULT_PROJECT_ID).strip()
        summary_specs = [
            ("user", "default", "User summary"),
            ("project", project_id, "Project summary"),
            ("session", session_id, "Active session summary"),
        ]

        lines: list[str] = []
        with self._lock:
            for scope, scope_id, label in summary_specs:
                row = self._conn.execute(
                    """
                    SELECT summary
                    FROM summaries
                    WHERE scope = ? AND scope_id = ?
                    """,
                    (scope, scope_id),
                ).fetchone()
                if row and str(row["summary"]).strip():
                    lines.append(f"{label}: {str(row['summary']).strip()}")

        facts = self.search_facts(query_text, limit=8)
        if facts:
            lines.append("Relevant facts:")
            for fact in facts:
                lines.append(f"- [{fact.scope}] {fact.key}: {fact.value}")

        if not lines:
            return ""

        prompt_memory = "TALOS durable memory (compact, read-only):\n" + "\n".join(lines)
        return _truncate_block(prompt_memory, max_chars=max_chars)

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_id
            ON messages(session_id, id);

            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                salience INTEGER NOT NULL DEFAULT 5,
                source_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope, key)
            );

            CREATE INDEX IF NOT EXISTS idx_facts_salience
            ON facts(salience DESC, updated_at DESC);

            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope, scope_id)
            );

            CREATE INDEX IF NOT EXISTS idx_summaries_scope
            ON summaries(scope, scope_id);
            """
        )
        self._conn.commit()


_default_store: MemoryStore | None = None
_default_store_lock = threading.Lock()


def get_default_memory_store() -> MemoryStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = MemoryStore()
        return _default_store


def reset_default_memory_store() -> None:
    global _default_store
    with _default_store_lock:
        if _default_store is not None:
            _default_store.close()
        _default_store = None


def _fact_from_row(row: sqlite3.Row) -> MemoryFact:
    return MemoryFact(
        id=int(row["id"]),
        scope=str(row["scope"]),
        key=str(row["key"]),
        value=str(row["value"]),
        salience=int(row["salience"]),
        updated_at=str(row["updated_at"]),
    )


def _required_text(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty.")
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _compact_text(value: str, *, limit: int) -> str:
    compacted = " ".join(value.split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 3].rsplit(" ", 1)[0] + "..."


def _query_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]{3,}", value.lower())
    unique_tokens: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique_tokens.append(token)
        if len(unique_tokens) >= MAX_QUERY_TOKENS:
            break
    return unique_tokens


def _truncate_block(value: str, *, max_chars: int) -> str:
    max_chars = max(0, int(max_chars))
    if max_chars == 0:
        return ""
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return value[:max_chars]
    return value[: max_chars - 3].rsplit("\n", 1)[0] + "..."
