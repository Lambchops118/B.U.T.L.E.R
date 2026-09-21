import math

from rich.color import Color
from rich.style import Style
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)


# ============================================================
# 2D ANIMATION
# ============================================================

class Animation2D(Widget):
    """Simple bouncing 2D block rendered into terminal cells."""

    DEFAULT_CSS = """
    Animation2D {
        height: 1fr;
        border: round cyan;
        padding: 1;
    }
    """

    def __init__(self):
        super().__init__()

        self.x = 2.0
        self.y = 2.0

        self.dx = 0.65
        self.dy = 0.35

        self.angle = 0.0

    def on_mount(self):
        # ~30 FPS
        self.set_interval(1 / 30, self.animate)

    def animate(self):
        width = max(self.size.width - 4, 10)
        height = max(self.size.height - 4, 5)

        self.x += self.dx
        self.y += self.dy
        self.angle += 0.12

        # Bounce off edges
        if self.x <= 0:
            self.x = 0
            self.dx *= -1

        if self.x >= width - 5:
            self.x = width - 5
            self.dx *= -1

        if self.y <= 0:
            self.y = 0
            self.dy *= -1

        if self.y >= height - 3:
            self.y = height - 3
            self.dy *= -1

        self.refresh()

    def render(self):
        width = max(self.size.width - 4, 10)
        height = max(self.size.height - 4, 5)

        buffer = [[" " for _ in range(width)] for _ in range(height)]

        x = int(self.x)
        y = int(self.y)

        # Change block appearance while it moves
        frames = [
            [
                "████",
                "████",
            ],
            [
                "▓▓▓▓",
                "████",
            ],
            [
                "▒▒▒▒",
                "▓▓▓▓",
            ],
            [
                "▓▓▓▓",
                "▒▒▒▒",
            ],
        ]

        frame = frames[int(self.angle * 2) % len(frames)]

        for row_index, row in enumerate(frame):
            for col_index, char in enumerate(row):

                px = x + col_index
                py = y + row_index

                if 0 <= px < width and 0 <= py < height:
                    buffer[py][px] = char

        result = Text()

        result.append("2D TERMINAL RASTER\n\n", style="bold cyan")

        for row in buffer:
            result.append("".join(row))
            result.append("\n")

        return result


# ============================================================
# 3D RENDERER
# ============================================================

