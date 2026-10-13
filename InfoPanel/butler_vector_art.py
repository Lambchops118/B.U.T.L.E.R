# Function to draw the portrait of Monkey Butler

# To Do:
# - Create a vector(s) for each of the body part's points, then just call pygame.draw.line a few times.

import math
import random

import pygame


def _scroll_rows(elapsed: float, *, base_speed: float, variation: float, frequency: float) -> float:
    """Return monotonically increasing row travel with a varying write rate."""
    elapsed = max(0.0, float(elapsed))
    # Integral of ``base_speed + variation * sin(frequency * t)``. Keeping the
    # instantaneous speed positive makes each terminal advance without ever
    # appearing to run backward.
    return (
        base_speed * elapsed
        + (variation / frequency) * (1.0 - math.cos(frequency * elapsed))
    )


def _looping_scroll_rows(
    elapsed: float,
    *,
    duration: float,
    total_rows: int,
    variation: float,
    phase: float,
) -> float:
    """Return a positive-speed scroll that closes exactly after one GIF loop."""
    progress = (max(0.0, float(elapsed)) % duration) / duration
    angle = math.tau * progress
    # The sine term changes the instantaneous speed, while subtracting its
    # starting value keeps travel exactly 0..total_rows over one period.
    return (
        total_rows * progress
        + variation * (math.sin(angle + phase) - math.sin(phase))
    )


def _code_chunks(line_number: int, lens_seed: int) -> list[tuple[float, float]]:
    """Create stable code-like horizontal chunks for one terminal row."""
    rng = random.Random((line_number * 1_000_003) ^ lens_seed)
    first_width = rng.uniform(0.38, 0.72)
    # Every row starts at the same terminal margin. Its generated endpoints are
    # stable for the lifetime of the row; only its Y coordinate changes.
    chunks = [(0.0, first_width)]

    # Some rows have a second token, producing a less barcode-like silhouette.
    if rng.random() < 0.42:
        gap = rng.uniform(0.08, 0.18)
        start = chunks[0][1] + gap
        if start < 0.88:
            chunks.append((start, min(0.97, start + rng.uniform(0.10, 0.30))))
    return chunks


def _draw_lens_terminal(
    surface,
    transform,
    *,
    center: tuple[int, int],
    radius: float,
    elapsed: float,
    lens_seed: int,
    base_speed: float,
    variation: float,
    frequency: float,
    color,
    loop_duration: float | None = None,
    loop_rows: int | None = None,
    loop_phase: float = 0.0,
) -> None:
    """Draw an independently scrolling terminal clipped to a circular lens."""
    cx, cy = center
    row_spacing = max(5, int(round(radius * 0.23)))
    inner_radius = max(1.0, radius - 3.0)
    # Keep a rectangular terminal viewport entirely inside the lens. This lets
    # a complete row enter at the bottom without being progressively revealed
    # by the circle's narrowing chord (which looked like the text was growing).
    terminal_half_width = inner_radius * 0.62
    terminal_half_height = inner_radius * 0.74
    terminal_left = cx - terminal_half_width
    terminal_width = terminal_half_width * 2
    if loop_duration is not None and loop_rows is not None:
        travel = _looping_scroll_rows(
            elapsed,
            duration=loop_duration,
            total_rows=loop_rows,
            variation=0.22,
            phase=loop_phase,
        )
    else:
        travel = _scroll_rows(
            elapsed,
            base_speed=base_speed,
            variation=variation,
            frequency=frequency,
        )
    newest_line = math.floor(travel)
    visible_rows = math.ceil((terminal_half_height * 2) / row_spacing) + 1

    for line_number in range(newest_line - visible_rows, newest_line + 1):
        # Pygame's portrait transform flips the design-space Y axis. Increasing
        # this logical Y therefore moves a fixed line upward on screen, while a
        # newly numbered line appears at the bottom terminal margin.
        y = cy - terminal_half_height + ((travel - line_number) * row_spacing)
        if y < cy - terminal_half_height or y > cy + terminal_half_height:
            continue

        pattern_number = line_number % loop_rows if loop_rows else line_number
        for start_ratio, end_ratio in _code_chunks(pattern_number, lens_seed):
            start_x = terminal_left + terminal_width * start_ratio
            end_x = terminal_left + terminal_width * end_ratio
            if end_x - start_x < 1.0:
                continue
            pygame.draw.line(
                surface,
                color,
                transform(start_x, y),
                transform(end_x, y),
                width=1,
            )


