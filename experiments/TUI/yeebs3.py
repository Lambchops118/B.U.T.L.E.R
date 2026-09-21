"""Standalone Textual recreation of the Pygame Monkey Butler InfoPanel.

This module intentionally contains demo data only.  It mirrors the visual
composition from ``InfoPanel/screen.py`` without importing its Pygame,
networking, queue, or home-automation dependencies.
"""

from datetime import datetime

from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Static


PHOSPHOR = "#00ff64"
PHOSPHOR_DIM = "#087532"
PHOSPHOR_DARK = "#032d15"


class FramedText(Static):
    """A phosphor-green InfoPanel window with a border label."""

    def __init__(self, content: str, title: str, **kwargs):
        super().__init__(content, **kwargs)
        self._panel_title = title

    def on_mount(self) -> None:
        self.border_title = self._panel_title


class ButlerPortrait(Widget):
    """Animated terminal interpretation of the original vector portrait."""

    DEFAULT_CSS = """
    ButlerPortrait {
        height: 7fr;
        border: solid #00aa44;
        border-title-color: #00ff64;
        padding: 0 1;
        content-align: center middle;
    }
    """

    ART = (
        "       ▄▄▄▄▄       ",
        "    ▄██▀   ▀██▄    ",
        "  ▄█ ╭◉╮───╭◉╮ █▄  ",
        " █   ╰─╯   ╰─╯   █ ",
        " █      ╲▄╱      █ ",
        " █     ╭███╮     █ ",
        "  █▄   ╰─┬─╯   ▄█  ",
        "   ▀█▄   ┴   ▄█▀   ",
        "     ▀██▄▄▄██▀     ",
        "        ╱ ╲        ",
    )

    COMPACT_ART = (
        "  ▄▄▄▄▄  ",
        "▄█▀   ▀█▄",
        "█ ◉───◉ █",
        "█   ▄   █",
        "█  ╰┬╯  █",
        "▀█▄▄▄▄▄█▀",
        "   ╱ ╲   ",
    )

    def __init__(self) -> None:
        super().__init__()
        self.scanline = 0

    def on_mount(self) -> None:
        self.border_title = "MB // VECTOR PORTRAIT"
        self.set_interval(0.11, self.animate)

    def animate(self) -> None:
        self.scanline = (self.scanline + 1) % (len(self.ART) + 5)
        self.refresh()

    def render(self) -> Text:
        art = self.COMPACT_ART if self.size.width < 20 else self.ART
        available = max(1, self.size.height)
        start = max(0, (available - len(art)) // 2)
        output = Text("\n" * start)

        for index, line in enumerate(art):
            distance = abs(index - self.scanline)
            style = "bold bright_green" if distance == 0 else PHOSPHOR
            if distance == 1:
                style = "#70ff9c"
            output.append(line, style=style)
            output.append("\n")

        output.justify = "center"
        return output


class ClockPanel(Widget):
    """Clock and date corresponding to the center readout in screen.py."""

    DEFAULT_CSS = """
    ClockPanel {
        height: 3fr;
        color: #00ff64;
        content-align: center middle;
        padding: 1 0;
    }
    """

    def on_mount(self) -> None:
        self.set_interval(1.0, self.tick)

    def tick(self) -> None:
        self.refresh()

    def render(self) -> Text:
        now = datetime.now().astimezone()
        hour = now.strftime("%I").lstrip("0") or "12"
        output = Text(justify="center")
        output.append(
            f"{now.strftime('%A').upper()}  {hour}:{now.strftime('%M %p')}\n",
            style="bold #00ff64",
        )
        output.append(now.strftime("%B %d, %Y").upper(), style="#00b94b")
        return output


class SignalPanel(Widget):
    """Small center-column instrumentation readout."""

    DEFAULT_CSS = """
    SignalPanel {
        height: 2fr;
        border-top: solid #064c23;
        color: #087532;
        content-align: center middle;
    }
    """

    def render(self) -> Text:
        text = Text(justify="center")
        text.append("CRT SIGNAL LOCKED\n", style="#00ff64")
        text.append("60.0 HZ  //  VECTOR BUS A", style="#087532")
        return text


class DynamoStatus(Widget):
    """Terminal version of the geared status indicators from windows.py."""

    DEFAULT_CSS = """
    DynamoStatus {
        height: 1fr;
        min-height: 3;
        padding: 0 1;
        border-bottom: solid #032d15;
        content-align: left middle;
    }
    """

    GEAR_FRAMES = (
        (" ▄ █ ▄ ", "█  ●  █", " ▀ █ ▀ "),
        ("▄  █  ▄", " █ ● █ ", "▀  █  ▀"),
        (" ▄ █ ▄ ", "█  ●  █", " ▀ █ ▀ "),
        ("▄ █   ▄", "  █●█  ", "▀   █ ▀"),
    )

    def __init__(self, label: str, online: bool) -> None:
        super().__init__()
        self.label = label
        self.online = online
        self.phase = 0

    def on_mount(self) -> None:
        self.set_interval(0.14, self.animate)

    def animate(self) -> None:
        if self.online:
            self.phase = (self.phase + 1) % len(self.GEAR_FRAMES)
            self.refresh()

    def render(self) -> Text:
        colour = PHOSPHOR if self.online else "#102d1b"
        status_colour = "bold #00ff64" if self.online else "bold #24432f"
        state = "ONLINE" if self.online else "OFFLINE"
        gear = self.GEAR_FRAMES[self.phase if self.online else 0]

        if self.size.height < 3:
            output = Text()
            output.append("⚙  " + self.label + "\n", style=colour)
            output.append("   └─ ", style=colour)
            output.append(f"[ {state} ]", style=status_colour)
            return output

        box_width = max(14, min(28, self.size.width - 11))
        label = self.label[: max(1, box_width - 3)]
        rule = "─" * box_width

        output = Text()
        output.append(gear[0] + "  ╭" + rule + "╮\n", style=colour)
        output.append(gear[1] + "  │ ", style=colour)
        output.append(label.ljust(box_width - 1), style=colour)
        output.append("│\n", style=colour)
        output.append(gear[2] + "  ╰─", style=colour)
        output.append(f"[ {state} ]".ljust(box_width - 1, "─"), style=status_colour)
        output.append("╯", style=colour)
        return output


class InfoPanelDemo(App):
    """Graphics-only recreation of the Monkey Butler InfoPanel."""

    TITLE = "Monkey Butler Information Panel"

    CSS = """
    Screen {
        background: #000400;
        color: #00ff64;
        layout: vertical;
    }

    #masthead {
        height: 4;
        border-bottom: double #00aa44;
        background: #001006;
        padding: 0 1;
    }

    #information-title {
        width: 40%;
        content-align: center middle;
        color: #00c950;
        text-style: bold;
    }

    #brand-title {
        width: 22%;
        content-align: center middle;
        color: #00ff64;
        text-style: bold;
    }

    #systems-title {
        width: 1fr;
        content-align: center middle;
        color: #00c950;
        text-style: bold;
    }

    #body {
        height: 1fr;
        padding: 1 1 0 1;
    }

    #left-column {
        width: 40%;
        padding-right: 1;
    }

    #center-column {
        width: 22%;
        padding-right: 1;
    }

    #right-column {
        width: 1fr;
    }

    FramedText {
        border: solid #00aa44;
        border-title-color: #00ff64;
        color: #00e85c;
        padding: 0 1;
        background: #000803;
    }

    #information {
        height: 5fr;
    }

    #voice-command {
        height: 3fr;
        min-height: 5;
        margin-top: 1;
        color: #60ff91;
    }

    #voice-response {
        height: 4fr;
        min-height: 6;
        margin-top: 1;
    }

    #footerline {
        height: 1;
        background: #001006;
        color: #087532;
        text-align: right;
        padding-right: 2;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("I N F O R M A T I O N", id="information-title")
            yield Static("MONKEY BUTLER", id="brand-title")
            yield Static("S Y S T E M S   S T A T U S", id="systems-title")

        with Horizontal(id="body"):
            with Vertical(id="left-column"):
                yield FramedText(
                    "BTC $64,231  |  ETH $3,487\n"
                    "SOL $148.72\n\n"
                    "TEMP 72°F  |  FEELS 71°F\n"
                    "HUMIDITY 46%\n"
                    "WIND 7 MPH NW  |  CLEAR\n\n"
                    "UPTIME  19 DAYS\n"
                    "NET WORTH  $101,749",
                    "BASIC INFORMATION",
                    id="information",
                )
                yield FramedText(
                    '"BUTLER, WATER THE MONSTERA..."',
                    "VOICE INPUT",
                    id="voice-command",
                )
                yield FramedText(
                    "OF COURSE, SIR. I HAVE ACTIVATED THE PUMP FOR THE POT "
                    "WITH THE MONSTERA.",
                    "VOICE RESPONSE",
                    id="voice-response",
                )

            with Vertical(id="center-column"):
                yield ButlerPortrait()
                yield ClockPanel()
                yield SignalPanel()

            with Vertical(id="right-column"):
                yield DynamoStatus("MQTT BROKER", True)
                yield DynamoStatus("HOME AUTOMATION MCP", False)
                yield DynamoStatus("AUTO WATERER", False)
                yield DynamoStatus("DISCORD FRONTEND", True)
                yield DynamoStatus("ENGINEERING ASSIST", False)

        yield Static(
            "CHOPSCORP. LTD. © 1977   //   CRT-77 INFO TERMINAL   //   Q: QUIT",
            id="footerline",
        )


if __name__ == "__main__":
    InfoPanelDemo().run()