class Cube3D(Widget):
    """Rotating shaded cube rendered with terminal subpixels."""

    RENDER_TITLE = "3D HOLOGRAPHIC CUBE"
    RENDER_SUBTITLE = "6 faces • z-buffer • 2×4 subpixels"
    FACE_COLOUR_A = (20, 225, 255)
    FACE_COLOUR_B = (205, 55, 255)
    EDGE_COLOUR = (145, 245, 255)
    TITLE_STYLE = "bold green"
    SUBTITLE_STYLE = "dim cyan"

    DEFAULT_CSS = """
    Cube3D {
        height: 1fr;
        border: round green;
        padding: 1;
    }
    """

    def __init__(self):
        super().__init__()

        self.angle_x = 0.0
        self.angle_y = 0.0
        self.angle_z = 0.0

    def on_mount(self):
        # ~30 FPS
        self.set_interval(1 / 30, self.animate)

    def animate(self):
        self.angle_x += 0.018
        self.angle_y += 0.032
        self.angle_z += 0.012

        self.refresh()

    # --------------------------------------------------------
    # 3D math
    # --------------------------------------------------------

    def rotate_point(self, point):
        x, y, z = point

        # Rotate around X
        cos_x = math.cos(self.angle_x)
        sin_x = math.sin(self.angle_x)

        y, z = (
            y * cos_x - z * sin_x,
            y * sin_x + z * cos_x,
        )

        # Rotate around Y
        cos_y = math.cos(self.angle_y)
        sin_y = math.sin(self.angle_y)

        x, z = (
            x * cos_y + z * sin_y,
            -x * sin_y + z * cos_y,
        )

        # Rotate around Z
        cos_z = math.cos(self.angle_z)
        sin_z = math.sin(self.angle_z)

        x, y = (
            x * cos_z - y * sin_z,
            x * sin_z + y * cos_z,
        )

        return x, y, z

    def project(self, point, width, height):
        x, y, z = point

        # Move cube away from camera
        camera_distance = 4.0

        z += camera_distance

        # Braille cells provide a 2x4 dot grid. With a terminal cell roughly
        # twice as tall as it is wide, those dots are approximately square, so
        # the subpixel canvas no longer needs an additional Y-axis correction.
        scale = min(width, height) * 1.15

        projected_x = (
            width / 2 + (x / z) * scale
        )

        projected_y = (
            height / 2 - (y / z) * scale
        )

        return projected_x, projected_y, z

    # --------------------------------------------------------
    # Software rasterization
    # --------------------------------------------------------

    @staticmethod
    def _normalise(vector):
        length = math.sqrt(sum(component * component for component in vector))
        return tuple(component / length for component in vector)

    @staticmethod
    def _mix(first, second, amount):
        return tuple(
            int(a + (b - a) * amount)
            for a, b in zip(first, second)
        )

    @staticmethod
    def _face_normal(vertices, indices):
        """Calculate an outward face normal from counter-clockwise vertices."""

        first, second, third = (vertices[index] for index in indices[:3])
        edge_a = tuple(b - a for a, b in zip(first, second))
        edge_b = tuple(b - a for a, b in zip(first, third))
        return Cube3D._normalise((
            edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
            edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
            edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
        ))

    def mesh(self):
        """Return the cube's vertices and counter-clockwise polygon faces."""

        vertices = [
            (-1, -1, -1),
            ( 1, -1, -1),
            ( 1,  1, -1),
            (-1,  1, -1),

            (-1, -1,  1),
            ( 1, -1,  1),
            ( 1,  1,  1),
            (-1,  1,  1),
        ]
        faces = [
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 4, 7, 3),
            (1, 2, 6, 5),
            (0, 1, 5, 4),
            (3, 7, 6, 2),
        ]
        return vertices, faces

    def _draw_triangle(self, pixels, depth, points, colours):
        """Fill a projected triangle using barycentric coordinates and a z-buffer."""

        height = len(pixels)
        width = len(pixels[0])
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = points

        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 0.0001:
            return

        min_x = max(0, int(math.floor(min(x0, x1, x2))))
        max_x = min(width - 1, int(math.ceil(max(x0, x1, x2))))
        min_y = max(0, int(math.floor(min(y0, y1, y2))))
        max_y = min(height - 1, int(math.ceil(max(y0, y1, y2))))

        for py in range(min_y, max_y + 1):
            for px in range(min_x, max_x + 1):
                sample_x = px + 0.5
                sample_y = py + 0.5
                weight0 = (
                    (y1 - y2) * (sample_x - x2)
                    + (x2 - x1) * (sample_y - y2)
                ) / denominator
                weight1 = (
                    (y2 - y0) * (sample_x - x2)
                    + (x0 - x2) * (sample_y - y2)
                ) / denominator
                weight2 = 1.0 - weight0 - weight1

                if min(weight0, weight1, weight2) < 0:
                    continue

                pixel_depth = weight0 * z0 + weight1 * z1 + weight2 * z2
                if pixel_depth >= depth[py][px]:
                    continue

                depth[py][px] = pixel_depth
                pixels[py][px] = tuple(
                    int(
                        weight0 * colours[0][channel]
                        + weight1 * colours[1][channel]
                        + weight2 * colours[2][channel]
                    )
                    for channel in range(3)
                )

    @staticmethod
    def _draw_edge(pixels, depth, start, end, colour):
        """Add a depth-tested highlight to a cube edge."""

        x0, y0, z0 = start
        x1, y1, z1 = end
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0))))
        height = len(pixels)
        width = len(pixels[0])

        for step in range(steps + 1):
            amount = step / steps
            x = round(x0 + (x1 - x0) * amount)
            y = round(y0 + (y1 - y0) * amount)
            z = z0 + (z1 - z0) * amount

            if 0 <= x < width and 0 <= y < height and z <= depth[y][x] + 0.06:
                pixels[y][x] = colour
                depth[y][x] = min(depth[y][x], z)

    # --------------------------------------------------------
    # Render cube
    # --------------------------------------------------------

    def render(self):

        width = max(self.size.width - 4, 20)
        height = max(self.size.height - 4, 10)

        # Two lines are reserved for the title. Each remaining terminal cell is
        # split into a 2x4 Unicode Braille grid for a higher-resolution silhouette.
        canvas_rows = max(1, height - 2)
        pixel_width = width * 2
        pixel_height = canvas_rows * 4
        pixels = [[None for _ in range(pixel_width)] for _ in range(pixel_height)]
        depth = [[math.inf for _ in range(pixel_width)] for _ in range(pixel_height)]

        vertices, faces = self.mesh()
        edges = sorted({
            tuple(sorted((indices[index], indices[(index + 1) % len(indices)])))
            for indices in faces
            for index in range(len(indices))
        })

        rotated = [self.rotate_point(vertex) for vertex in vertices]
        projected = [
            self.project(vertex, pixel_width, pixel_height)
            for vertex in rotated
        ]
        light = self._normalise((-0.45, -0.65, -1.0))
        for indices in faces:
            normal = self._face_normal(vertices, indices)
            rotated_normal = self.rotate_point(normal)
            diffuse = max(
                0.0,
                sum(a * b for a, b in zip(rotated_normal, light)),
            )
            brightness = 0.28 + diffuse * 0.72
            face_colours = []

            for index in indices:
                x, y, z = rotated[index]
                gradient = max(0.0, min(1.0, 0.5 + y * 0.24 + x * 0.08))
                colour = self._mix(
                    self.FACE_COLOUR_A,
                    self.FACE_COLOUR_B,
                    gradient,
                )
                # Fade the farther side very slightly to reinforce depth.
                depth_fade = max(0.72, min(1.0, 1.08 - (z + 1) * 0.09))
                face_colours.append(tuple(
                    int(channel * brightness * depth_fade)
                    for channel in colour
                ))

            for index in range(1, len(indices) - 1):
                triangle = (0, index, index + 1)
                self._draw_triangle(
                    pixels,
                    depth,
                    [projected[indices[i]] for i in triangle],
                    [face_colours[i] for i in triangle],
                )

        for start, end in edges:
            self._draw_edge(
                pixels,
                depth,
                projected[start],
                projected[end],
                self.EDGE_COLOUR,
            )

        output = Text()

        output.append(
            f"{self.RENDER_TITLE}\n",
            style=self.TITLE_STYLE,
        )
        output.append(
            f"{self.RENDER_SUBTITLE}\n",
            style=self.SUBTITLE_STYLE,
        )

        # Unicode Braille bit order is column-major for the first three rows,
        # with dots 7 and 8 occupying the bottom-left and bottom-right.
        braille_bits = (
            (0x01, 0x08),
            (0x02, 0x10),
            (0x04, 0x20),
            (0x40, 0x80),
        )
        style_cache = {}

        for row in range(canvas_rows):
            for column in range(width):
                samples = tuple(
                    pixels[row * 4 + dot_row][column * 2 + dot_column]
                    for dot_row in range(4)
                    for dot_column in range(2)
                )
                mask = sum(
                    braille_bits[dot_row][dot_column]
                    for dot_row in range(4)
                    for dot_column in range(2)
                    if pixels[row * 4 + dot_row][column * 2 + dot_column]
                )
                occupied = [sample for sample in samples if sample]

                if not occupied:
                    output.append(" ")
                    continue

                colour = tuple(
                    sum(sample[channel] for sample in occupied) // len(occupied)
                    for channel in range(3)
                )
                # Quantisation keeps Rich's style table compact at animation speed.
                colour = tuple(min(255, (channel // 12) * 12) for channel in colour)
                style = style_cache.setdefault(
                    colour,
                    Style(color=Color.from_rgb(*colour)),
                )
                output.append(chr(0x2800 + mask), style=style)
            output.append("\n")

        return output


class Icosahedron3D(Cube3D):
    """Rotating regular icosahedron with 20 triangular faces."""

    RENDER_TITLE = "3D HOLOGRAPHIC ICOSAHEDRON"
    RENDER_SUBTITLE = "20 faces • 30 edges • 2×4 subpixels"
    FACE_COLOUR_A = (0, 72, 18)
    FACE_COLOUR_B = (0, 255, 70)
    EDGE_COLOUR = (140, 255, 165)
    TITLE_STYLE = "bold bright_green"
    SUBTITLE_STYLE = "dim green"

    DEFAULT_CSS = """
    Icosahedron3D {
        height: 1fr;
        border: round green;
        padding: 1;
    }
    """

    def mesh(self):
        golden_ratio = (1 + math.sqrt(5)) / 2
        vertices = [
            (-1,  golden_ratio, 0), (1,  golden_ratio, 0),
            (-1, -golden_ratio, 0), (1, -golden_ratio, 0),
            (0, -1,  golden_ratio), (0, 1,  golden_ratio),
            (0, -1, -golden_ratio), (0, 1, -golden_ratio),
            ( golden_ratio, 0, -1), ( golden_ratio, 0, 1),
            (-golden_ratio, 0, -1), (-golden_ratio, 0, 1),
        ]
        faces = [
            (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
            (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
            (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
            (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
        ]
        return vertices, faces


# ============================================================
# 1960s-STYLE WIREFRAME
# ============================================================

class LunarLanderWireframe(Cube3D):
    """Line-only lunar module rendered as single-dot Braille vectors."""

    DEFAULT_CSS = """
    LunarLanderWireframe {
        height: 1fr;
        border: round green;
        padding: 1;
    }
    """

    def __init__(self):
        super().__init__()
        self.angle_x = -0.12
        self.angle_y = -0.5
        self.angle_z = 0.0
        self.vertices, self.edges = self._build_lunar_module()

    def animate(self):
        # A slow turntable motion keeps the drawing legible and period-appropriate.
        self.angle_y += 0.009
        self.refresh()

    def project(self, point, width, height):
        x, y, z = point
        z += 6.5
        scale = min(width, height) * 1.05
        return width / 2 + (x / z) * scale, height / 2 - (y / z) * scale, z

    @staticmethod
    def _build_lunar_module():
        vertices = []
        edges = []

        def vertex(x, y, z):
            vertices.append((x, y, z))
            return len(vertices) - 1

        def edge(start, end):
            edges.append((start, end))

        def ring(y, radius, sides=8, phase=math.pi / 8):
            return [
                vertex(
                    math.cos(phase + index * math.tau / sides) * radius,
                    y,
                    math.sin(phase + index * math.tau / sides) * radius,
                )
                for index in range(sides)
            ]

        def close(points):
            for index, point in enumerate(points):
                edge(point, points[(index + 1) % len(points)])

        def join(first, second, diagonals=False):
            for index, point in enumerate(first):
                edge(point, second[index])
                if diagonals:
                    edge(point, second[(index + 1) % len(second)])

        # Octagonal descent stage and faceted ascent cabin.
        descent_bottom = ring(-0.72, 1.03)
        descent_top = ring(0.02, 1.16)
        cabin_bottom = ring(0.02, 0.78)
        cabin_shoulder = ring(0.86, 0.82)
        cabin_top = ring(1.38, 0.47)
        for points in (
            descent_bottom,
            descent_top,
            cabin_bottom,
            cabin_shoulder,
            cabin_top,
        ):
            close(points)
        join(descent_bottom, descent_top, diagonals=True)
        join(cabin_bottom, cabin_shoulder, diagonals=True)
        join(cabin_shoulder, cabin_top, diagonals=True)
        roof = vertex(0, 1.58, 0)
        for point in cabin_top:
            edge(point, roof)

        # Four articulated legs, paired braces, and octagonal landing pads.
        leg_angles = (
            math.pi / 4,
            math.pi * 3 / 4,
            math.pi * 5 / 4,
            math.pi * 7 / 4,
        )
        for leg_index, angle in enumerate(leg_angles):
            hip = descent_top[(leg_index * 2) % 8]
            knee = vertex(
                math.cos(angle) * 1.62,
                -0.72,
                math.sin(angle) * 1.62,
            )
            foot_x = math.cos(angle) * 2.05
            foot_z = math.sin(angle) * 2.05
            foot = vertex(foot_x, -1.34, foot_z)
            edge(hip, knee)
            edge(knee, foot)
            edge(descent_bottom[(leg_index * 2 + 1) % 8], knee)
            edge(descent_top[(leg_index * 2 + 7) % 8], knee)
            pad = [
                vertex(
                    foot_x + math.cos(index * math.pi / 4) * 0.28,
                    -1.36,
                    foot_z + math.sin(index * math.pi / 4) * 0.28,
                )
                for index in range(8)
            ]
            close(pad)
            edge(foot, pad[0])
            edge(foot, pad[4])

        # Forward ladder.
        ladder_left = []
        ladder_right = []
        for rung in range(7):
            y = 0.05 - rung * 0.19
            ladder_left.append(vertex(-0.17, y, 1.12 + rung * 0.055))
            ladder_right.append(vertex(0.17, y, 1.12 + rung * 0.055))
            edge(ladder_left[-1], ladder_right[-1])
            if rung:
                edge(ladder_left[-2], ladder_left[-1])
                edge(ladder_right[-2], ladder_right[-1])
        edge(cabin_shoulder[1], ladder_left[0])
        edge(cabin_shoulder[2], ladder_right[0])

        # Rendezvous radar: shallow concentric dish, spokes, receiver, and mast.
        mast_base = vertex(0.25, 1.48, -0.2)
        mast_top = vertex(0.25, 2.08, -0.2)
        edge(roof, mast_base)
        edge(mast_base, mast_top)
        dish_rings = []
        for ring_index, radius in enumerate((0.14, 0.30, 0.48)):
            points = [
                vertex(
                    0.25 + math.cos(index * math.pi / 6) * radius,
                    2.13 - ring_index * 0.07,
                    -0.2 + math.sin(index * math.pi / 6) * radius,
                )
                for index in range(12)
            ]
            close(points)
            dish_rings.append(points)
        for index in range(0, 12, 2):
            edge(dish_rings[0][index], dish_rings[1][index])
            edge(dish_rings[1][index], dish_rings[2][index])
        receiver = vertex(0.25, 2.41, -0.2)
        edge(mast_top, receiver)
        for index in (0, 3, 6, 9):
            edge(receiver, dish_rings[2][index])

        # Four reaction-control cages around the upper stage.
        for angle in (0, math.pi / 2, math.pi, math.pi * 3 / 2):
            tangent_x = -math.sin(angle) * 0.14
            tangent_z = math.cos(angle) * 0.14
            centre_x = math.cos(angle) * 1.02
            centre_z = math.sin(angle) * 1.02
            cage = (
                vertex(centre_x + tangent_x, 0.54, centre_z + tangent_z),
                vertex(centre_x - tangent_x, 0.54, centre_z - tangent_z),
                vertex(centre_x - tangent_x, 0.91, centre_z - tangent_z),
                vertex(centre_x + tangent_x, 0.91, centre_z + tangent_z),
            )
            close(cage)
            edge(cage[0], cage[2])
            edge(cage[1], cage[3])

        return vertices, edges

    @staticmethod
    def _draw_vector(pixels, start, end):
        """Rasterize one mathematical edge as a one-subpixel-wide line."""

        x0, y0, _ = start
        x1, y1, _ = end
        steps = max(1, math.ceil(max(abs(x1 - x0), abs(y1 - y0))))
        height = len(pixels)
        width = len(pixels[0])
        for step in range(steps + 1):
            amount = step / steps
            x = round(x0 + (x1 - x0) * amount)
            y = round(y0 + (y1 - y0) * amount)
            if 0 <= x < width and 0 <= y < height:
                pixels[y][x] = True

    def render(self):
        width = max(self.size.width - 4, 20)
        height = max(self.size.height - 4, 10)
        canvas_rows = max(1, height - 2)
        pixel_width = width * 2
        pixel_height = canvas_rows * 4
        pixels = [[False for _ in range(pixel_width)] for _ in range(pixel_height)]

        projected = [
            self.project(self.rotate_point(point), pixel_width, pixel_height)
            for point in self.vertices
        ]
        for start, end in self.edges:
            self._draw_vector(pixels, projected[start], projected[end])

        output = Text()
        output.append("LUNAR EXCURSION MODULE / VECTOR STUDY\n", style="bold green")
        output.append(
            f"{len(self.vertices)} points • {len(self.edges)} edges • line display\n",
            style="dim green",
        )
        braille_bits = (
            (0x01, 0x08),
            (0x02, 0x10),
            (0x04, 0x20),
            (0x40, 0x80),
        )
        line_style = Style(color=Color.from_rgb(70, 255, 115))
        for row in range(canvas_rows):
            for column in range(width):
                mask = sum(
                    braille_bits[dot_row][dot_column]
                    for dot_row in range(4)
                    for dot_column in range(2)
                    if pixels[row * 4 + dot_row][column * 2 + dot_column]
                )
                output.append(chr(0x2800 + mask) if mask else " ", style=line_style)
            output.append("\n")
        return output


# ============================================================
# MAIN APPLICATION
# ============================================================

class ButlerUI(App):

    TITLE = "Monkey Butler"

    CSS = """

    Screen {
        background: #101010;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        padding: 1;
    }

    #sidebar {
        width: 26;
        border: round green;
        padding: 1;
    }

    #main {
        width: 1fr;
        border: round cyan;
        padding: 1 2;
    }

    .section-title {
        margin-top: 1;
        text-style: bold;
        color: cyan;
    }

    Button {
        width: 100%;
        margin-top: 1;
    }

    #system-status {
        color: green;
        text-style: bold;
    }

    #command {
        margin-top: 2;
    }

    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("1", "dashboard", "Dashboard"),
        ("2", "graphics2d", "2D"),
        ("3", "graphics3d", "3D"),
        ("4", "icosahedron", "Icosahedron"),
        ("5", "wireframe", "Wireframe"),
    ]

    def compose(self) -> ComposeResult:

        yield Header()

        with TabbedContent(
            initial="dashboard",
            id="tabs",
        ):

            # =================================================
            # TAB 1 — Dashboard
            # =================================================

            with TabPane(
                "Dashboard",
                id="dashboard",
            ):

                with Horizontal():

                    with Vertical(id="sidebar"):

                        yield Static(
                            "MONKEY BUTLER",
                            classes="section-title",
                        )

                        yield Button(
                            "Lights",
                            id="lights",
                        )

                        yield Button(
                            "Pumps",
                            id="pumps",
                        )

                        yield Button(
                            "Cameras",
                            id="cameras",
                        )

                        yield Static(
                            "\nHOUSE"
                            "\nLiving Room    ● ON"
                            "\nKitchen        ● ON"
                            "\nBedroom        ○ OFF"
                        )

                    with Vertical(id="main"):

                        yield Static(
                            "SYSTEM ONLINE",
                            id="system-status",
                        )

                        yield Static(
                            "\nAI ENGINE"
                            "\nModel: Qwen"
                            "\nGPU: RTX"
                            "\nContext: 18%"
                        )

                        yield Static(
                            "\nDEVICES"
                            "\nPump 1       OFF"
                            "\nPump 2       ON"
                            "\nPump 3       OFF"
                        )

                        yield Static(
                            "\n> Waiting for command...",
                            id="command",
                        )

            # =================================================
            # TAB 2 — 2D
            # =================================================

            with TabPane(
                "2D Graphics",
                id="graphics2d",
            ):

                yield Animation2D()

            # =================================================
            # TAB 3 — 3D
            # =================================================

            with TabPane(
                "3D Graphics",
                id="graphics3d",
            ):

                yield Cube3D()

            # =================================================
            # TAB 4 — Icosahedron
            # =================================================

            with TabPane(
                "Icosahedron",
                id="icosahedron",
            ):

                yield Icosahedron3D()

            # =================================================
            # TAB 5 — Wireframe
            # =================================================

            with TabPane(
                "Wireframe",
                id="wireframe",
            ):

                yield LunarLanderWireframe()

        yield Footer()

    # --------------------------------------------------------
    # Keyboard shortcuts
    # --------------------------------------------------------

    def action_dashboard(self):
        self.query_one("#tabs", TabbedContent).active = (
            "dashboard"
        )

    def action_graphics2d(self):
        self.query_one("#tabs", TabbedContent).active = (
            "graphics2d"
        )

    def action_graphics3d(self):
        self.query_one("#tabs", TabbedContent).active = (
            "graphics3d"
        )

    def action_icosahedron(self):
        self.query_one("#tabs", TabbedContent).active = (
            "icosahedron"
        )

    def action_wireframe(self):
        self.query_one("#tabs", TabbedContent).active = (
            "wireframe"
        )


if __name__ == "__main__":
    ButlerUI().run()
