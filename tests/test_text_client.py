from __future__ import annotations

import io
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.text import client


class TextClientEventPollingTests(unittest.TestCase):
    def test_poller_swallows_historical_events_after_cursor_bootstrap_failure(self) -> None:
        stop_event = threading.Event()
        waits = iter([False, False, True])
        responses = [
            RuntimeError("temporary failure"),
            {
                "latest_event_id": 99,
                "events": [{"id": 7, "event_type": "completed", "job_id": "job_old", "message": "Old result."}],
            },
            {
                "latest_event_id": 100,
                "events": [{"id": 100, "event_type": "completed", "job_id": "job_new", "message": "Fresh result."}],
            },
        ]

        def fake_list_session_events(*args, **kwargs):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        def fake_wait(_timeout: float) -> bool:
            return next(waits)

        with (
            mock.patch.object(client, "list_session_events", side_effect=fake_list_session_events),
            mock.patch.object(stop_event, "wait", side_effect=fake_wait),
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            client._poll_session_events("http://example.test", "", "main-pc", 1.0, stop_event)

        output = stdout.getvalue()
        self.assertNotIn("job_old", output)
        self.assertIn("job_new", output)


if __name__ == "__main__":
    unittest.main()
