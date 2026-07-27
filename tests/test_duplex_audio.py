import threading
import unittest

import speech_recognition as sr

from talos.voice.streaming.duplex import (
    BoundedFrameQueue,
    DuplexAudioPipeline,
    DuplexRecognizerAudioSource,
)


class FakeProcessor:
    def __init__(self):
        self.callback = None
        self.running = False
        self.error = None

    def start(self, callback):
        self.callback = callback
        self.running = True

    def stop(self, timeout=5.0):
        self.running = False

    def snapshot(self):
        return {"fake": True, "running": self.running, "error": self.error}


class DuplexAudioPipelineTests(unittest.TestCase):
    def test_bounded_queue_drops_oldest_without_blocking(self):
        frames = BoundedFrameQueue(2)
        for value in (b"a", b"b", b"c"):
            frames.put_nowait(value)
        self.assertEqual((frames.get(), frames.get()), (b"b", b"c"))
        self.assertEqual(frames.snapshot()["dropped"], 1)

    def test_capture_is_fanned_to_recognizer(self):
        processor = FakeProcessor()
        seen = threading.Event()
        pipeline = DuplexAudioPipeline(processor, on_clean_frame=lambda _: seen.set())
        pipeline.start()
        try:
            processor.callback(b"\x01\x02" * 160)
            self.assertTrue(seen.wait(1.0))
            source = DuplexRecognizerAudioSource(pipeline)
            self.assertIsInstance(source, sr.AudioSource)
            with source:
                self.assertEqual(source.stream.read(160), b"\x01\x02" * 160)
        finally:
            pipeline.stop()

    def test_recognizer_is_silenced_only_while_speaking(self):
        processor = FakeProcessor()
        pipeline = DuplexAudioPipeline(processor)
        pipeline.start()
        try:
            self.assertTrue(pipeline.note_render_submitted(b"\x03\x04" * 160))
            processor.callback(b"\x01\x02" * 160)
            source = DuplexRecognizerAudioSource(pipeline)
            with source:
                self.assertEqual(source.stream.read(160), b"\x00" * 320)
            pipeline.finish_speaking()
            processor.callback(b"\x05\x06" * 160)
            with source:
                self.assertEqual(source.stream.read(160), b"\x05\x06" * 160)
        finally:
            pipeline.stop()

    def test_processor_failure_marks_pipeline_unhealthy(self):
        processor = FakeProcessor()
        pipeline = DuplexAudioPipeline(processor)
        pipeline.start()
        try:
            self.assertTrue(pipeline.healthy)
            processor.error = "device changed"
            processor.running = False
            threading.Event().wait(0.3)
            self.assertFalse(pipeline.healthy)
            self.assertFalse(pipeline.speaking)
        finally:
            pipeline.stop()


if __name__ == "__main__":
    unittest.main()
