from __future__ import annotations

import unittest

from talos.voice.asr_queue import AsrPriority, BoundedAsrQueue


class BoundedAsrQueueTests(unittest.TestCase):
    def test_idle_command_precedes_queued_barge_in_confirmation(self):
        work = BoundedAsrQueue(capacity=3)
        self.assertTrue(
            work.put_nowait(
                kind="barge_in",
                payload="old confirmation",
                priority=AsrPriority.BARGE_IN_CONFIRMATION,
            )
        )
        self.assertTrue(
            work.put_nowait(
                kind="idle",
                payload="new command",
                priority=AsrPriority.IDLE_COMMAND,
            )
        )

        self.assertEqual(work.get().payload, "new command")
        self.assertEqual(work.get().payload, "old confirmation")

    def test_same_priority_remains_fifo(self):
        work = BoundedAsrQueue(capacity=2)
        for payload in ("first", "second"):
            self.assertTrue(
                work.put_nowait(
                    kind="idle",
                    payload=payload,
                    priority=AsrPriority.IDLE_COMMAND,
                )
            )
        self.assertEqual(work.get().payload, "first")
        self.assertEqual(work.get().payload, "second")

    def test_capacity_is_bounded(self):
        work = BoundedAsrQueue(capacity=1)
        self.assertTrue(
            work.put_nowait(
                kind="idle",
                payload="accepted",
                priority=AsrPriority.IDLE_COMMAND,
            )
        )
        self.assertFalse(
            work.put_nowait(
                kind="idle",
                payload="dropped",
                priority=AsrPriority.IDLE_COMMAND,
            )
        )
        self.assertEqual(work.depth, 1)
        self.assertEqual(work.capacity, 1)


if __name__ == "__main__":
    unittest.main()
