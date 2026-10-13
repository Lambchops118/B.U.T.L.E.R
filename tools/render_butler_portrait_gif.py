"""Render the InfoPanel's Butler portrait as a deterministic README GIF.

This reuses the production vector drawing function without opening the full
InfoPanel, connecting to Butler, or requiring an OpenGL context.

Install the one export-only dependency before running::

    .venv-display/bin/python -m pip install Pillow
    .venv-display/bin/python tools/render_butler_portrait_gif.py
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

# Pygame must select the headless backend before it is imported.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from InfoPanel.butler_vector_art import draw_monkey_butler_head


WIDTH = 600
HEIGHT = 450
FPS = 60
DURATION_SECONDS = 3
BACKGROUND = (0, 1, 0)
GREEN = (0, 255, 100)
OUTPUT = REPO_ROOT / "docs" / "assets" / "butler-portrait.gif"


def _add_bloom(surface: pygame.Surface, alpha: int) -> None:
    small = pygame.transform.smoothscale(surface, (WIDTH // 4, HEIGHT // 4))
    glow = pygame.transform.smoothscale(small, (WIDTH, HEIGHT))
    glow.set_alpha(alpha)
    surface.blit(glow, (0, 0), special_flags=pygame.BLEND_ADD)


def _add_crt_mask(surface: pygame.Surface, phase: int) -> None:
    overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    for y in range(phase, HEIGHT, 4):
        pygame.draw.line(overlay, (0, 0, 0, 105), (0, y), (WIDTH, y))
    for x in range(0, WIDTH, 6):
        pygame.draw.line(overlay, (0, 0, 0, 24), (x, 0), (x, HEIGHT))
    surface.blit(overlay, (0, 0))


def _to_pillow(surface: pygame.Surface) -> Image.Image:
    pixels = pygame.image.tostring(surface, "RGB")
    return Image.frombytes("RGB", surface.get_size(), pixels)


def _gif_frame_durations(frame_count: int) -> list[int]:
    """Distribute GIF centiseconds to average the requested frame rate exactly."""
    boundaries = [round(index * 100 / FPS) for index in range(frame_count + 1)]
    return [
        (boundaries[index + 1] - boundaries[index]) * 10
        for index in range(frame_count)
    ]


def render() -> Path:
    pygame.init()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    frames: list[Image.Image] = []
    frame_count = FPS * DURATION_SECONDS

    for index in range(frame_count):
        phase = index / frame_count
        bob = round(math.sin(phase * math.tau) * 5)
        glow = round(50 + 12 * math.sin(phase * math.tau))

        surface = pygame.Surface((WIDTH, HEIGHT))
        surface.fill(BACKGROUND)

        # The original portrait uses design-space coordinates and flips Y
        # against the destination surface. These values center its full bounds
        # in this export without changing the production drawing function.
        draw_monkey_butler_head(
            surface,
            dx=-88,
            dy=23 + bob,
            scale_x=1.2,
            scale_y=1.2,
            color=GREEN,
            animation_time=index / FPS,
            loop_duration=DURATION_SECONDS,
        )

        _add_bloom(surface, glow)
        _add_crt_mask(surface, index % 4)

        # Match the portrait panel's restrained green border.
        pygame.draw.rect(surface, (0, 150, 60), surface.get_rect(), width=2)
        frames.append(_to_pillow(surface))

    frames[0].save(
        OUTPUT,
        save_all=True,
        append_images=frames[1:],
        duration=_gif_frame_durations(frame_count),
        loop=0,
        optimize=True,
        disposal=2,
    )

    pygame.quit()
    return OUTPUT


if __name__ == "__main__":
    print(f"Wrote {render()}")
