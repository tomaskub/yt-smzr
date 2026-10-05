"""Settings dialog interaction and worker responsiveness without servers."""

import threading
from pathlib import Path
from typing import cast

import pytest
from textual.app import App
from textual.widgets import Input, Select, Static

from yt_smzr.config import Settings
from yt_smzr.settings_screen import SettingsScreen
from yt_smzr.summarization.base import SummarizationError


@pytest.mark.asyncio
async def test_keyboard_apply_and_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = Settings(summarization_provider="ollama", summarization_model="old:tag")
    selections: list[Settings | None] = []
    app: App[None] = App()
    async with app.run_test(size=(80, 35)) as pilot:
        screen = SettingsScreen(settings)
        app.push_screen(screen, selections.append)
        await pilot.pause()
        screen.query_one("#settings-model", Input).focus()
        await pilot.press("home", "ctrl+k")
        await pilot.press(*list("new:tag"))
        # Move server, timeout, refresh, Apply, Save entirely by keyboard.
        await pilot.press("tab", "tab", "tab", "tab", "tab", "enter")
        await pilot.pause()
    assert selections[-1] is not None
    assert selections[-1].summarization_model == "new:tag"
    assert Settings.from_env().summarization_model == "new:tag"


@pytest.mark.asyncio
async def test_model_listing_worker_keeps_dialog_responsive() -> None:
    entered = threading.Event()
    release = threading.Event()

    def load(settings: Settings) -> tuple[str, ...]:
        entered.set()
        release.wait(timeout=5)
        return ()

    app: App[None] = App()
    async with app.run_test(size=(80, 35)) as pilot:
        screen = SettingsScreen(Settings(), model_loader=load)
        app.push_screen(screen)
        await pilot.pause()
        await pilot.click("#settings-refresh")
        await pilot.pause()
        assert entered.is_set()
        screen.query_one("#settings-model", Input).focus()
        await pilot.press("home", "ctrl+k", "x")
        assert screen.query_one("#settings-model", Input).value == "x"
        release.set()
        await pilot.pause()
        assert "No installed models" in str(
            screen.query_one("#settings-models", Static).content
        )


@pytest.mark.asyncio
async def test_model_listing_failure_and_manual_entry() -> None:
    def load(settings: Settings) -> tuple[str, ...]:
        raise SummarizationError("Cannot reach Ollama. Start ollama serve.")

    app: App[None] = App()
    async with app.run_test(size=(80, 35)) as pilot:
        screen = SettingsScreen(Settings(), model_loader=load)
        app.push_screen(screen)
        await pilot.pause()
        await pilot.click("#settings-refresh")
        await pilot.pause()
        assert "Cannot reach" in str(
            screen.query_one("#settings-models", Static).content
        )
        assert not screen.query_one("#settings-model", Input).disabled
        assert (
            cast(Select[str], screen.query_one("#settings-provider", Select)).value
            == "openai"
        )
