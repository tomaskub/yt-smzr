"""Single-screen frontend for the synchronous, reusable video pipeline."""

from collections.abc import Callable
from typing import Protocol

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import (
    Pipeline,
    PipelineError,
    PipelineEvent,
    PipelineResult,
    PipelineStage,
    PreparedVideo,
)
from yt_smzr.storage.export import summary_markdown, timestamp, transcript_markdown


class Workflow(Protocol):
    def prepare(self, url: str, *, force: bool = False) -> PreparedVideo: ...

    def process(
        self, prepared: PreparedVideo, *, confirmed: bool
    ) -> PipelineResult: ...


PipelineFactory = Callable[[Callable[[PipelineEvent], None]], Workflow]


def _pipeline(on_event: Callable[[PipelineEvent], None]) -> Pipeline:
    return Pipeline(on_event=on_event)


def _metadata(metadata: VideoMetadata) -> str:
    return (
        f"Title: {metadata.title}\nChannel: {metadata.channel}\n"
        f"Duration: {timestamp(metadata.duration_seconds)}\n"
        f"Video ID: {metadata.video_id}\nURL: {metadata.original_url}"
    )


class SummarizerApp(App[None]):
    """Confirm metadata before processing, with one active workflow at a time."""

    TITLE = "yt-smzr"
    BINDINGS = [("ctrl+q", "quit", "Quit")]
    CSS = """
    #entry, #confirmation { height: auto; }
    #entry Input { width: 1fr; }
    #entry Checkbox { width: 21; }
    #entry Button { margin-left: 1; }
    #video-metadata { height: 5; }
    #stage { height: 2; padding: 0 1; }
    #metadata { height: auto; padding: 0 1; }
    #events { height: 4; border: solid $primary; }
    TabbedContent { height: 1fr; min-height: 5; }
    TabPane { padding: 0; }
    .result { height: auto; padding: 1; }
    """

    def __init__(self, *, pipeline_factory: PipelineFactory = _pipeline) -> None:
        super().__init__()
        self._pipeline_factory = pipeline_factory
        self._pipeline: Workflow | None = None
        self._prepared: PreparedVideo | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="entry"):
            yield Input(placeholder="Paste a YouTube video URL", id="url")
            yield Button("Fetch metadata", id="prepare", variant="primary")
            yield Checkbox("Force refresh", id="force")
        yield Static("Ready. Enter a URL to fetch metadata.", id="stage", markup=False)
        with VerticalScroll(id="video-metadata"):
            yield Static(
                "Video metadata will appear here.", id="metadata", markup=False
            )
        with Horizontal(id="confirmation"):
            yield Button("Confirm and process", id="confirm", variant="success")
            yield Button("Cancel", id="cancel")
        yield RichLog(id="events", max_lines=100, wrap=True, markup=False)
        with TabbedContent():
            with TabPane("Summary", id="summary-tab"):
                with VerticalScroll():
                    yield Static(
                        "No summary yet.", id="summary", classes="result", markup=False
                    )
            with TabPane("Transcript", id="transcript-tab"):
                with VerticalScroll():
                    yield Static(
                        "No transcript yet.",
                        id="transcript",
                        classes="result",
                        markup=False,
                    )
            with TabPane("Output paths", id="paths-tab"):
                with VerticalScroll():
                    yield Static(
                        "No outputs yet.", id="paths", classes="result", markup=False
                    )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#confirmation").display = False
        self.query_one("#url", Input).focus()

    def _controls(self) -> None:
        locked = self._busy or self._prepared is not None
        for selector in ("#url", "#force", "#prepare"):
            self.query_one(selector).disabled = locked
        for selector in ("#confirm", "#cancel"):
            self.query_one(selector, Button).disabled = self._busy
        self.query_one("#confirmation").display = self._prepared is not None

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
        summary = _metadata(result.metadata) + "\n\n" + summary_markdown(result.summary)
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


def run() -> None:
    """Launch without contacting providers until the user submits a URL."""
    SummarizerApp().run()
