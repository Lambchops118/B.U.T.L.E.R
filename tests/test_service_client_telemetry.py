from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.text.service_client import stream_message


class _FakeSseResponse:
    def __init__(self, events):
        self._lines = [
            f"data: {json.dumps(event)}\n\n".encode("utf-8") for event in events
        ]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)


class ServiceClientTelemetryTests(unittest.TestCase):
    def test_forwards_telemetry_and_request_id_without_yielding_it_as_text(self):
        response = _FakeSseResponse(
            [
                {
                    "type": "telemetry",
                    "data": {"event": "prompt_ready", "prompt_tokens_estimated": 321},
                },
                {"type": "delta", "text": "Hello"},
                {"type": "done", "text": "Hello"},
            ]
        )
        received = []
        with mock.patch(
            "talos.text.service_client.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            deltas = list(
                stream_message(
                    "hi",
                    session_id="voice",
                    request_id="req-42",
                    telemetry_callback=received.append,
                )
            )

        self.assertEqual(deltas, ["Hello"])
        self.assertEqual(received[0]["prompt_tokens_estimated"], 321)
        request = urlopen.call_args.args[0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["request_id"], "req-42")


if __name__ == "__main__":
    unittest.main()
