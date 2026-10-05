"""Local smoke checks without network requests or model downloads."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import Input

from yt_smzr import __version__
from yt_smzr.tui import SummarizerApp


@pytest.mark.parametrize(
    "module",
    [
        "yt_smzr",
        "yt_smzr.cli",
        "yt_smzr.config",
        "yt_smzr.models",
        "yt_smzr.pipeline",
        "yt_smzr.tui",
        "yt_smzr.youtube",
        "yt_smzr.transcription",
        "yt_smzr.summarization",
        "yt_smzr.storage",
    ],
)
def test_package_import(module: str) -> None:
    assert importlib.import_module(module) is not None


@pytest.mark.parametrize("args", [[], ["--help"], ["--version"]])
def test_installed_console_entrypoint(args: list[str]) -> None:
    result = subprocess.run(
        [str(Path(sys.executable).with_name("yt-smzr")), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "yt-smzr" in result.stdout
    if args == ["--version"]:
        assert __version__ in result.stdout


def test_module_entrypoint() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "yt_smzr", "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"yt-smzr {__version__}"


async def test_tui_starts_and_quits() -> None:
    app = SummarizerApp()
    async with app.run_test() as pilot:
        assert app.query_one("#url", Input).is_mounted
        await pilot.press("ctrl+q")
        assert not app.is_running
