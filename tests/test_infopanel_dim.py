"""The info panel's sleep dim, measured at the pixel rather than asserted.

The dim used to be a CPU-side BLEND_MULT applied to the frame *before* the CRT
shader. That shader ends in a 1/gamma pass, so a 1% multiply came back out at
roughly 18% of its awake brightness on the glass -- the panel looked barely
dimmed -- and the 8-bit multiply had already crushed the mid-tones on the way
in. The dim now lives in the shader, after gamma, and these tests render real
frames to prove the requested level is the level that reaches the screen.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import moderngl
    import numpy as np
except ImportError as exc:  # pragma: no cover - GPU deps are optional
    raise unittest.SkipTest(f"moderngl/numpy not installed: {exc}")

from InfoPanel.screen_effects import FS_SRC, VS_SRC

SIZE = (64, 64)
MID_GREY = 128


class ShaderDimTest(unittest.TestCase):
    """Renders the real fragment shader into an offscreen buffer."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.ctx = moderngl.create_standalone_context(require=330)
        except Exception as exc:  # pragma: no cover - no GL on this host
            raise unittest.SkipTest(f"no OpenGL 3.3 context available: {exc}")
        cls.prog = cls.ctx.program(vertex_shader=VS_SRC, fragment_shader=FS_SRC)
        quad = np.array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1], dtype="f4")
        cls.vao = cls.ctx.simple_vertex_array(
            cls.prog, cls.ctx.buffer(quad.tobytes()), "in_pos"
        )
        cls.tex = cls.ctx.texture(
            SIZE, 3, np.full((*SIZE, 3), MID_GREY, dtype="u1").tobytes()
        )
        cls.tex.use(0)
        # The same uniforms screen.py constructs GpuCRT with.
        for name, value in (
            ("u_kx", 0.18), ("u_ky", 0.16), ("u_curv", 0.3), ("u_scan", 0.18),
            ("u_vign", 0.45), ("u_gamma", 2.0), ("u_zoom", 1.05),
        ):
            cls.prog[name].value = value
        cls.prog["u_texSize"].value = SIZE
        cls.prog["u_tex"].value = 0
        cls.fbo = cls.ctx.simple_framebuffer(SIZE)
        cls.fbo.use()

    def _center_pixel(self, dim: float) -> int:
        self.prog["u_dim"].value = dim
        self.fbo.clear(0, 0, 0, 1)
        self.vao.render()
        pixels = np.frombuffer(self.fbo.read(components=3), dtype="u1")
        return int(pixels.reshape(SIZE[1], SIZE[0], 3)[SIZE[1] // 2, SIZE[0] // 2, 0])

    def test_a_one_percent_dim_really_reaches_the_glass_as_one_percent(self) -> None:
        awake = self._center_pixel(1.0)
        asleep = self._center_pixel(0.01)
        self.assertGreater(awake, 100, "awake frame should be a normal picture")
        # 8-bit output quantization is the only slack allowed here; the old
        # pre-gamma path landed near 0.18, which this bound excludes outright.
        self.assertLess(asleep / awake, 0.02)

    def test_dim_is_proportional_across_levels(self) -> None:
        awake = self._center_pixel(1.0)
        for level in (0.5, 0.25, 0.1):
            with self.subTest(level=level):
                self.assertAlmostEqual(
                    self._center_pixel(level) / awake, level, delta=0.02
                )

    def test_full_brightness_leaves_the_picture_alone(self) -> None:
        self.prog["u_dim"].value = 1.0
        self.fbo.clear(0, 0, 0, 1)
        self.vao.render()
        lit = np.frombuffer(self.fbo.read(components=3), dtype="u1").copy()
        self.assertGreater(int(lit.max()), 150)


class SetDimTest(unittest.TestCase):
    """GpuCRT.set_dim clamps and skips redundant uploads, without needing a window."""

    class _FakeProgram(dict):
        class _Uniform:
            def __init__(self) -> None:
                self.value = 1.0
                self.writes = 0

        def __init__(self) -> None:
            super().__init__()
            self["u_dim"] = self._Uniform()

        def __setitem__(self, key, value):  # pragma: no cover - unused
            super().__setitem__(key, value)

    def _set_dim(self, program, level):
        from InfoPanel.screen_effects import GpuCRT

        crt = GpuCRT.__new__(GpuCRT)  # no GL context, no window
        crt.prog = program
        GpuCRT.set_dim(crt, level)

    def test_levels_are_clamped_to_a_valid_range(self) -> None:
        program = self._FakeProgram()
        for given, expected in ((-5, 0.0), (0.01, 0.01), (7, 1.0)):
            self._set_dim(program, given)
            self.assertAlmostEqual(program["u_dim"].value, expected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
