"""Headless frontend checks with no downloads, models, or provider calls."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, get_ident

from textual.widgets import Button, Checkbox, Input, RichLog, Static

from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import (
    PipelineError,
    PipelineEvent,
    PipelineResult,
    PipelineStage,
    PreparedVideo,
)
from yt_smzr.storage.paths import artifact_paths
from yt_smzr.storage.sqlite import CacheRecord
from yt_smzr.summarization.base import Chapter, NotableClaim, NotableQuote, Summary
from yt_smzr.transcription.base import Transcript, TranscriptSegment
from yt_smzr.tui import SummarizerApp

URL = "https://youtu.be/dQw4w9WgXcQ"


def result(tmp_path: Path) -> PipelineResult:
    metadata = VideoMetadata("dQw4w9WgXcQ", URL, "A [bold]title[/bold]", "Channel", 60)
    paths = artifact_paths(tmp_path, metadata.video_id)
    return PipelineResult(
        metadata=metadata,
        transcript=Transcript(
            provider="fake",
            model="test",
            segments=(
                TranscriptSegment(start_seconds=0, end_seconds=2, text="Hello world."),
            ),
        ),
        summary=Summary(
            provider="fake",
            model="test",
            short_summary="Short notes.",
            detailed_summary="Detailed notes.",
            key_points=("One key point.",),
            chapters=(
                Chapter(
                    title="Introduction",
                    start_seconds=0,
                    end_seconds=60,
                    summary="Chapter notes.",
                ),
            ),
            notable_claims=(NotableClaim(claim="A claim.", timestamp_seconds=1),),
            notable_quotes=(NotableQuote(quote="Hello world.", timestamp_seconds=0),),
        ),
        paths=paths,
        audio_path=paths.audio("webm"),
        cache_record=CacheRecord(
            video_id=metadata.video_id,
            original_url=URL,
            title=metadata.title,
            channel=metadata.channel,
            duration_seconds=metadata.duration_seconds,
        ),
        reused_stages=(PipelineStage.DOWNLOAD,),
    )


class FakeWorkflow:
    def __init__(self, output: PipelineResult) -> None:
        self.output = output
        self.on_event: Callable[[PipelineEvent], None] = lambda _: None
        self.preparations: list[tuple[str, bool]] = []
        self.confirmations: list[bool] = []
        self.threads: list[int] = []
        self.started = Event()
        self.release = Event()
        self.release.set()
        self.failure: PipelineError | None = None

    def factory(self, on_event: Callable[[PipelineEvent], None]) -> "FakeWorkflow":
        self.on_event = on_event
        return self

    def prepare(self, url: str, *, force: bool = False) -> PreparedVideo:
        self.threads.append(get_ident())
        self.preparations.append((url, force))
        self.on_event(PipelineEvent(PipelineStage.METADATA, "Reading metadata."))
        return PreparedVideo(self.output.metadata, force=force)

    def process(
        self, prepared: PreparedVideo, *, confirmed: bool = False
    ) -> PipelineResult:
        self.threads.append(get_ident())
        self.confirmations.append(confirmed)
        self.on_event(
            PipelineEvent(PipelineStage.TRANSCRIPTION, "Transcribing speech.")
        )
        self.started.set()
        assert self.release.wait(5), "Test failed to release worker"
        if self.failure:
            raise self.failure
        return self.output


def text(app: SummarizerApp, selector: str) -> str:
    return str(app.query_one(selector, Static).content)


async def wait_workers(app: SummarizerApp) -> None:
    # Textual annotates this manager method with an unparameterized Worker.
    await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]


async def test_confirmation_force_and_complete_result(tmp_path: Path) -> None:
    workflow = FakeWorkflow(result(tmp_path))
    app = SummarizerApp(pipeline_factory=workflow.factory)
    async with app.run_test(size=(110, 40)) as pilot:
        assert not workflow.preparations
        await pilot.click("#force")
        app.query_one("#url", Input).value = f"  {URL}  "
        app.query_one("#url", Input).focus()
        await pilot.press("enter")
        await wait_workers(app)
        await pilot.pause()
        assert workflow.preparations == [(URL, True)]
        assert not workflow.confirmations
        assert "A [bold]title[/bold]" in text(app, "#metadata")
        assert "Channel" in text(app, "#metadata")
        assert "00:01:00.000" in text(app, "#metadata")
        assert "dQw4w9WgXcQ" in text(app, "#metadata")
        assert "confirmation" in text(app, "#stage")
        assert app.query_one("#force", Checkbox).disabled
        await pilot.click("#confirm")
        await wait_workers(app)
        await pilot.pause()
        assert workflow.confirmations == [True]
        assert all(thread != get_ident() for thread in workflow.threads)
        assert "completion" in text(app, "#stage")
        for value in (
            "Short notes.",
            "Detailed notes.",
            "One key point.",
            "Chapter notes.",
            "A claim.",
            "Hello world.",
        ):
            assert value in text(app, "#summary")
        assert "Hello world." in text(app, "#transcript")
        for path in (
            workflow.output.paths.metadata,
            workflow.output.audio_path,
            workflow.output.paths.transcript,
            workflow.output.paths.transcript_json,
            workflow.output.paths.summary,
            workflow.output.paths.summary_json,
        ):
            assert str(path) in text(app, "#paths")
        assert app.query_one("#events", RichLog).lines
        assert not app.query_one("#prepare", Button).disabled


async def test_empty_input_cancel_and_repeat(tmp_path: Path) -> None:
    workflow = FakeWorkflow(result(tmp_path))
    app = SummarizerApp(pipeline_factory=workflow.factory)
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.press("enter")
        assert not workflow.preparations
        assert "Enter a YouTube" in text(app, "#stage")
        app.query_one("#url", Input).value = URL
        await pilot.click("#prepare")
        await wait_workers(app)
        await pilot.pause()
        await pilot.click("#cancel")
        assert not workflow.confirmations
        assert not app.query_one("#url", Input).disabled
        await pilot.click("#prepare")
        await wait_workers(app)
        await pilot.pause()
        assert workflow.preparations == [(URL, False), (URL, False)]
        assert app.query_one("#confirmation").display


async def test_worker_progress_double_submission_failure_and_previous_result(
    tmp_path: Path,
) -> None:
    workflow = FakeWorkflow(result(tmp_path))
    app = SummarizerApp(pipeline_factory=workflow.factory)
    async with app.run_test(size=(100, 35)) as pilot:
        app.query_one("#url", Input).value = URL
        await pilot.press("enter")
        await wait_workers(app)
        await pilot.pause()
        await pilot.click("#confirm")
        await wait_workers(app)
        await pilot.pause()
        previous = text(app, "#summary")
        workflow.release.clear()
        workflow.started.clear()
        workflow.failure = PipelineError(
            PipelineStage.SUMMARIZATION, "Input limit exceeded."
        )
        await pilot.click("#prepare")
        await wait_workers(app)
        await pilot.pause()
        await pilot.click("#confirm")
        await pilot.pause()
        assert workflow.started.is_set()
        assert "transcription" in text(app, "#stage")
        assert app.query_one("#confirm", Button).disabled
        app.confirm_requested()
        app.prepare_requested()
        assert workflow.confirmations == [True, True]
        # UI input/event handling continues while the worker blocks.
        await pilot.press("tab")
        workflow.release.set()
        await wait_workers(app)
        await pilot.pause()
        assert "summarization" in text(app, "#stage")
        assert "Input limit exceeded" in text(app, "#stage")
        assert text(app, "#summary") == previous
        assert not app.query_one("#prepare", Button).disabled


async def test_prepare_failure_and_callback_after_quit(tmp_path: Path) -> None:
    workflow = FakeWorkflow(result(tmp_path))

    def fail_factory(callback: Callable[[PipelineEvent], None]) -> FakeWorkflow:
        workflow.on_event = callback
        raise PipelineError(PipelineStage.PREFLIGHT, "Missing ffmpeg.")

    app = SummarizerApp(pipeline_factory=fail_factory)
    async with app.run_test() as pilot:
        app.query_one("#url", Input).value = URL
        await pilot.press("enter")
        await wait_workers(app)
        await pilot.pause()
        assert "Missing ffmpeg" in text(app, "#stage")
        assert not app.query_one("#url", Input).disabled
        await pilot.press("ctrl+q")
        assert not app.is_running
    workflow.on_event(PipelineEvent(PipelineStage.COMPLETION, "Late callback."))


async def test_small_screen_long_metadata_and_quit_during_work(tmp_path: Path) -> None:
    output = result(tmp_path)
    output = replace(
        output,
        metadata=replace(
            output.metadata,
            title="Long video title " * 30,
            channel="Channel name " * 10,
        ),
    )
    workflow = FakeWorkflow(output)
    app = SummarizerApp(pipeline_factory=workflow.factory)
    async with app.run_test(size=(80, 24)) as pilot:
        app.query_one("#url", Input).value = URL
        await pilot.press("enter")
        await wait_workers(app)
        await pilot.pause()
        for selector in ("#confirm", "#cancel", "#stage", "#events"):
            widget = app.query_one(selector)
            assert widget.region.y >= 0
            assert widget.region.bottom <= 23
        assert app.query_one("#summary-tab").region.height >= 1
        workflow.release.clear()
        try:
            await pilot.click("#confirm")
            await pilot.pause()
            assert workflow.started.is_set()
            await pilot.press("ctrl+q")
            assert not app.is_running
        finally:
            workflow.release.set()
    # A stopped thread must not deliver events into a closed application.
    workflow.on_event(PipelineEvent(PipelineStage.COMPLETION, "Late completion."))
