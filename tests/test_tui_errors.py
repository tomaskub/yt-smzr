"""Read the actual event/status strips and scrollable error content after resize."""

from collections.abc import Callable

import pytest
from textual.containers import VerticalScroll
from textual.geometry import Region
from textual.widgets import Input, RichLog, Static

from yt_smzr.error_display import FailureScreen
from yt_smzr.pipeline import PipelineError, PipelineEvent, PipelineStage
from yt_smzr.tui import SummarizerApp, Workflow

LONG_ERROR = (
    "faster-whisper could not transcribe audio with model /private/tmp/"
    + "local-model-directory/" * 70
    + "tiny.en. Check the retained audio and model files.\n\n   "
)


def failure_app(message: str) -> SummarizerApp:
    def factory(callback: Callable[[PipelineEvent], None]) -> Workflow:
        for _ in range(5):
            callback(PipelineEvent(PipelineStage.METADATA, "Reading metadata."))
        raise PipelineError(PipelineStage.TRANSCRIPTION, message)

    return SummarizerApp(pipeline_factory=factory)


async def submit(app: SummarizerApp) -> None:
    app.query_one("#url", Input).value = "https://youtu.be/dQw4w9WgXcQ"
    app.prepare_requested()
    await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]


def visible_status(widget: Static) -> str:
    return "\n".join(
        strip.text for strip in widget.render_lines(Region(0, 0, widget.size.width, 2))
    )


@pytest.mark.parametrize("initial", [(120, 40), (80, 24)])
@pytest.mark.parametrize(
    "message",
    ["Missing OPENROUTER_API_KEY.\n\n   ", LONG_ERROR],
)
async def test_latest_failure_beginning_reflows_and_full_error_is_reachable(
    initial: tuple[int, int], message: str
) -> None:
    app = failure_app(message)
    async with app.run_test(size=initial) as pilot:
        app.query_one("#summary", Static).update("Previous successful summary.")
        await submit(app)
        await pilot.pause()

        for width, height in (initial, (80, 24), (120, 40), (80, 24)):
            await pilot.resize_terminal(width, height)
            await pilot.pause()
            events = app.query_one("#events", RichLog)
            assert events.render_line(0).text.startswith("transcription: Failed:")
            assert events.scroll_x == 0
            assert events.virtual_size.width <= events.scrollable_content_region.width
            assert all(line.cell_length <= width - 2 for line in events.lines)
            status = visible_status(app.query_one("#stage", Static))
            assert "transcription: Failed:" in status
            assert message.split()[0] in status
            assert "F2 full error" in status
            assert app.query_one("#events").region.height == 3
            assert app.query_one("#summary-tab .result-pane").region.height >= 8
            assert "Previous successful summary" in str(
                app.query_one("#summary", Static).content
            )

        # Failure returns focus to URL entry; F2 must work without leaving it.
        entry = app.query_one("#url", Input)
        assert app.focused is entry
        await pilot.press("f2")
        assert isinstance(app.screen, FailureScreen)
        details = app.screen.query_one("#error-message", Static)
        assert str(details.content) == message.strip()
        assert "Failure during transcription" in str(
            app.screen.query_one("#error-title", Static).content
        )
        assert "PgDn scroll" in visible_status(
            app.screen.query_one("#error-help", Static)
        )
        pane = app.screen.query_one("#error-scroll", VerticalScroll)
        assert app.focused is pane
        if message == LONG_ERROR:
            assert pane.max_scroll_y > 0
            await pilot.press("j", "pagedown", "end")
            await pilot.pause()
            assert pane.scroll_y > 0
            last_lines = "\n".join(
                strip.text
                for strip in details.render_lines(
                    Region(0, max(0, details.size.height - 3), details.size.width, 3)
                )
            )
            assert "Check the retained audio and model files." in " ".join(
                last_lines.split()
            )
        await pilot.press("escape")
        assert app.focused is entry
        await pilot.press("f2", "f2")
        assert app.focused is entry


async def test_new_preparation_clears_current_error_and_old_event_source() -> None:
    app = failure_app("Missing ffmpeg.")
    async with app.run_test(size=(80, 24)) as pilot:
        await submit(app)
        await pilot.pause()
        app.query_one("#url", Input).value = "invalid"
        app.prepare_requested()
        # Inspect synchronously before the worker's next failure is delivered.
        assert app.query_one("#events", RichLog).lines == []
        app.action_error_details()
        assert not isinstance(app.screen, FailureScreen)
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
