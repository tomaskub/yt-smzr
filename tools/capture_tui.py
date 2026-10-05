"""Reproduce terminal review captures without network, credentials, or models.

Run: uv run --locked python tools/capture_tui.py
The workflow fake is shared with the behavioral TUI tests.
"""

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_tui import FakeWorkflow, result, wait_workers  # noqa: E402
from textual.theme import Theme  # noqa: E402
from textual.widgets import Input  # noqa: E402

from yt_smzr.pipeline import PipelineError, PipelineStage  # noqa: E402
from yt_smzr.tui import SummarizerApp  # noqa: E402

DESTINATION = Path(__file__).resolve().parents[1] / "docs" / "captures"


async def capture(size: tuple[int, int], *, monochrome: bool = False) -> None:
    output = result(Path(".yt-smzr") / ("long-output-directory-" * 6))
    output = replace(
        output,
        metadata=replace(
            output.metadata, title="A long video title about useful notes " * 4
        ),
    )
    workflow = FakeWorkflow(output)
    app = SummarizerApp(pipeline_factory=workflow.factory)
    if monochrome:
        app.register_theme(
            Theme(
                name="monochrome",
                primary="#ffffff",
                secondary="#aaaaaa",
                accent="#ffffff",
                foreground="#ffffff",
                background="#000000",
                surface="#000000",
                panel="#444444",
                success="#ffffff",
                warning="#ffffff",
                error="#ffffff",
                dark=True,
            )
        )
        app.theme = "monochrome"
    prefix = f"{size[0]}x{size[1]}" + ("-mono" if monochrome else "")
    async with app.run_test(size=size) as pilot:

        async def save(state: str) -> None:
            await pilot.pause()
            app.save_screenshot(f"{prefix}-{state}.svg", path=str(DESTINATION))

        await save("idle")
        app.query_one("#url", Input).value = "https://youtu.be/dQw4w9WgXcQ"
        await pilot.press("enter")
        await wait_workers(app)
        await save("confirmation")
        workflow.release.clear()
        await pilot.press("enter")
        await save("processing")
        workflow.release.set()
        await wait_workers(app)
        await save("completed")
        await pilot.press("3")
        await save("paths")
        await pilot.press("?")
        await save("help")
        await pilot.press("escape", "u", "enter")
        await wait_workers(app)
        workflow.failure = PipelineError(
            PipelineStage.SUMMARIZATION, "Input limit exceeded."
        )
        await pilot.press("enter")
        await wait_workers(app)
        await save("failed")


async def main() -> None:
    DESTINATION.mkdir(exist_ok=True)
    await capture((80, 24))
    await capture((120, 40))
    await capture((80, 24), monochrome=True)


if __name__ == "__main__":
    asyncio.run(main())
