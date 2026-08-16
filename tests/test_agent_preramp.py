"""GPU pre-ramp: replaying the last prompt so idle clocks are not the turn's problem.

The GPU drops to its idle power state within ~10 s of no work, and the ramp back
is otherwise paid inline by the next turn. These tests pin the two properties that
make the pre-ramp worth having: it replays the *exact* last prompt (anything else
truncates the model server's cached prefix and costs more than it saves), and it
stands aside rather than queueing whenever a real turn is running.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from talos.agent import runtime as agent_runtime


class _RecordingBackend:
    """Stands in for the Ollama backend and remembers what it was asked to warm."""

    backend_name = "ollama"
    is_local = True
    model = "test-model"

    def __init__(self) -> None:
        self.calls: list[tuple[list[dict], list[dict] | None]] = []

    def warmup(self, messages, *, tools=None):
        self.calls.append((messages, tools))
        return 17.4


class PreRampTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = _RecordingBackend()
        patcher = mock.patch.object(
            agent_runtime, "_get_stream_backend", return_value=self.backend
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        emit = mock.patch.object(agent_runtime, "emit_pipeline_event")
        emit.start()
        self.addCleanup(emit.stop)

        enabled = mock.patch.object(agent_runtime, "PRERAMP_ENABLED", True)
        enabled.start()
        self.addCleanup(enabled.stop)

        # No debounce unless a test is specifically about debouncing.
        interval = mock.patch.object(
            agent_runtime, "PRERAMP_MIN_INTERVAL_SECONDS", 0.0
        )
        interval.start()
        self.addCleanup(interval.stop)

        self._reset_state()
        self.addCleanup(self._reset_state)

    def _reset_state(self) -> None:
        agent_runtime._last_prompt = None
        agent_runtime._last_preramp_monotonic = 0.0
        agent_runtime._active_turns = 0

    def test_replays_the_last_prompt_exactly(self) -> None:
        """The whole point: a different prompt would truncate the cached prefix."""
        messages = [
            {"role": "system", "content": "stable persona"},
            {"role": "user", "content": "prime the pump"},
        ]
        tools = [{"name": "pump_on"}]
        agent_runtime._record_preramp_prompt(messages, tools)

        result = agent_runtime.prewarm_llm()

        self.assertTrue(result["ok"])
        self.assertEqual(result["preramp_ms"], 17.4)
        self.assertEqual(len(self.backend.calls), 1)
        sent_messages, sent_tools = self.backend.calls[0]
        self.assertEqual(sent_messages, messages)
        self.assertEqual(sent_tools, tools)

    def test_records_a_snapshot_that_later_turn_growth_cannot_disturb(self) -> None:
        """The tool loop keeps appending to the same list it handed us."""
        messages = [{"role": "user", "content": "first"}]
        tools = [{"name": "pump_on"}]
        agent_runtime._record_preramp_prompt(messages, tools)

        messages.append({"role": "assistant", "content": "a later round"})
        tools.append({"name": "added_later"})

        agent_runtime.prewarm_llm()

        sent_messages, sent_tools = self.backend.calls[0]
        self.assertEqual(sent_messages, [{"role": "user", "content": "first"}])
        self.assertEqual(sent_tools, [{"name": "pump_on"}])

    def test_skipped_before_anything_has_been_sent(self) -> None:
        """With no cached prefix to replay there is nothing safe to send."""
        result = agent_runtime.prewarm_llm()

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_prompt")
        self.assertEqual(self.backend.calls, [])

    def test_stands_aside_while_a_real_turn_is_running(self) -> None:
        """Queueing behind a live turn would delay the very thing it speeds up."""
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])

        with agent_runtime._turn_in_flight():
            result = agent_runtime.prewarm_llm()

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "turn_in_flight")
        self.assertEqual(self.backend.calls, [])

    def test_turn_marker_clears_after_the_turn_ends(self) -> None:
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])
        with agent_runtime._turn_in_flight():
            pass

        self.assertTrue(agent_runtime.prewarm_llm()["ok"])

    def test_turn_marker_clears_when_the_turn_raises(self) -> None:
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])
        with self.assertRaises(RuntimeError):
            with agent_runtime._turn_in_flight():
                raise RuntimeError("turn blew up")

        self.assertEqual(agent_runtime._active_turns, 0)
        self.assertTrue(agent_runtime.prewarm_llm()["ok"])

    def test_debounces_a_burst_of_speech(self) -> None:
        """One ramp is enough; the GPU does not idle down again in between."""
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])
        with mock.patch.object(agent_runtime, "PRERAMP_MIN_INTERVAL_SECONDS", 30.0):
            first = agent_runtime.prewarm_llm()
            second = agent_runtime.prewarm_llm()

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "debounced")
        self.assertEqual(len(self.backend.calls), 1)

    def test_disabled_by_flag(self) -> None:
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])
        with mock.patch.object(agent_runtime, "PRERAMP_ENABLED", False):
            result = agent_runtime.prewarm_llm()

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "disabled")
        self.assertEqual(self.backend.calls, [])

    def test_recording_is_skipped_while_disabled(self) -> None:
        with mock.patch.object(agent_runtime, "PRERAMP_ENABLED", False):
            agent_runtime._record_preramp_prompt([{"role": "user", "content": "x"}], [])

        self.assertIsNone(agent_runtime._last_prompt)

    def test_a_failing_warmup_never_escapes(self) -> None:
        """A pre-ramp is pure optimization; the turn behind it must be unaffected."""
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])
        self.backend.warmup = mock.Mock(side_effect=RuntimeError("ollama is down"))

        result = agent_runtime.prewarm_llm()

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "error")

    def test_a_failing_warmup_does_not_hold_the_lock(self) -> None:
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])
        self.backend.warmup = mock.Mock(side_effect=RuntimeError("ollama is down"))
        agent_runtime.prewarm_llm()

        self.assertTrue(agent_runtime._preramp_request_lock.acquire(blocking=False))
        agent_runtime._preramp_request_lock.release()

    def test_backend_without_warmup_is_tolerated(self) -> None:
        """A hosted backend has no prefix to warm and no warmup() to call."""
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])

        class _NoWarmupBackend:
            backend_name = "openai_chat"

        with mock.patch.object(
            agent_runtime, "_get_stream_backend", return_value=_NoWarmupBackend()
        ):
            result = agent_runtime.prewarm_llm()

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsupported_backend")

    def test_only_one_pre_ramp_runs_at_a_time(self) -> None:
        """Two clips in flight must not put two requests on the model server."""
        agent_runtime._record_preramp_prompt([{"role": "user", "content": "hi"}], [])
        entered = threading.Event()
        release = threading.Event()
        results: list[dict] = []

        def blocking_warmup(messages, *, tools=None):
            entered.set()
            release.wait(timeout=5)
            return 17.4

        self.backend.warmup = blocking_warmup
        first = threading.Thread(target=lambda: results.append(agent_runtime.prewarm_llm()))
        first.start()
        self.assertTrue(entered.wait(timeout=5))

        second = agent_runtime.prewarm_llm()
        release.set()
        first.join(timeout=5)

        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "already_running")
        self.assertTrue(results[0]["ok"])


class StreamBackendCacheTest(unittest.TestCase):
    """Building the backend costs ~150 ms, so a turn must never pay for it."""

    def setUp(self) -> None:
        self._saved = agent_runtime._stream_backend
        agent_runtime._stream_backend = None
        self.addCleanup(setattr, agent_runtime, "_stream_backend", self._saved)

    def test_built_once_and_reused(self) -> None:
        built: list[object] = []

        def factory():
            backend = _RecordingBackend()
            built.append(backend)
            return backend

        with mock.patch(
            "talos.voice.backends.factory.get_llm_backend", side_effect=factory
        ):
            first = agent_runtime._get_stream_backend()
            second = agent_runtime._get_stream_backend()
            third = agent_runtime._get_stream_backend()

        self.assertEqual(len(built), 1)
        self.assertIs(first, second)
        self.assertIs(second, third)

    def test_concurrent_callers_share_one_backend(self) -> None:
        """A pre-ramp and a turn can ask for it at the same moment."""
        start = threading.Barrier(8)
        seen: list[object] = []
        built: list[object] = []

        def factory():
            backend = _RecordingBackend()
            built.append(backend)
            return backend

        def worker():
            start.wait(timeout=5)
            seen.append(agent_runtime._get_stream_backend())

        with mock.patch(
            "talos.voice.backends.factory.get_llm_backend", side_effect=factory
        ):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(len(built), 1)
        self.assertEqual(len({id(backend) for backend in seen}), 1)


if __name__ == "__main__":
    unittest.main()
