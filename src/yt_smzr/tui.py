"""Single-screen frontend for the synchronous, reusable video pipeline."""

from collections.abc import Callable
from typing import Protocol

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from yt_smzr.config import ENV_FIELDS, ConfigurationError, Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import (
    Pipeline,
    PipelineError,
    PipelineEvent,
    PipelineResult,
    PipelineStage,
    PreparedVideo,
)
from yt_smzr.settings_screen import SettingsScreen
from yt_smzr.storage.export import summary_markdown, timestamp, transcript_markdown


class Workflow(Protocol):
    def prepare(self, url: str, *, force: bool = False) -> PreparedVideo: ...

    def process(
        self, prepared: PreparedVideo, *, confirmed: bool
    ) -> PipelineResult: ...


PipelineFactory = Callable[[Callable[[PipelineEvent], None]], Workflow]


def _metadata(metadata: VideoMetadata) -> str:
    return (
        f"Title: {metadata.title}\nChannel: {metadata.channel}\n"
        f"Duration: {timestamp(metadata.duration_seconds)}\n"
        f"Video ID: {metadata.video_id}\nURL: {metadata.original_url}"
    )


class HelpScreen(ModalScreen[None]):
    """A scrollable key map with a predictable return to the invoking pane."""

    BINDINGS = [("escape", "dismiss", "Close"), ("?", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Static(
                "KEY MAP\n\n"
                "Tab / Shift+Tab   Move focus\n"
                "Enter             Fetch URL / activate focused confirmation\n"
                "Escape            Cancel pending confirmation / close dialog\n"
                "u                 Focus URL\n"
                "f                 Toggle force refresh before preparation\n"
                "m                 Show/hide metadata\n"
                "s                 Provider/model settings\n"
                "1 / 2 / 3         Summary / transcript / output paths\n"
                "Arrows / j / k    Navigate or scroll outside inputs\n"
                "?                 This help\n"
                "Ctrl+Q            Quit\n\n"
                "Shortcuts are inactive while editing text.\n"
                "Escape does not stop pipeline work.\n\n"
                "Escape to return",
                markup=False,
            )


class SummarizerApp(App[None]):
    """Confirm metadata before processing, with one active workflow at a time."""

    TITLE = "yt-smzr"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("u", "url", "URL", show=False),
        Binding("f", "force", "Force", show=False),
        Binding("m", "metadata", "Metadata", show=False),
        Binding("s", "settings", "Settings", show=False),
        Binding("?", "help", "Help", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("1", "view('summary-tab')", "Summary", show=False),
        Binding("2", "view('transcript-tab')", "Transcript", show=False),
        Binding("3", "view('paths-tab')", "Paths", show=False),
        Binding("j", "scroll_result(1)", "Down", show=False),
        Binding("k", "scroll_result(-1)", "Up", show=False),
    ]
    CSS = """
    Screen { background: $surface; color: $text; }
    #entry { height: 3; }
    Input { border: solid $foreground 45%; height: 3; padding: 0 1; }
    Input:focus { border: solid $foreground; }
    Button { border: none; height: 1; min-width: 8;
             padding: 0 1; background: $surface; }
    Button:focus { text-style: bold reverse; }
    Button:hover { background: $panel; }
    #entry Input { width: 1fr; }
    #entry Button { width: 9; margin-top: 1; }
    #entry Checkbox { width: 20; height: 1; margin-top: 1; border: none; padding: 0; }
    Checkbox:focus { text-style: reverse; }
    #provider-status { height: 1; padding: 0 1; }
    #stage { height: 2; padding: 0 1; }
    #video-metadata { height: 4; border: solid $foreground 45%; }
    #video-metadata:focus { border: solid $foreground; }
    #metadata { height: auto; }
    #confirmation { height: 1; }
    #confirm { width: 25; }
    #cancel { width: 12; }
    #events { height: 3; border: solid $foreground 45%; }
    TabbedContent { height: 1fr; min-height: 4; }
    TabPane { padding: 0; }
    Tabs { height: 1; }
    Tab { padding: 0 1; height: 1; }
    Underline { display: none; }
    .result-pane { border: solid $foreground 45%; }
    .result-pane:focus { border: solid $foreground; }
    .result { height: auto; padding: 0 1; }
    #hints { height: 1; padding: 0 1; text-style: bold; }
    HelpScreen { align: center middle; background: $background 80%; }
    .dialog { width: 72; max-width: 100%; height: auto; max-height: 100%;
              border: solid $foreground; padding: 1 2; background: $surface; }
    """

    def __init__(self, *, pipeline_factory: PipelineFactory | None = None) -> None:
        super().__init__()
        self._session: dict[str, object] = {}
        self._pipeline_factory: PipelineFactory = (
            pipeline_factory or self._configured_pipeline
        )
        self._pipeline: Workflow | None = None
        self._prepared: PreparedVideo | None = None
        self._busy = False
        self._show_metadata = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="entry"):
            yield Input(placeholder="Paste a YouTube video URL", id="url")
            yield Button("Fetch", id="prepare")
            yield Checkbox("Force refresh", id="force")
        yield Static("", id="provider-status", markup=False)
        yield Static("Ready. Enter a URL to fetch metadata.", id="stage", markup=False)
        with VerticalScroll(id="video-metadata"):
            yield Static(
                "Video metadata will appear here.", id="metadata", markup=False
            )
        with Horizontal(id="confirmation"):
            yield Button("Process", id="confirm")
            yield Button("Cancel", id="cancel")
        yield RichLog(id="events", max_lines=100, wrap=True, markup=False)
        with TabbedContent():
            with TabPane("Summary", id="summary-tab"):
                with VerticalScroll(classes="result-pane"):
                    yield Static(
                        "No summary yet.", id="summary", classes="result", markup=False
                    )
            with TabPane("Transcript", id="transcript-tab"):
                with VerticalScroll(classes="result-pane"):
                    yield Static(
                        "No transcript yet.",
                        id="transcript",
                        classes="result",
                        markup=False,
                    )
            with TabPane("Output paths", id="paths-tab"):
                with VerticalScroll(classes="result-pane"):
                    yield Static(
                        "No outputs yet.", id="paths", classes="result", markup=False
                    )
        yield Static("", id="hints", markup=False)

    def on_mount(self) -> None:
        self.query_one("#confirmation").display = False
        self.query_one("#url", Input).focus()
        self.query_one("#video-metadata").border_title = "Video"
        self.query_one("#events").border_title = "Events"
        for pane in self.query(".result-pane"):
            pane.border_title = "Result"
        self.refresh_provider_status()
        self._controls()

    def _configured_pipeline(self, event: Callable[[PipelineEvent], None]) -> Pipeline:
        return Pipeline(self._effective_settings(), on_event=event)

    def _effective_settings(self) -> Settings:
        return Settings.from_env(session=self._session)

    def refresh_provider_status(self) -> None:
        try:
            settings = self._effective_settings()
            label = (
                f"{settings.summarization_provider} / {settings.summarization_model}"
            )
        except ConfigurationError:
            label = "Invalid configuration"
        self.query_one("#provider-status", Static).update(label)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, ModalScreen) and action in {
            "metadata",
            "url",
            "force",
            "settings",
            "help",
            "cancel",
            "view",
        }:
            return False
        if action in {
            "metadata",
            "url",
            "force",
            "settings",
            "help",
            "cancel",
            "view",
            "scroll_result",
        } and isinstance(self.focused, Input):
            return False
        return True

    def action_url(self) -> None:
        if not self._busy and self._prepared is None:
            self.query_one("#url", Input).focus()

    def action_metadata(self) -> None:
        if self._prepared is None:
            self._show_metadata = not self._show_metadata
            self._controls()
            if self._show_metadata:
                self.query_one("#video-metadata").focus()

    def action_force(self) -> None:
        if not self._busy and self._prepared is None:
            checkbox = self.query_one("#force", Checkbox)
            checkbox.value = not checkbox.value

    def action_settings(self) -> None:
        """Edit next-run selections without changing prepared/active workflows."""
        if self._busy or self._prepared is not None:
            return
        focused = self.focused
        try:
            settings = self._effective_settings()
        except ConfigurationError as exc:
            self.notify(str(exc), severity="error")
            return

        def applied(selection: Settings | None) -> None:
            if selection is not None:
                self._session = {name: getattr(selection, name) for name in ENV_FIELDS}
                self.refresh_provider_status()
                self.notify("Settings applied to subsequent runs.")
            if focused is not None:
                focused.focus()

        self.push_screen(SettingsScreen(settings), applied)

    def action_help(self) -> None:
        focused = self.focused

        def restore(_: None) -> None:
            if focused is not None:
                focused.focus()

        self.push_screen(HelpScreen(), restore)

    def action_cancel(self) -> None:
        self.cancel_requested()

    def action_view(self, pane: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = pane
        self.query_one(f"#{pane} .result-pane").focus()

    def action_scroll_result(self, direction: int) -> None:
        focused = self.focused
        if isinstance(focused, (VerticalScroll, RichLog)):
            if direction > 0:
                focused.scroll_down()
            else:
                focused.scroll_up()

    def on_descendant_focus(self) -> None:
        self._update_hints()
        for pane in self.query("VerticalScroll"):
            if "dialog" not in pane.classes:
                pane.border_title = (
                    "> Focus"
                    if pane.has_focus
                    else ("Video" if pane.id == "video-metadata" else "Result")
                )

    def _controls(self) -> None:
        locked = self._busy or self._prepared is not None
        for selector in ("#url", "#force", "#prepare"):
            self.query_one(selector).disabled = locked
        for selector in ("#confirm", "#cancel"):
            self.query_one(selector, Button).disabled = self._busy
        self.query_one("#confirmation").display = self._prepared is not None
        self.query_one("#video-metadata").display = (
            self._prepared is not None or self._show_metadata
        )
        self._update_hints()

    def _update_hints(self) -> None:
        hints = "Tab focus  1/2/3 views  ? help  Ctrl+Q quit"
        if self._busy:
            hints = "Working | " + hints
        elif self._prepared is not None:
            hints = "Enter on Process  Esc cancel | " + hints
        elif isinstance(self.focused, Input):
            hints = "Enter fetch  Tab leave input  Ctrl+Q quit"
        else:
            hints = (
                "u URL  f force  s settings  m video | 1/2/3 views  ? help  Ctrl+Q quit"
            )
        self.query_one("#hints", Static).update(hints)

    @on(Input.Submitted, "#url")
    @on(Button.Pressed, "#prepare")
    def prepare_requested(self) -> None:
        if self._busy or self._prepared is not None:
            return
        url = self.query_one("#url", Input).value.strip()
        if not url:
            self.query_one("#stage", Static).update("Enter a YouTube video URL.")
            return
        self._busy = True
        self._controls()
        self.query_one("#events", RichLog).clear()
        self.query_one("#stage", Static).update("Preparing metadata...")
        self._prepare(url, self.query_one("#force", Checkbox).value)

    def _on_event(self, event: PipelineEvent) -> None:
        self._ui(self._render_event, event)

    def _ui(self, callback: Callable[..., None], *args: object) -> None:
        # Thread workers may finish after the user has closed the app.
        if self.is_running:
            try:
                self.call_from_thread(callback, *args)
            except RuntimeError:
                if self.is_running:
                    raise

    def _render_event(self, event: PipelineEvent) -> None:
        stage = event.stage.value.replace("_", "/")
        self.query_one("#stage", Static).update(f"{stage}: {event.message}")
        self.query_one("#events", RichLog).write(f"{stage}: {event.message}")

    @work(thread=True, exit_on_error=False)
    def _prepare(self, url: str, force: bool) -> None:
        try:
            pipeline = self._pipeline_factory(self._on_event)
            prepared = pipeline.prepare(url, force=force)
        except ConfigurationError as error:
            self._ui(self._failed, PipelineStage.PREFLIGHT, str(error))
        except PipelineError as error:
            self._ui(self._failed, error.stage, str(error))
        except Exception:
            self._ui(self._failed, PipelineStage.PREFLIGHT, "Could not prepare video.")
        else:
            self._ui(self._ready_to_confirm, pipeline, prepared)

    def _ready_to_confirm(self, pipeline: Workflow, prepared: PreparedVideo) -> None:
        self._pipeline = pipeline
        self._prepared = prepared
        self._busy = False
        self.query_one("#metadata", Static).update(_metadata(prepared.metadata))
        cache = " Metadata reused from cache." if prepared.metadata_cached else ""
        refresh = " Force refresh selected." if prepared.force else ""
        self.query_one("#stage", Static).update(
            f"confirmation: Check the metadata, then confirm or cancel.{cache}{refresh}"
        )
        self._controls()
        self.query_one("#confirm", Button).focus()

    @on(Button.Pressed, "#cancel")
    def cancel_requested(self) -> None:
        if self._busy or self._prepared is None:
            return
        self._prepared = None
        self._pipeline = None
        self._render_event(
            PipelineEvent(PipelineStage.CONFIRMATION, "Cancelled before processing.")
        )
        self._controls()
        self.query_one("#url", Input).focus()

    @on(Button.Pressed, "#confirm")
    def confirm_requested(self) -> None:
        if self._busy or self._prepared is None or self._pipeline is None:
            return
        self._busy = True
        self._controls()
        self._process(self._pipeline, self._prepared)

    @work(thread=True, exit_on_error=False)
    def _process(self, pipeline: Workflow, prepared: PreparedVideo) -> None:
        try:
            result = pipeline.process(prepared, confirmed=True)
        except PipelineError as error:
            self._ui(self._failed, error.stage, str(error))
        except Exception:
            self._ui(self._failed, PipelineStage.FAILURE, "Could not process video.")
        else:
            self._ui(self._completed, result)

    def _reset(self) -> None:
        self._busy = False
        self._prepared = None
        self._pipeline = None
        self._controls()

    def _failed(self, stage: PipelineStage, message: str) -> None:
        self._render_event(PipelineEvent(stage, f"Failed: {message}"))
        self._reset()
        self.query_one("#url", Input).focus()

    def _completed(self, result: PipelineResult) -> None:
        self.query_one("#metadata", Static).update(_metadata(result.metadata))
        summary = summary_markdown(result.summary)
        if not result.summary.notable_quotes:
            summary += "\n## Notable quotes\n\nNo grounded quotes selected.\n"
        self.query_one("#summary", Static).update(summary)
        self.query_one("#transcript", Static).update(
            transcript_markdown(result.transcript)
        )
        paths = result.paths
        self.query_one("#paths", Static).update(
            f"Metadata JSON: {paths.metadata}\nAudio: {result.audio_path}\n"
            f"Transcript Markdown: {paths.transcript}\n"
            f"Transcript JSON: {paths.transcript_json}\n"
            f"Summary Markdown: {paths.summary}\nSummary JSON: {paths.summary_json}"
        )
        reused = ", ".join(stage.value for stage in result.reused_stages) or "none"
        self.query_one("#stage", Static).update(
            f"completion: Outputs saved. Reused stages: {reused}."
        )
        self._reset()
        self.action_view("summary-tab")


def run() -> None:
    """Launch without contacting providers until the user submits a URL."""
    SummarizerApp().run()
