"""Unit tests for main-agent → awareness signal emission (no network, no DB).

The signal emitter sits on the voice hot path, so its three safety properties
are the ones worth testing: it never blocks the caller, it never raises, and
its queue is hard-bounded with dropped signals counted rather than hidden.
"""

from __future__ import annotations

import unittest

from talos.services import awareness_signals


class _CapturingClient:
    """Stands in for awareness_client.post_json."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.error: Exception | None = None

    def __call__(self, path: str, body: dict) -> dict:
        if self.error is not None:
            raise self.error
        self.bodies.append({"path": path, **body})
        return {"accepted": True}


class SignalEmissionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = _CapturingClient()
        self._original = awareness_signals.awareness_client.post_json
        awareness_signals.awareness_client.post_json = self.capture
        # Reset the module's rate-limit clock so tests do not suppress
        # each other's presence signals.
        awareness_signals._last_presence_at = 0.0

    def tearDown(self) -> None:
        awareness_signals.awareness_client.post_json = self._original

    def _drain(self) -> None:
        queue = awareness_signals._queue
        if queue is not None:
            queue.join()

    def test_presence_is_sent_on_the_internal_transport(self) -> None:
        awareness_signals.record_presence(modality="wake_word", force=True)
        self._drain()
        self.assertTrue(self.capture.bodies)
        body = self.capture.bodies[-1]
        self.assertEqual(body["path"], "/ingest")
        self.assertEqual(body["topic"], awareness_signals.PRESENCE_TOPIC)
        self.assertEqual(body["transport"], "internal")
        self.assertTrue(body["payload"]["present"])
        self.assertEqual(body["payload"]["modality"], "wake_word")
        # Every signal carries its own id and observation time.
        self.assertIn("event_id", body["payload"])
        self.assertIn("observed_at", body["payload"])

    def test_presence_is_rate_limited_unless_forced(self) -> None:
        awareness_signals.record_presence(modality="voice", force=True)
        awareness_signals.record_presence(modality="voice")  # suppressed
        awareness_signals.record_presence(modality="voice")  # suppressed
        self._drain()
        self.assertEqual(len(self.capture.bodies), 1)

        awareness_signals.record_presence(modality="wake_word", force=True)
        self._drain()
        self.assertEqual(len(self.capture.bodies), 2)

    def test_interaction_records_facts_and_never_transcripts(self) -> None:
        awareness_signals.record_interaction_started(
            session_id="voice", modality="voice", source="voice", routing_mode="status"
        )
        awareness_signals.record_interaction_ended(
            session_id="voice", modality="voice", duration_seconds=1.25, ok=True
        )
        self._drain()
        started, ended = self.capture.bodies[-2], self.capture.bodies[-1]
        self.assertEqual(started["topic"], awareness_signals.INTERACTION_TOPIC)
        self.assertEqual(
            started["payload"]["event_type"], "person.interaction.started"
        )
        self.assertEqual(ended["payload"]["event_type"], "person.interaction.ended")
        self.assertEqual(ended["payload"]["duration_seconds"], 1.25)
        # The API deliberately offers no way to pass utterance text.
        for body in (started, ended):
            self.assertNotIn("command", body["payload"])
            self.assertNotIn("text", body["payload"])
            self.assertNotIn("transcript", body["payload"])

    def test_agent_events_carry_type_and_severity(self) -> None:
        awareness_signals.record_agent_event(
            "agent.job.failed",
            {"job_id": "abc", "error": "boom"},
            severity="warning",
            entity_ids=["quad_pump"],
        )
        self._drain()
        body = self.capture.bodies[-1]
        self.assertEqual(body["topic"], awareness_signals.AGENT_TOPIC)
        self.assertEqual(body["payload"]["event_type"], "agent.job.failed")
        self.assertEqual(body["payload"]["severity"], "warning")
        self.assertEqual(body["payload"]["entity_ids"], ["quad_pump"])

    def test_backend_failure_never_reaches_the_caller(self) -> None:
        self.capture.error = RuntimeError("awareness backend unreachable")
        before = awareness_signals.stats()["failed"]
        awareness_signals.record_presence(modality="voice", force=True)
        self._drain()
        # The caller saw no exception; the failure is counted, not swallowed
        # silently.
        self.assertGreater(awareness_signals.stats()["failed"], before)

    def test_queue_is_bounded_and_drops_are_counted(self) -> None:
        """A stalled backend must not grow this queue without limit."""
        import queue as queue_module

        work_queue: "queue_module.Queue[dict]" = queue_module.Queue(
            maxsize=awareness_signals.MAX_QUEUE_DEPTH
        )
        original_queue = awareness_signals._queue
        original_ensure = awareness_signals._ensure_worker
        awareness_signals._queue = work_queue
        # No worker: simulate a backend that never drains.
        awareness_signals._ensure_worker = lambda: work_queue
        try:
            before = awareness_signals.stats()["dropped"]
            overshoot = awareness_signals.MAX_QUEUE_DEPTH + 25
            for index in range(overshoot):
                awareness_signals.record_agent_event(
                    "agent.tool.failed", {"n": index}
                )
            self.assertEqual(work_queue.qsize(), awareness_signals.MAX_QUEUE_DEPTH)
            self.assertGreaterEqual(
                awareness_signals.stats()["dropped"] - before, 25
            )
            # The newest signal survived; the oldest was dropped.
            remaining = [work_queue.get_nowait() for _ in range(work_queue.qsize())]
            self.assertEqual(remaining[-1]["payload"]["n"], overshoot - 1)
        finally:
            awareness_signals._queue = original_queue
            awareness_signals._ensure_worker = original_ensure

    def test_disabled_by_environment(self) -> None:
        import os

        original = os.environ.get("TALOS_AWARENESS_SIGNALS_ENABLED")
        os.environ["TALOS_AWARENESS_SIGNALS_ENABLED"] = "0"
        try:
            self.assertFalse(awareness_signals.signals_enabled())
            before = len(self.capture.bodies)
            awareness_signals.record_presence(modality="voice", force=True)
            awareness_signals.record_agent_event("agent.job.completed", {})
            self.assertEqual(len(self.capture.bodies), before)
        finally:
            if original is None:
                os.environ.pop("TALOS_AWARENESS_SIGNALS_ENABLED", None)
            else:
                os.environ["TALOS_AWARENESS_SIGNALS_ENABLED"] = original


if __name__ == "__main__":
    unittest.main()
