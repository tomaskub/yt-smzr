"""Minimal Textual app. The processing workflow is not implemented yet."""

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static


class SummarizerApp(App[None]):
    """Welcome screen for the local application bootstrap."""

    TITLE = "yt-smzr"
    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "YouTube Video Summarizer\n\n"
            "The app is installed. Video processing is not implemented yet.\n"
            "Press q to quit.",
            id="welcome",
        )
        yield Footer()


def run() -> None:
    """Launch the terminal UI without initializing processing providers."""
    SummarizerApp().run()
