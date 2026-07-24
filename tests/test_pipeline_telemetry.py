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

from talos import telemetry


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
            self.assertEqual(payload["component"], "test")
            self.assertEqual(payload["duration_ms"], 12.5)
            self.assertEqual(payload["dimensions"], {"backend": "ollama"})
            self.assertNotIn("prompt", payload)
            self.assertNotIn("transcript", payload)
            self.assertNotIn("response", payload)


if __name__ == "__main__":
    unittest.main()
