from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler import router
from butler.jobs import JobStore


class RouterJobStatusTests(unittest.TestCase):
    def test_runtime_context_reports_latest_completed_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = JobStore(Path(tmpdir) / "jobs.sqlite3")
            job = store.create_job(
                session_id="main-pc",
                source="terminal",
                request_text="Create a KiCad project.",
            )
            store.update_job(
                job.job_id,
                status="succeeded",
                progress_message="Background work completed.",
                result_summary="Project creation failed because KiCad timed out.",
                result_payload={"response": "Project creation failed because KiCad timed out."},
            )

            with mock.patch.object(router, "get_default_job_store", return_value=store):
                context = router._runtime_context_for_session("main-pc")

            store.close()

        self.assertIn("No background jobs are currently active.", context)
        self.assertIn(job.job_id, context)
        self.assertIn("Project creation failed because KiCad timed out.", context)

    def test_contextual_classifier_uses_model_decision(self) -> None:
        with mock.patch.object(
            router.agent_runtime,
            "classify_request_route",
            return_value={
                "mode": "foreground",
                "reason": "ordinary vocabulary question",
                "response": "",
            },
        ) as classifier:
            decision = router._classify_with_context(
                "define the word background",
                source="terminal",
                session_id="main-pc",
                runtime_context="No active jobs.",
            )

        self.assertEqual(decision.mode, "foreground")
        self.assertEqual(decision.reason, "ordinary vocabulary question")
        classifier.assert_called_once()

    def test_background_response_uses_model_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = JobStore(Path(tmpdir) / "jobs.sqlite3")
            job = store.create_job(
                session_id="main-pc",
                source="terminal",
                request_text="Create a KiCad project.",
            )
            response = router._job_response(
                job,
                source="terminal",
                response_text="I'll take that into the background and report back.",
            )
            store.close()

        self.assertEqual(
            response["response"],
            "I'll take that into the background and report back.",
        )


if __name__ == "__main__":
    unittest.main()
