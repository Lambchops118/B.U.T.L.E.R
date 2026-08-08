from __future__ import annotations

import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.voice.streaming.barge_in_observability import (
    BargeInMetrics,
    FixtureRecorderConfig,
    SynchronizedFixtureRecorder,
)


class BargeInMetricsTests(unittest.TestCase):
    def test_snapshot_has_bounded_counts_and_truthful_capabilities(self):
        metrics = BargeInMetrics()
        metrics.candidate_started(
            capture_rms=2100,
            render_rms=9000,
            threshold_rms=1800,
            pause_latency_ms=192,
        )
        metrics.capture_completed(
            capture_duration_ms=960,
            heuristic_speech_ms=320,
        )
        metrics.observe_levels(capture_rms=1600, render_rms=8000)
        metrics.candidate_rejected(
            "echo",
            asr_latency_ms=250,
            asr_confidence=0.7,
        )
        metrics.accepted(asr_latency_ms=180)

        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["counters"]["candidate_started"], 1)
        self.assertEqual(snapshot["counters"]["candidate_rejected"], 1)
        self.assertEqual(snapshot["counters"]["candidate_rejected_echo"], 1)
        self.assertEqual(snapshot["counters"]["accepted"], 1)
        self.assertEqual(
            snapshot["measurements"]["asr_latency_ms"]["average"], 215.0
        )
        self.assertFalse(
            snapshot["capabilities"]["vad_probability_available"]
        )
        self.assertFalse(
            snapshot["capabilities"]["aec_residual_rms_available"]
        )
        self.assertTrue(snapshot["capabilities"]["asr_confidence_observed"])
        self.assertNotIn("transcript", json.dumps(snapshot).lower())
        self.assertNotIn("pcm", json.dumps(snapshot).lower())

    def test_unknown_rejection_reasons_are_bounded(self):
        metrics = BargeInMetrics()

        metrics.candidate_rejected("arbitrary-user-controlled-value")

        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["candidate_rejected"], 1)
        self.assertEqual(counters["candidate_rejected_other"], 1)
        self.assertNotIn("arbitrary-user-controlled-value", counters)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000_000

    def __call__(self) -> int:
        self.now += 10_000_000
        return self.now


class SynchronizedFixtureRecorderTests(unittest.TestCase):
    def test_disabled_recorder_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixtures"
            recorder = SynchronizedFixtureRecorder(
                FixtureRecorderConfig(enabled=False, directory=root)
            )

            recorder.record_capture(b"\x01\x00")
            recorder.record_render(b"\x02\x00")
            recorder.close()

            self.assertFalse(root.exists())
            self.assertIsNone(recorder.session_directory)

    def test_opt_in_recorder_writes_wavs_and_timestamp_alignment(self):
        clock = _Clock()
        with tempfile.TemporaryDirectory() as temporary:
            recorder = SynchronizedFixtureRecorder(
                FixtureRecorderConfig(
                    enabled=True,
                    directory=Path(temporary),
                    max_duration_seconds=10,
                    max_pcm_bytes=1024,
                    max_sessions=2,
                    queue_frames=8,
                ),
                clock_ns=clock,
            )
            recorder.record_capture(b"\x01\x00\x02\x00")
            recorder.record_render(b"\x03\x00")
            recorder.record_capture(b"\x04\x00")
            recorder.close()

            session = recorder.session_directory
            self.assertIsNotNone(session)
            manifest = json.loads((session / "manifest.json").read_text("utf-8"))
            events = [
                json.loads(line)
                for line in (session / "events.jsonl").read_text("utf-8").splitlines()
            ]

            self.assertTrue(manifest["operator_opt_in_required"])
            self.assertTrue(manifest["contains_room_audio"])
            self.assertEqual(manifest["written_pcm_bytes"], 8)
            self.assertEqual([event["stream"] for event in events], [
                "capture",
                "render",
                "capture",
            ])
            self.assertEqual(
                [event["sample_start"] for event in events], [0, 0, 2]
            )
            self.assertEqual(
                [event["elapsed_ns"] for event in events],
                sorted(event["elapsed_ns"] for event in events),
            )

            with wave.open(str(session / "capture.wav"), "rb") as captured:
                self.assertEqual(captured.getframerate(), 16000)
                self.assertEqual(captured.getnframes(), 3)
            with wave.open(str(session / "render.wav"), "rb") as rendered:
                self.assertEqual(rendered.getnframes(), 1)

    def test_pcm_byte_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            recorder = SynchronizedFixtureRecorder(
                FixtureRecorderConfig(
                    enabled=True,
                    directory=Path(temporary),
                    max_duration_seconds=10,
                    max_pcm_bytes=4,
                    max_sessions=2,
                    queue_frames=8,
                )
            )
            recorder.record_capture(b"\x01\x00\x02\x00")
            recorder.record_render(b"\x03\x00")
            recorder.close()

            manifest = json.loads(
                (recorder.session_directory / "manifest.json").read_text("utf-8")
            )
            self.assertLessEqual(manifest["written_pcm_bytes"], 4)
            self.assertEqual(manifest["limit_reason"], "max_pcm_bytes")
            self.assertGreaterEqual(manifest["dropped_pcm_bytes"], 2)

    def test_retention_only_removes_owned_fixture_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for suffix in ("001", "002"):
                session = root / f"barge-in-fixture-{suffix}"
                session.mkdir()
                (session / "manifest.json").write_text("{}", encoding="utf-8")
            unrelated = root / "unrelated"
            unrelated.mkdir()

            recorder = SynchronizedFixtureRecorder(
                FixtureRecorderConfig(
                    enabled=True,
                    directory=root,
                    max_duration_seconds=10,
                    max_pcm_bytes=1024,
                    max_sessions=2,
                )
            )
            recorder.close()

            self.assertFalse((root / "barge-in-fixture-001").exists())
            self.assertTrue((root / "barge-in-fixture-002").exists())
            self.assertTrue(unrelated.exists())
            owned = [
                path
                for path in root.iterdir()
                if path.name.startswith("barge-in-fixture-")
            ]
            self.assertEqual(len(owned), 2)


if __name__ == "__main__":
    unittest.main()
