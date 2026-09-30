from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from butler import telemetry


class PipelineTelemetryTests(unittest.TestCase):
    def test_writes_correlated_bounded_json_without_user_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            log_path = log_dir / "pipeline.jsonl"
            with (
                mock.patch.object(telemetry, "_ENABLED", True),
                mock.patch.object(telemetry, "_LOG_DIR", log_dir),
                mock.patch.object(telemetry, "_LOG_PATH", log_path),
            ):
                telemetry.emit_pipeline_event(
                    request_id="req-1",
                    component="test",
                    event="stage_completed",
                    duration_ms=12.5,
                    dimensions={"backend": "ollama"},
                )

            payload = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["request_id"], "req-1")
            self.assertEqual(payload["trace_id"], "req-1")
            self.assertEqual(payload["schema_version"], 2)
            self.assertTrue(payload["boot_id"])
            self.assertTrue(payload["config_fingerprint"])
            self.assertIn("runtime_version", payload)
            self.assertEqual(payload["component"], "test")
            self.assertEqual(payload["duration_ms"], 12.5)
            self.assertEqual(payload["dimensions"], {"backend": "ollama"})
            self.assertNotIn("prompt", payload)
            self.assertNotIn("transcript", payload)
            self.assertNotIn("response", payload)

    def test_rotates_full_log_without_deleting_previous_part(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            original = log_dir / "pipeline.jsonl"
            original.write_text("already full\n", encoding="utf-8")
            with (
                mock.patch.object(telemetry, "_ENABLED", True),
                mock.patch.object(telemetry, "_LOG_DIR", log_dir),
                mock.patch.object(telemetry, "_LOG_PATH", original),
                mock.patch.object(telemetry, "_MAX_LOG_BYTES", 1),
                mock.patch.object(telemetry, "_WRITE_COUNT", 255),
                mock.patch.object(telemetry, "_PART", 0),
            ):
                telemetry.emit_pipeline_event(
                    request_id="req-rotate",
                    component="test",
                    event="stage_completed",
                )
                rotated = telemetry.telemetry_log_path()

            self.assertTrue(original.exists())
            self.assertNotEqual(rotated, original)
            self.assertTrue(rotated.exists())


if __name__ == "__main__":
    unittest.main()
