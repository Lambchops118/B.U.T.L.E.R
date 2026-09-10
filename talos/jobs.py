from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from talos.services import awareness_signals


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_DB_PATH = REPO_ROOT / "db" / "talos_jobs.sqlite3"

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    session_id: str
    source: str
    request_text: str
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    progress_message: str | None = None
    result_summary: str | None = None
    result_payload: dict[str, Any] | None = None
    error_message: str | None = None
    requires_user_input: bool = False
    resource_locks: list[str] | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "source": self.source,
            "request_text": self.request_text,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "progress_message": self.progress_message,
            "result_summary": self.result_summary,
            "result_payload": self.result_payload or {},
            "error_message": self.error_message,
            "requires_user_input": self.requires_user_input,
            "resource_locks": self.resource_locks or [],
            "metadata": self.metadata or {},
        }


@dataclass(frozen=True)
class JobEvent:
    id: int
    job_id: str
    session_id: str
    event_type: str
    message: str
    created_at: str
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "message": self.message,
            "created_at": self.created_at,
            "metadata": self.metadata or {},
        }


class JobStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        selected_path = db_path or os.getenv("TALOS_JOBS_DB_PATH") or DEFAULT_JOB_DB_PATH
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

    def create_job(
        self,
        *,
        session_id: str,
        source: str,
        request_text: str,
        status: str = "queued",
        progress_message: str = "Queued.",
        resource_locks: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        now = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    job_id, session_id, source, request_text, status,
                    created_at, updated_at, progress_message, requires_user_input,
                    resource_locks_json, metadata_json, result_payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, '{}')
                """,
                (
                    job_id,
                    _required_text(session_id, "session_id"),
                    _required_text(source, "source"),
                    _required_text(request_text, "request_text"),
                    _required_text(status, "status"),
                    now,
                    now,
                    progress_message,
                    _json_dumps(resource_locks or []),
                    _json_dumps(metadata or {}),
                ),
            )
            self._conn.commit()
        return self.get_job(job_id) or JobRecord(
            job_id=job_id,
            session_id=session_id,
            source=source,
            request_text=request_text,
            status=status,
            created_at=now,
            updated_at=now,
            progress_message=progress_message,
            resource_locks=resource_locks or [],
            metadata=metadata or {},
        )

    def update_job(self, job_id: str, **fields: Any) -> JobRecord:
        if not fields:
            job = self.get_job(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

        allowed = {
            "status",
            "started_at",
            "finished_at",
            "progress_message",
            "result_summary",
            "result_payload",
            "error_message",
            "requires_user_input",
            "resource_locks",
            "metadata",
        }
        assignments = ["updated_at = ?"]
        params: list[Any] = [_utc_now()]
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unsupported job field: {key}")
            column = key
            if key == "result_payload":
                column = "result_payload_json"
                value = _json_dumps(value or {})
            elif key == "resource_locks":
                column = "resource_locks_json"
                value = _json_dumps(value or [])
            elif key == "metadata":
                column = "metadata_json"
                value = _json_dumps(value or {})
            elif key == "requires_user_input":
                value = 1 if value else 0
            assignments.append(f"{column} = ?")
            params.append(value)

        params.append(_required_text(job_id, "job_id"))
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id = ?",
                params,
            )
            self._conn.commit()
            if cursor.rowcount == 0:
                raise KeyError(job_id)

        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def add_event(
        self,
        *,
        job_id: str,
        session_id: str,
        event_type: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> JobEvent:
        now = _utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO job_events (
                    job_id, session_id, event_type, message, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _required_text(job_id, "job_id"),
                    _required_text(session_id, "session_id"),
                    _required_text(event_type, "event_type"),
                    _required_text(message, "message"),
                    now,
                    _json_dumps(metadata or {}),
                ),
            )
            self._conn.commit()
            event_id = int(cursor.lastrowid)
        return JobEvent(
            id=event_id,
            job_id=job_id,
            session_id=session_id,
            event_type=event_type,
            message=message,
            created_at=now,
            metadata=metadata or {},
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (_required_text(job_id, "job_id"),),
            ).fetchone()
        return _job_from_row(row) if row else None

    def list_session_jobs(self, session_id: str, *, limit: int = 25) -> list[JobRecord]:
        limit = max(1, int(limit))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE session_id = ?
                ORDER BY created_at DESC, job_id DESC
                LIMIT ?
                """,
                (_required_text(session_id, "session_id"), limit),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def list_session_events(
        self,
        session_id: str,
        *,
        after_id: int = 0,
        limit: int = 50,
    ) -> list[JobEvent]:
        limit = max(1, int(limit))
        after_id = max(0, int(after_id))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM job_events
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (_required_text(session_id, "session_id"), after_id, limit),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def latest_session_event_id(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COALESCE(MAX(id), 0) AS latest_id
                FROM job_events
                WHERE session_id = ?
                """,
                (_required_text(session_id, "session_id"),),
            ).fetchone()
        if row is None:
            return 0
        return int(row["latest_id"] or 0)

    def mark_incomplete_jobs_interrupted(self) -> int:
        now = _utc_now()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT job_id, session_id
                FROM jobs
                WHERE status NOT IN ('succeeded', 'failed', 'cancelled', 'interrupted')
                """
            ).fetchall()
            self._conn.execute(
                """
                UPDATE jobs
                SET status = 'interrupted',
                    updated_at = ?,
                    finished_at = COALESCE(finished_at, ?),
                    progress_message = 'Interrupted by TALOS restart.',
                    error_message = COALESCE(error_message, 'Interrupted by TALOS restart.')
                WHERE status NOT IN ('succeeded', 'failed', 'cancelled', 'interrupted')
                """,
                (now, now),
            )
            for row in rows:
                self._conn.execute(
                    """
                    INSERT INTO job_events (
                        job_id, session_id, event_type, message, created_at, metadata_json
                    )
                    VALUES (?, ?, 'interrupted', 'Interrupted by TALOS restart.', ?, '{}')
                    """,
                    (row["job_id"], row["session_id"], now),
                )
            self._conn.commit()
            return len(rows)

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source TEXT NOT NULL,
                request_text TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                progress_message TEXT,
                result_summary TEXT,
                result_payload_json TEXT NOT NULL DEFAULT '{}',
                error_message TEXT,
                requires_user_input INTEGER NOT NULL DEFAULT 0,
                resource_locks_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_session_status
            ON jobs(session_id, status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_job_events_session
            ON job_events(session_id, id);
            """
        )
        self._conn.commit()


JobExecutor = Callable[[JobRecord], str]


class JobManager:
    def __init__(
        self,
        executor: JobExecutor,
        *,
        store: JobStore | None = None,
        worker_count: int = 1,
    ) -> None:
        self.store = store or get_default_job_store()
        self.executor = executor
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._closed = False
        interrupted = self.store.mark_incomplete_jobs_interrupted()
        if interrupted:
            print(f"TALOS jobs marked {interrupted} incomplete job(s) interrupted on startup.")

        for index in range(max(1, int(worker_count))):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"talos-job-worker-{index + 1}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def submit(
        self,
        *,
        session_id: str,
        source: str,
        request_text: str,
        state_snapshot: str,
        interaction_mode: str,
        classification_reason: str,
    ) -> JobRecord:
        if self._closed:
            raise RuntimeError("Job manager is shut down.")
        job = self.store.create_job(
            session_id=session_id,
            source=source,
            request_text=request_text,
            metadata={
                "state_snapshot": state_snapshot,
                "interaction_mode": interaction_mode,
                "classification_reason": classification_reason,
            },
        )
        self.store.add_event(
            job_id=job.job_id,
            session_id=session_id,
            event_type="queued",
            message="Queued background work.",
            metadata={"classification_reason": classification_reason},
        )
        self._queue.put(job.job_id)
        return job

    def shutdown(self, *, wait: bool = False) -> None:
        self._closed = True
        for _ in self._threads:
            self._queue.put(None)
        if wait:
            for thread in self._threads:
                thread.join(timeout=2)

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                self._queue.task_done()
                break
            try:
                self._run_job(job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        now = _utc_now()
        job = self.store.update_job(
            job_id,
            status="running",
            started_at=now,
            progress_message="Running background work.",
        )
        self.store.add_event(
            job_id=job.job_id,
            session_id=job.session_id,
            event_type="started",
            message="Started background work.",
        )
        try:
            response_text = self.executor(job)
        except Exception as exc:
            error_message = str(exc)
            self.store.update_job(
                job.job_id,
                status="failed",
                finished_at=_utc_now(),
                progress_message="Background work failed.",
                error_message=error_message,
            )
            self.store.add_event(
                job_id=job.job_id,
                session_id=job.session_id,
                event_type="failed",
                message=error_message,
            )
            # A job the user asked for and that then failed is exactly the kind
            # of thing the system should be able to raise on its own later.
            awareness_signals.record_agent_event(
                "agent.job.failed",
                {
                    "job_id": job.job_id,
                    "session_id": job.session_id,
                    "source": job.source,
                    "error": _compact_text(error_message, limit=200),
                },
                severity="warning",
            )
            return

        summary = _compact_text(response_text, limit=800)
        self.store.update_job(
            job.job_id,
            status="succeeded",
            finished_at=_utc_now(),
            progress_message="Background work completed.",
            result_summary=summary,
            result_payload={"response": response_text},
        )
        self.store.add_event(
            job_id=job.job_id,
            session_id=job.session_id,
            event_type="completed",
            message=summary or "Background work completed.",
        )
        awareness_signals.record_agent_event(
            "agent.job.completed",
            {
                "job_id": job.job_id,
                "session_id": job.session_id,
                "source": job.source,
                "summary_chars": len(summary or ""),
            },
        )


_default_store: JobStore | None = None
_default_store_lock = threading.Lock()


def get_default_job_store() -> JobStore:
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = JobStore()
        return _default_store


def reset_default_job_store() -> None:
    global _default_store
    with _default_store_lock:
        if _default_store is not None:
            _default_store.close()
        _default_store = None


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=str(row["job_id"]),
        session_id=str(row["session_id"]),
        source=str(row["source"]),
        request_text=str(row["request_text"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        progress_message=row["progress_message"],
        result_summary=row["result_summary"],
        result_payload=_json_loads(row["result_payload_json"], default={}),
        error_message=row["error_message"],
        requires_user_input=bool(row["requires_user_input"]),
        resource_locks=_json_loads(row["resource_locks_json"], default=[]),
        metadata=_json_loads(row["metadata_json"], default={}),
    )


def _event_from_row(row: sqlite3.Row) -> JobEvent:
    return JobEvent(
        id=int(row["id"]),
        job_id=str(row["job_id"]),
        session_id=str(row["session_id"]),
        event_type=str(row["event_type"]),
        message=str(row["message"]),
        created_at=str(row["created_at"]),
        metadata=_json_loads(row["metadata_json"], default={}),
    )


def _required_text(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty.")
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def _json_loads(value: str | None, *, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _compact_text(value: str, *, limit: int) -> str:
    compacted = " ".join(str(value or "").split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 3].rsplit(" ", 1)[0] + "..."
