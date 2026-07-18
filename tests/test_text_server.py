from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.services import awareness_client
from talos.text.server import TextAgentRequestHandler, _stream_state_snapshot


class TextServerLoggingTests(unittest.TestCase):
    def test_event_poll_requests_are_suppressed(self) -> None:
        handler = object.__new__(TextAgentRequestHandler)
        handler.command = "GET"
        handler.path = "/sessions/main-pc/events?after_id=63"
        handler.address_string = lambda: "127.0.0.1"

        with mock.patch("builtins.print") as print_mock:
            handler.log_message('"%s" %s %s', "GET /sessions/main-pc/events?after_id=63 HTTP/1.1", "200", "-")

        print_mock.assert_not_called()

    def test_non_event_requests_still_log(self) -> None:
        handler = object.__new__(TextAgentRequestHandler)
        handler.command = "GET"
        handler.path = "/health"
        handler.address_string = lambda: "127.0.0.1"

        with mock.patch("builtins.print") as print_mock:
            handler.log_message('"%s" %s %s', "GET /health HTTP/1.1", "200", "-")

        print_mock.assert_called_once()


class TextServerStreamContextTests(unittest.TestCase):
    def test_stream_uses_awareness_snapshot_with_request_fallback(self) -> None:
        with mock.patch.object(
            awareness_client,
            "snapshot_with_fallback",
            return_value="awareness situation",
        ) as snapshot_mock:
            snapshot = _stream_state_snapshot({"state_snapshot": "legacy state"})

        self.assertEqual(snapshot, "awareness situation")
        snapshot_mock.assert_called_once_with("legacy state")


if __name__ == "__main__":
    unittest.main()
