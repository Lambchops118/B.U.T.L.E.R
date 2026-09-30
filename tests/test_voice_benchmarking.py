from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler.voice import benchmarking
from butler.voice.benchmarking import VoiceBenchmarkSession


class VoiceBenchmarkTelemetryTests(unittest.TestCase):
    def test_merges_main_process_backend_token_and_stage_telemetry(self):
        benchmark = VoiceBenchmarkSession(wake_word="butler", wake_word_mode="local")
        benchmark.apply_pipeline_telemetry(
            {
                "event": "prompt_ready",
                "backend": "ollama",
                "backend_location": "local",
                "model": "mb-core-v1:latest",
                "prompt_tokens_estimated": 7900,
                "tool_build_ms": 12.5,
            }
        )
        benchmark.apply_pipeline_telemetry(
            {
                "event": "llm_round_completed",
                "provider_prompt_tokens": 7821,
                "model_load_ms": 0.0,
                "model_load_measurement": "already_loaded",
                "ollama_context_length": 16384,
            }
        )

        snapshot = benchmark.pipeline_snapshot()
        self.assertEqual(snapshot["dimensions"]["llm_backend"], "ollama")
        self.assertEqual(snapshot["dimensions"]["llm_backend_location"], "local")
        self.assertEqual(snapshot["latencies_ms"]["provider_prompt_tokens"], 7821)
        self.assertEqual(snapshot["latencies_ms"]["ollama_context_length"], 16384)
        self.assertEqual(snapshot["latencies_ms"]["tool_build_ms"], 12.5)

    def test_rotates_full_csv_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current = root / "voice_benchmarks.csv"
            current.write_text("header\nrow\n", encoding="utf-8")
            with (
                mock.patch.object(benchmarking, "LOG_DIR", root),
                mock.patch.object(benchmarking, "CSV_LOG_PATH", current),
                mock.patch.object(benchmarking, "_CSV_MAX_BYTES", 1),
            ):
                benchmarking._rotate_csv_if_needed()

            self.assertFalse(current.exists())
            archives = list(root.glob("voice_benchmarks_*.csv"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_text(encoding="utf-8"), "header\nrow\n")


if __name__ == "__main__":
    unittest.main()
