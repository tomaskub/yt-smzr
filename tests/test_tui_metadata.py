"""Confirmation metadata stays visible or has a keyboard scrolling hint."""

from collections.abc import Callable
from dataclasses import replace
from xml.etree import ElementTree

from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Static, TabbedContent

from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import (
    PipelineError,
    PipelineEvent,
    PipelineResult,
    PipelineStage,
    PreparedVideo,
)
from yt_smzr.tui import SummarizerApp

URL = "https://www.youtube.com/watch?v=_5p1_TNSWqQ"


class ConfirmationWorkflow:
    def __init__(self) -> None:
        self.metadata = VideoMetadata(
            "_5p1_TNSWqQ", URL, "A video worth checking", "A channel", 345
        )
        self.confirmations: list[bool] = []

    def factory(
        self, callback: Callable[[PipelineEvent], None]
    ) -> "ConfirmationWorkflow":
        return self

    def prepare(self, url: str, *, force: bool = False) -> PreparedVideo:
        return PreparedVideo(self.metadata, force=force)

    def process(self, prepared: PreparedVideo, *, confirmed: bool) -> PipelineResult:
        self.confirmations.append(confirmed)
        raise PipelineError(PipelineStage.DOWNLOAD, "Test stops after confirmation.")


def screen_text(app: SummarizerApp) -> str:
    svg = ElementTree.fromstring(app.export_screenshot())
    return " ".join(
        "".join(node.itertext()) for node in svg.iter() if node.tag.endswith("}text")
    ).replace("\N{NO-BREAK SPACE}", " ")


async def fetch(app: SummarizerApp) -> None:
    app.query_one("#url", Input).value = URL
    app.prepare_requested()
    await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]


async def test_tall_confirmation_shows_all_metadata_and_requires_process() -> None:
    workflow = ConfirmationWorkflow()
    app = SummarizerApp(pipeline_factory=workflow.factory)
    async with app.run_test(size=(120, 40)) as pilot:
        await fetch(app)
        await pilot.pause()
        visible = screen_text(app)
        for field in (
            "Title: A video worth checking",
            "Channel: A channel",
            "Duration: 00:05:45.000",
            "Video ID: _5p1_TNSWqQ",
            f"URL: {URL}",
        ):
            assert field in visible
        assert not app.query_one("#metadata-hint").display
        assert app.focused is app.query_one("#confirm", Button)
        assert not workflow.confirmations
        await pilot.press("enter")
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        assert workflow.confirmations == [True]


async def test_compact_confirmation_hints_scroll_and_keeps_results_and_controls() -> (
    None
):
    workflow = ConfirmationWorkflow()
    app = SummarizerApp(pipeline_factory=workflow.factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await fetch(app)
        await pilot.pause()
        assert "Shift+Tab: video; arrows scroll" in screen_text(app)
        assert app.query_one(TabbedContent).region.height >= 4
        for selector in ("#confirm", "#cancel", "#metadata-hint"):
            assert app.query_one(selector).region.bottom < 24
        assert not workflow.confirmations
        await pilot.press("shift+tab")
        pane = app.query_one("#video-metadata", VerticalScroll)
        assert app.focused is pane
        await pilot.press("end")
        await pilot.pause()
        visible = screen_text(app)
        assert "Video ID: _5p1_TNSWqQ" in visible
        assert f"URL: {URL}" in visible
        await pilot.press("escape")
        assert not workflow.confirmations
        assert not app.query_one("#confirmation").display
        assert app.focused is app.query_one("#url", Input)


async def test_confirmation_resizes_and_new_metadata_resets_scroll() -> None:
    workflow = ConfirmationWorkflow()
    app = SummarizerApp(pipeline_factory=workflow.factory)
    async with app.run_test(size=(120, 40)) as pilot:
        await fetch(app)
        await pilot.pause()
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert app.query_one("#metadata-hint").display
        assert app.focused is app.query_one("#confirm", Button)
        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        assert not app.query_one("#metadata-hint").display
        assert "Video ID: _5p1_TNSWqQ" in screen_text(app)
        await pilot.press("escape")
        workflow.metadata = replace(
            workflow.metadata,
            title="Long title " * 100,
            original_url=URL + "&ignored=" + "a" * 250,
        )
        await fetch(app)
        await pilot.pause()
        pane = app.query_one("#video-metadata", VerticalScroll)
        assert app.query_one("#metadata-hint").display
        await pilot.press("shift+tab", "end")
        await pilot.pause()
        assert pane.scroll_y > 0
        assert "ignored=" in str(app.query_one("#metadata", Static).content)
        assert "URL:" in screen_text(app)
        await pilot.press("escape")
        workflow.metadata = replace(
            workflow.metadata, title="Another long title " * 100
        )
        await fetch(app)
        await pilot.pause()
        assert pane.scroll_y == 0
        assert "Title: Another long title" in screen_text(app)
        assert not workflow.confirmations
