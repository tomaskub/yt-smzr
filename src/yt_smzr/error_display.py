"""Compact event excerpts and keyboard-accessible failure details."""

from collections import deque

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static


class EventLog(RichLog):
    """Reflow retained events and show the beginning of the latest entry."""

    def __init__(self, *, id: str) -> None:
        super().__init__(id=id, min_width=1, wrap=True, markup=False, auto_scroll=False)
        self._events: deque[str] = deque(maxlen=100)

    def append_event(self, message: str) -> None:
        # Trailing newlines must not become the only visible row in compact mode.
        self._events.append(" ".join(message.split()))
        self._reflow()

    def clear_events(self) -> None:
        self._events.clear()
        self.clear()

    def on_resize(self, event: Resize) -> None:
        # RichLog retains rendered strips, not their source, and won't rewrap
        # them on later resizes. Wait for the new viewport before rewriting.
        self.call_after_refresh(self._reflow)

    def _reflow(self) -> None:
        self.clear()
        latest_start = 0
        width = max(1, self.scrollable_content_region.width)
        for message in self._events:
            latest_start = len(self.lines)
            self.write(message, width=width, scroll_end=False)
        self.scroll_to(x=0, y=latest_start, animate=False, immediate=True, force=True)


class FailureStatus(Static):
    """Keep the failure's beginning and detail shortcut inside two rows."""

    def __init__(self, content: str, *, id: str) -> None:
        super().__init__(content, id=id, markup=False)
        self._failure: tuple[str, str] | None = None

    def show_failure(self, stage: str, message: str) -> None:
        self._failure = (stage, message)
        self._render_failure()

    def clear_failure(self) -> None:
        self._failure = None

    def on_resize(self, event: Resize) -> None:
        self.call_after_refresh(self._render_failure)

    def _render_failure(self) -> None:
        if self._failure is None:
            return
        stage, message = self._failure
        excerpt = Text(f"{stage}: Failed: {' '.join(message.split())}")
        excerpt.truncate(max(1, self.content_size.width), overflow="ellipsis")
        self.update(f"{excerpt.plain}\nF2 full error")


class FailureScreen(ModalScreen[None]):
    """Full stage context and error text with visible scrolling instructions."""

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("f2", "dismiss", "Close"),
        ("j", "scroll_error(1)", "Down"),
        ("k", "scroll_error(-1)", "Up"),
    ]
    DEFAULT_CSS = """
    FailureScreen { align: center middle; background: $background 80%; }
    #error-dialog { width: 90; max-width: 100%; height: 80%;
                    border: solid $foreground; padding: 1 2; background: $surface; }
    #error-title { height: 2; text-style: bold; }
    #error-scroll { height: 1fr; }
    #error-message { height: auto; }
    #error-help { height: 2; }
    """

    def __init__(self, stage: str, message: str) -> None:
        super().__init__()
        self.stage = stage
        self.message = message.strip()

    def compose(self) -> ComposeResult:
        with Vertical(id="error-dialog"):
            yield Static(f"Failure during {self.stage}", id="error-title", markup=False)
            with VerticalScroll(id="error-scroll"):
                yield Static(self.message, id="error-message", markup=False)
            yield Static(
                "Arrows / j / k / PgUp / PgDn scroll\nEsc or F2 returns",
                id="error-help",
                markup=False,
            )

    def on_mount(self) -> None:
        self.query_one("#error-scroll", VerticalScroll).focus()

    def action_scroll_error(self, direction: int) -> None:
        pane = self.query_one("#error-scroll", VerticalScroll)
        if direction > 0:
            pane.scroll_down()
        else:
            pane.scroll_up()
