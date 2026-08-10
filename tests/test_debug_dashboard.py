from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.debug_dashboard.server import (
    DebugSnapshotService,
    DebugHTTPServer,
    _normalize_remote_host,
    build_audio_snapshot,
    read_conversation_messages,
    read_pipeline_events,
    read_voice_benchmarks,
)


class _FakeSampler:
    def snapshot(self):
        return {
            "sampled_at": "2026-08-10T00:00:00+00:00",
            "cpu": {"logical_count": 8, "utilization_percent": 12.5, "load_average": None},
            "memory": {"total_bytes": 1000, "available_bytes": 400, "used_percent": 60.0},
            "disk": {"total_bytes": 2000, "free_bytes": 1000, "used_percent": 50.0},
            "gpu": {"status": "available", "gpus": []},
        }


class DebugDashboardDataTests(unittest.TestCase):
    def test_reads_bounded_newest_pipeline_events_and_skips_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "pipeline_telemetry_run_1.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"timestamp": "2026-08-10T00:00:01Z", "event": "one"}),
                        "not-json",
                        json.dumps({"timestamp": "2026-08-10T00:00:03Z", "event": "three"}),
                    ]
                ),
                encoding="utf-8",
            )

            result = read_pipeline_events(root, limit=10)

        self.assertEqual(result["status"], "available")
        self.assertEqual([event["event"] for event in result["events"]], ["three", "one"])
        self.assertEqual(result["malformed_lines_skipped"], 1)

    def test_reads_voice_benchmark_content_and_numeric_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "voice_benchmarks_run.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["run_started_at", "session_id", "transcript", "command", "response_preview", "input_rms"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "run_started_at": "2026-08-10T00:00:00+00:00",
                        "session_id": "req-1",
                        "transcript": "Butler turn on the light",
                        "command": "turn on the light",
                        "response_preview": "Done.",
                        "input_rms": "418.5",
                    }
                )

            result = read_voice_benchmarks(root, limit=10)

        self.assertEqual(result["rows"][0]["transcript"], "Butler turn on the light")
        self.assertEqual(result["rows"][0]["input_rms"], 418.5)

    def test_reads_conversation_database_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    created_at TEXT,
                    metadata_json TEXT
                );
                INSERT INTO messages VALUES
                    (1, 'voice', 'user', 'hello', '2026-08-10T00:00:00Z', '{"interaction_mode":"voice"}'),
                    (2, 'voice', 'assistant', 'hi', '2026-08-10T00:00:01Z', '{}');
                """
            )
            connection.commit()
            connection.close()

            result = read_conversation_messages(db_path, limit=10)

        self.assertEqual([row["content"] for row in result["messages"]], ["hi", "hello"])
        self.assertEqual(result["messages"][1]["metadata"]["interaction_mode"], "voice")

    def test_audio_snapshot_merges_voice_rms_and_barge_in_measurements(self) -> None:
        telemetry = {
            "events": [
                {
                    "timestamp": "2026-08-10T00:00:02Z",
                    "event": "barge_in_metrics_snapshot",
                    "counters": {"accepted": 1},
                    "measurements": {"capture_rms_last": 900, "capture_rms_average": 450},
                }
            ]
        }
        benchmarks = {
            "rows": [
                {
                    "run_started_at": "2026-08-10T00:00:01Z",
                    "input_rms": 300,
                }
            ]
        }

        result = build_audio_snapshot(telemetry, benchmarks)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["series"]["input_rms"][0]["value"], 300)
        self.assertEqual(result["series"]["capture_rms"][0]["value"], 900)

    def test_snapshot_schema_keeps_unavailable_prompt_capture_truthful(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = DebugSnapshotService(
                log_root=root,
                memory_db=root / "missing.sqlite3",
                sampler=_FakeSampler(),
            )
            with mock.patch.object(service, "_service_health", return_value=[]):
                snapshot = service.snapshot(event_limit=5, interaction_limit=5)

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["interaction_io"]["prompt_capture"]["status"], "not_available")
        self.assertEqual(snapshot["extensions"], [])

    def test_console_does_not_sample_its_own_host_without_explicit_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = DebugSnapshotService(
                log_root=root,
                memory_db=root / "missing.sqlite3",
                sampler=None,
                system_metrics_url="",
            )
            with mock.patch.object(service, "_service_health", return_value=[]):
                snapshot = service.snapshot(event_limit=5, interaction_limit=5)

        host = snapshot["system_health"]["host"]
        self.assertEqual(host["status"], "not_configured")
        self.assertIsNone(host["cpu"]["utilization_percent"])
        self.assertEqual(host["gpu"]["gpus"], [])

    def test_normalizes_remote_snapshot_host_metrics(self) -> None:
        host = _normalize_remote_host(
            {
                "snapshot": {
                    "system_health": {
                        "host": {
                            "sampled_at": "2026-08-10T00:00:00Z",
                            "cpu": {"utilization_percent": 25.0},
                            "gpu": {"status": "available", "gpus": [{"index": 0}]},
                        }
                    }
                }
            },
            source="http://talos-host/metrics",
        )

        self.assertEqual(host["status"], "available")
        self.assertEqual(host["cpu"]["utilization_percent"], 25.0)
        self.assertEqual(host["gpu"]["gpus"][0]["index"], 0)

    def test_http_server_serves_page_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = DebugSnapshotService(
                log_root=root,
                memory_db=root / "missing.sqlite3",
                sampler=_FakeSampler(),
            )
            with mock.patch.object(service, "_service_health", return_value=[]):
                server = DebugHTTPServer(("127.0.0.1", 0), service)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{server.server_port}"
                    with urllib.request.urlopen(base + "/", timeout=2) as response:
                        page = response.read().decode("utf-8")
                    with urllib.request.urlopen(base + "/api/snapshot", timeout=2) as response:
                        payload = json.load(response)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

        self.assertIn("TALOS DEBUG CONSOLE", page)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["snapshot"]["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
