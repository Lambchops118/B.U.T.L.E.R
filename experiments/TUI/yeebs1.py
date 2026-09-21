from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Button
from textual.containers import Horizontal, Vertical


class ButlerUI(App):

    CSS = """
    #sidebar {
        width: 25;
        border: solid green;
    }

    #main {
        border: solid cyan;
    }

    Button {
        width: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("MONKEY BUTLER")
                yield Button("Lights")
                yield Button("Pumps")
                yield Button("Cameras")

            with Vertical(id="main"):
                yield Static("SYSTEM ONLINE")
                yield Static("Waiting for command...")

        yield Footer()


ButlerUI().run()