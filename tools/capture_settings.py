"""Capture settings at review terminal sizes without server calls.

Run uv run --locked python tools/capture_settings.py.
PNG rendering requires rsvg-convert, which is a review tool, not an app dependency.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from textual.theme import Theme

from yt_smzr.config import Settings
from yt_smzr.settings_screen import SettingsScreen
from yt_smzr.tui import SummarizerApp

DESTINATION = Path(__file__).resolve().parents[1] / "docs" / "captures"


async def capture(size: tuple[int, int], *, monochrome: bool = False) -> None:
    app = SummarizerApp()
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
        app.push_screen(
            SettingsScreen(
                Settings(
                    summarization_provider="ollama", summarization_model="qwen3:8b"
                ),
                model_loader=lambda settings: ("qwen3:8b", "gemma3:4b"),
            )
        )
        await pilot.pause()
        path = DESTINATION / f"{prefix}-settings.svg"
        app.save_screenshot(path.name, path=str(DESTINATION))
        subprocess.run(
            ["rsvg-convert", str(path), "-o", str(path.with_suffix(".png"))], check=True
        )


async def main() -> None:
    with TemporaryDirectory() as home:
        os.environ["HOME"] = home
        os.environ["YT_SMZR_SUMMARIZATION_MODEL"] = "environment-model"
        await capture((80, 24))
        await capture((120, 40))
        await capture((80, 24), monochrome=True)


if __name__ == "__main__":
    asyncio.run(main())
