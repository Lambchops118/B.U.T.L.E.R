import unittest

import numpy as np

from talos.voice.streaming.windows_audio import normalize_audio_graph_pcm


class NormalizeAudioGraphPcmTests(unittest.TestCase):
    def test_converts_one_float32_quantum_to_pcm16(self):
        native = np.array([-1.0, -0.5, 0.0, 0.5, 1.0], dtype=np.float32).tobytes()

        pcm = normalize_audio_graph_pcm(
            native,
            samples_per_quantum=5,
            channels=1,
        )

        self.assertEqual(
            np.frombuffer(pcm, dtype=np.int16).tolist(),
            [-32767, -16383, 0, 16383, 32767],
        )

    def test_leaves_unexpected_frame_shape_unchanged(self):
        pcm = b"\x01\x02" * 5

        self.assertIs(
            normalize_audio_graph_pcm(
                pcm,
                samples_per_quantum=5,
                channels=1,
            ),
            pcm,
        )


if __name__ == "__main__":
    unittest.main()
