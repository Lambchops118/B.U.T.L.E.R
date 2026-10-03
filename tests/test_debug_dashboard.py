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

from butler.debug_dashboard.server import (
    DebugSnapshotService,
    DebugHTTPServer,
    _normalize_remote_host,
    build_audio_snapshot,
    build_request_traces,
    build_tool_reliability,
    read_conversation_messages,
    read_jobs,
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

    def test_reads_current_single_rolling_voice_benchmark_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "voice_benchmarks.csv"
            path.write_text("run_started_at,session_id,input_rms\n2026-08-10T00:00:00Z,req-2,12\n", encoding="utf-8")
            result = read_voice_benchmarks(root, limit=10)
        self.assertEqual(result["rows"][0]["session_id"], "req-2")

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

        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["interaction_io"]["prompt_capture"]["status"], "structure_only")
        self.assertEqual(snapshot["extensions"], [])
        model_memory = snapshot["model_memory"]
        self.assertEqual(model_memory["prompt_composition"]["status"], "not_available")
        self.assertEqual(model_memory["memory_store"]["status"], "not_available")
        self.assertEqual(model_memory["llm_rounds"]["latency_by_model"], [])
        self.assertIn("request_traces", snapshot["operations"])
        self.assertEqual(len(snapshot["system_health"]["history"]), 1)

    def test_content_is_redacted_by_default_and_cache_avoids_duplicate_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with mock.patch.dict("os.environ", {"BUTLER_DEBUG_INCLUDE_CONTENT": "0"}):
                service = DebugSnapshotService(
                    log_root=root,
                    memory_db=root / "missing.sqlite3",
                    sampler=_FakeSampler(),
                )
            visible = service._visible_messages({"messages": [{"content": "private"}]})
            loader = mock.Mock(return_value={"value": 1})
            first = service._cached("test", 10, loader)
            second = service._cached("test", 10, loader)

        self.assertEqual(visible["messages"][0]["content"], "[redacted; 7 chars]")
        self.assertIs(first, second)
        loader.assert_called_once()

    def test_host_history_is_downsampled_for_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            service = DebugSnapshotService(
                log_root=root,
                memory_db=root / "missing.sqlite3",
                sampler=_FakeSampler(),
            )
            service._host_history.extend({"timestamp": str(index)} for index in range(1000))
            result = service._downsampled_host_history(max_points=100)

        self.assertEqual(len(result), 100)
        self.assertEqual(result[0]["timestamp"], "0")
        self.assertEqual(result[-1]["timestamp"], "999")

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
            source="http://butler-host/metrics",
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

        self.assertIn("Butler DEBUG CONSOLE", page)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["snapshot"]["schema_version"], 2)

    def test_builds_correlated_traces_and_tool_reliability(self) -> None:
        events = [
            {"timestamp": "2026-08-10T00:00:00Z", "request_id": "req-1", "component": "agent", "event": "prompt_ready", "prompt_assembly_ms": 10},
            {"timestamp": "2026-08-10T00:00:01Z", "request_id": "req-1", "component": "agent", "event": "tool_call_completed", "tool_name": "water", "transport": "mcp", "tool_ms": 40, "failed": False},
            {"timestamp": "2026-08-10T00:00:02Z", "request_id": "req-2", "component": "agent", "event": "tool_call_completed", "tool_name": "water", "transport": "mcp", "tool_ms": 80, "failed": True},
        ]

        traces = build_request_traces(events)
        tools = build_tool_reliability(events)

        self.assertEqual(len(traces), 2)
        self.assertEqual(next(row for row in traces if row["trace_id"] == "req-2")["outcome"], "failed")
        self.assertEqual(tools[0]["tool_name"], "water")
        self.assertEqual(tools[0]["failures"], 1)
        self.assertEqual(tools[0]["success_rate_percent"], 50.0)

    def test_reads_jobs_without_exposing_request_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "jobs.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE jobs (
                    job_id TEXT, session_id TEXT, source TEXT, request_text TEXT,
                    status TEXT, created_at TEXT, updated_at TEXT, started_at TEXT,
                    finished_at TEXT, requires_user_input INTEGER, error_message TEXT,
                    resource_locks_json TEXT
                );
                INSERT INTO jobs VALUES ('job-1', 'voice', 'voice', 'private request',
                    'failed', '2026-08-10T00:00:00Z', '2026-08-10T00:00:01Z', NULL,
                    '2026-08-10T00:00:01Z', 0, 'private failure detail', '["speaker"]');
                """
            )
            connection.commit()
            connection.close()

            result = read_jobs(db_path, limit=10)

        self.assertEqual(result["jobs"][0]["status"], "failed")
        self.assertNotIn("request_text", result["jobs"][0])
        self.assertNotIn("error_message", result["jobs"][0])


def _event(name: str, request_id: str, stamp: str, **fields) -> str:
    return json.dumps({"timestamp": stamp, "request_id": request_id, "event": name, **fields})


class ModelMemoryInsightTests(unittest.TestCase):
    def test_round_stats_split_first_and_follow_up_rounds_and_count_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "pipeline_telemetry_a.jsonl").write_text(
                "\n".join([
                    _event("prompt_ready", "r1", "2026-10-03T00:00:00Z", tool_count=27,
                           memory_block_chars=0, history_message_count=4),
                    _event("llm_round_completed", "r1", "2026-10-03T00:00:01Z", round=1,
                           model="m", llm_ttft_ms=1600.0, provider_prompt_tokens=6500,
                           provider_completion_tokens=20),
                    _event("tool_call_completed", "r1", "2026-10-03T00:00:02Z",
                           tool_name="set_smart_plug", tool_ms=300.0, failed=False),
                    _event("llm_round_completed", "r1", "2026-10-03T00:00:03Z", round=2,
                           model="m", llm_ttft_ms=250.0, provider_prompt_tokens=6560,
                           provider_completion_tokens=3),
                    _event("prompt_ready", "r2", "2026-10-03T00:01:00Z", tool_count=27,
                           memory_block_chars=120),
                    "not-json",
                ]),
                encoding="utf-8",
            )
            from butler.debug_dashboard.insights import read_llm_round_stats

            stats = read_llm_round_stats(root)

        rows = {row["round_kind"]: row for row in stats["latency_by_model"]}
        self.assertEqual(rows["first round"]["ttft_p50_ms"], 1600.0)
        self.assertEqual(rows["follow-up round"]["ttft_p50_ms"], 250.0)
        self.assertEqual(stats["tool_calls"]["set_smart_plug"]["calls"], 1)
        turn = stats["recent_turns"][0]
        self.assertEqual((turn["first_ttft_ms"], turn["followup_ttft_ms"]), (1600.0, 250.0))
        self.assertEqual(turn["completion_tokens"], 23)
        self.assertEqual(stats["memory_injection"]["prompts_reporting"], 2)
        self.assertEqual(stats["memory_injection"]["prompts_with_memory"], 1)

    def test_prompt_composition_reports_sizes_without_text(self) -> None:
        secret = "PRIVATE-USER-TEXT"
        tools = [
            {"type": "function", "function": {"name": "big_tool", "description": "x" * 400}},
            {"type": "function", "function": {"name": "small_tool", "description": "y"}},
        ]
        request = {
            "model": "m",
            "tools": tools,
            "messages": [
                {"role": "system", "content": "## Base Soul Document\n" + "s" * 300},
                {"role": "system", "content": "## Memory Context (Runtime Injected)\nfact"},
                {"role": "user", "content": "earlier " + secret},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": secret},
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "llm_io_a.jsonl").write_text(
                "\n".join([
                    json.dumps({"direction": "sent", "operation": "warmup", "payload": request}),
                    json.dumps({"direction": "sent", "operation": "stream", "timestamp": "t", "payload": request}),
                    json.dumps({"direction": "received", "operation": "stream",
                                "payload": {"usage": {"prompt_tokens": 321}}}),
                ]),
                encoding="utf-8",
            )
            from butler.debug_dashboard.insights import read_prompt_composition

            result = read_prompt_composition(root)

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["requests_scanned"], 1)
        self.assertEqual(result["requests_with_memory_block"], 1)
        self.assertEqual(result["provider_prompt_tokens"], 321)
        self.assertEqual([tool["name"] for tool in result["tools"]], ["big_tool", "small_tool"])
        sections = {section["section"] for section in result["sections"]}
        self.assertTrue({"system prompt", "memory block", "conversation history", "user turn", "tool schemas"} <= sections)
        self.assertNotIn(secret, json.dumps(result))

    def test_ollama_placement_computes_gpu_share(self) -> None:
        payload = {"models": [{"name": "m:latest", "size": 1000, "size_vram": 750, "context_length": 16384}]}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with mock.patch("butler.debug_dashboard.insights.urllib.request.urlopen", return_value=response) as opened:
            from butler.debug_dashboard.insights import read_ollama_placement

            result = read_ollama_placement("http://127.0.0.1:11434/v1")

        self.assertEqual(opened.call_args[0][0].full_url, "http://127.0.0.1:11434/api/ps")
        self.assertEqual(result["models"][0]["gpu_percent"], 75.0)

    def test_memory_store_stats_count_rows_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.sqlite3"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE sessions (session_id TEXT);
                CREATE TABLE messages (id INTEGER, created_at TEXT);
                CREATE TABLE facts (scope TEXT, key TEXT, value TEXT, updated_at TEXT);
                CREATE TABLE summaries (scope TEXT);
                INSERT INTO facts VALUES ('user', 'units', 'PRIVATE-FACT', '2026-10-03');
                """
            )
            connection.commit()
            connection.close()
            from butler.debug_dashboard.insights import read_memory_store_stats

            result = read_memory_store_stats(db_path)

        self.assertEqual(result["counts"]["facts"], 1)
        self.assertEqual(result["facts_by_scope"], {"user": 1})
        self.assertNotIn("PRIVATE-FACT", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
