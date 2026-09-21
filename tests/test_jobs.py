from __future__ import annotations

import tempfile
import time
import unittest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.jobs import JobManager, JobStore


class JobStoreTests(unittest.TestCase):
    def test_jobs_and_events_persist_in_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobs.sqlite3"
            store = JobStore(db_path)
            job = store.create_job(
                session_id="session-a",
                source="terminal",
                request_text="Create a KiCad board.",
            )
            store.add_event(
                job_id=job.job_id,
                session_id=job.session_id,
                event_type="queued",
                message="Queued.",
            )
            store.close()

            reopened = JobStore(db_path)
            loaded = reopened.get_job(job.job_id)
            events = reopened.list_session_events("session-a")
            reopened.close()

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.status, "queued")
        self.assertEqual(events[0].event_type, "queued")

    def test_latest_session_event_id_returns_highest_event_id(self) -> None:
        store = JobStore(":memory:")
        try:
            first = store.create_job(
                session_id="session-c",
                source="terminal",
                request_text="First request",
            )
            store.add_event(
                job_id=first.job_id,
                session_id=first.session_id,
                event_type="queued",
                message="Queued first job.",
            )
            second = store.create_job(
                session_id="session-c",
                source="terminal",
                request_text="Second request",
            )
            latest = store.add_event(
                job_id=second.job_id,
                session_id=second.session_id,
                event_type="completed",
                message="Completed second job.",
            )
            self.assertEqual(store.latest_session_event_id("session-c"), latest.id)
        finally:
            store.close()

    def test_manager_runs_job_and_records_completion(self) -> None:
        store = JobStore(":memory:")
        manager = JobManager(lambda job: f"done: {job.request_text}", store=store)
        try:
            job = manager.submit(
                session_id="session-b",
                source="terminal",
                request_text="Work on this.",
                state_snapshot="no recent status",
                interaction_mode="text",
                classification_reason="test",
            )

            completed = _wait_for_terminal_job(store, job.job_id)
            events = store.list_session_events("session-b")
        finally:
            manager.shutdown(wait=True)
            store.close()

        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(completed.result_payload["response"], "done: Work on this.")
        self.assertIn("completed", {event.event_type for event in events})


def _wait_for_terminal_job(store: JobStore, job_id: str):
    deadline = time.time() + 2
    while time.time() < deadline:
        job = store.get_job(job_id)
        if job and job.status in {"succeeded", "failed", "cancelled", "interrupted"}:
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


if __name__ == "__main__":
    unittest.main()