def draw_monkey_butler_head(
    screen,
    dx,
    dy,
    scale_x,
    scale_y,
    color,
    animation_time=0.0,
    loop_duration=None,
):
    screen_height = screen.get_height()

    def t(ax, ay):
        scaled_x = int(ax * scale_x - 200)
        scaled_y = int(ay * scale_y - 145)
        real_x   = dx + scaled_x
        real_y   = screen_height - (dy + scaled_y)
        return (real_x, real_y)
    
    #def scale_radius(radius, scale_x, scale_y):
        #initial_x = 3840
        #initial_y = 2160



    c = color
    color_black = (0, 0, 0)

    # NOSE
    pygame.draw.line(screen, c, t(465, 325), t(470, 310), width=1)
    pygame.draw.line(screen, c, t(470, 310), t(465, 270), width=1)
    pygame.draw.line(screen, c, t(490, 310), t(495, 325), width=1)
    pygame.draw.line(screen, c, t(475, 280), t(480, 270), width=1)
    pygame.draw.line(screen, c, t(485, 280), t(495, 270), width=1)
    pygame.draw.line(screen, c, t(465, 270), t(475, 280), width=1)
    pygame.draw.line(screen, c, t(480, 270), t(485, 280), width=1)
    pygame.draw.line(screen, c, t(495, 270), t(490, 310), width=1)

    # HEAD Outline
    pygame.draw.line(screen, c, t(365, 340), t(365, 305), width=1)
    pygame.draw.line(screen, c, t(595, 305), t(595, 340), width=1)
    pygame.draw.line(screen, c, t(365, 305), t(380, 300), width=1)
    pygame.draw.line(screen, c, t(595, 305), t(595, 340), width=1)
    pygame.draw.line(screen, c, t(380, 300), t(380, 240), width=1)
    pygame.draw.line(screen, c, t(580, 300), t(595, 305), width=1)
    pygame.draw.line(screen, c, t(380, 240), t(430, 215), width=1)
    pygame.draw.line(screen, c, t(580, 240), t(580, 300), width=1)
    pygame.draw.line(screen, c, t(430, 215), t(430, 200), width=1)
    pygame.draw.line(screen, c, t(530, 215), t(580, 240), width=1)
    pygame.draw.line(screen, c, t(430, 200), t(480, 190), width=1)
    pygame.draw.line(screen, c, t(530, 200), t(530, 215), width=1)
    pygame.draw.line(screen, c, t(480, 190), t(530, 200), width=1)

    # SNOUT / brow
    pygame.draw.line(screen, c, t(440, 385), t(460, 380), width=1)
    pygame.draw.line(screen, c, t(460, 380), t(480, 375), width=1)
    pygame.draw.line(screen, c, t(480, 375), t(500, 380), width=1)
    pygame.draw.line(screen, c, t(500, 380), t(520, 385), width=1)

    # muzzle and mouth
    pygame.draw.line(screen, c, t(455, 310), t(435, 265), width=1)
    pygame.draw.line(screen, c, t(435, 265), t(435, 250), width=1)
    pygame.draw.line(screen, c, t(525, 265), t(505, 310), width=1)
    pygame.draw.line(screen, c, t(435, 250), t(455, 225), width=1)
    pygame.draw.line(screen, c, t(525, 250), t(525, 265), width=1)
    pygame.draw.line(screen, c, t(525, 250), t(495, 255), width=1)
    pygame.draw.line(screen, c, t(455, 225), t(480, 220), width=1)
    pygame.draw.line(screen, c, t(505, 225), t(480, 220), width=1)
    pygame.draw.line(screen, c, t(505, 225), t(525, 250), width=1)
    pygame.draw.line(screen, c, t(465, 255), t(435, 250), width=1)
    pygame.draw.line(screen, c, t(465, 255), t(495, 255), width=1)

    # HAIR
    pygame.draw.line(screen, c, t(480, 425), t(460, 435), width=1)
    pygame.draw.line(screen, c, t(460, 435), t(400, 415), width=1)
    pygame.draw.line(screen, c, t(400, 415), t(365, 370), width=1)
    pygame.draw.line(screen, c, t(365, 370), t(345, 370), width=1)
    pygame.draw.line(screen, c, t(345, 370), t(365, 340), width=1)
    pygame.draw.line(screen, c, t(365, 340), t(400, 350), width=1)
    pygame.draw.line(screen, c, t(400, 350), t(440, 385), width=1)
    pygame.draw.line(screen, c, t(440, 385), t(480, 400), width=1)
    pygame.draw.line(screen, c, t(480, 400), t(520, 385), width=1)
    pygame.draw.line(screen, c, t(520, 385), t(575, 350), width=1)
    pygame.draw.line(screen, c, t(575, 350), t(595, 340), width=1)
    pygame.draw.line(screen, c, t(595, 340), t(640, 370), width=1)
    pygame.draw.line(screen, c, t(640, 370), t(610, 385), width=1)
    pygame.draw.line(screen, c, t(610, 385), t(560, 430), width=1)
    pygame.draw.line(screen, c, t(560, 430), t(535, 430), width=1)
    pygame.draw.line(screen, c, t(535, 430), t(480, 425), width=1)

    # GLASSES: draw the bridge and temples first, then mask their intersections
    # with opaque lenses. Terminal rows sit on those black lenses and the round
    # frames are restored last for an uninterrupted rim.
    pygame.draw.line(screen, c, t(430, 335), t(530, 335), width=1)
    pygame.draw.line(screen, c, t(430, 325), t(365, 340), width=1)
    pygame.draw.line(screen, c, t(530, 325), t(595, 340), width=1)
    pygame.draw.circle(screen, color_black, t(430, 335), 35*scale_x)
    pygame.draw.circle(screen, color_black, t(530, 335), 35*scale_x)

    _draw_lens_terminal(
        screen,
        t,
        center=(430, 335),
        radius=35,
        elapsed=animation_time,
        lens_seed=0xB071,
        base_speed=1.75,
        variation=0.70,
        frequency=1.15,
        color=c,
        loop_duration=loop_duration,
        loop_rows=7 if loop_duration is not None else None,
        loop_phase=0.0,
    )
    _draw_lens_terminal(
        screen,
        t,
        center=(530, 335),
        radius=35,
        elapsed=animation_time,
        lens_seed=0xC0DE,
        base_speed=1.30,
        variation=0.52,
        frequency=0.83,
        color=c,
        loop_duration=loop_duration,
        loop_rows=5 if loop_duration is not None else None,
        loop_phase=1.7,
    )

    pygame.draw.circle(screen, c, t(430, 335), 36*scale_x, width=1)
    pygame.draw.circle(screen, c, t(530, 335), 36*scale_x, width=1)

    # DRIP
    pygame.draw.line(screen, c, t(580, 260), t(675, 240), width=1)
    pygame.draw.line(screen, c, t(380, 260), t(290, 240), width=1)
