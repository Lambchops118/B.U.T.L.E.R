from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.voice.benchmarking import VoiceBenchmarkSession


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


if __name__ == "__main__":
    unittest.main()
