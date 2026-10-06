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


@pytest.mark.asyncio
async def test_app_keyboard_settings_apply_save_restart_and_focus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yt_smzr.tui import SummarizerApp

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "environment-model")
    app = SummarizerApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("tab", "s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        screen = app.screen
        await pilot.press("tab")
        assert screen.focused is screen.query_one("#settings-model")
        await pilot.press("shift+tab")
        assert screen.focused is screen.query_one("#settings-provider")
        assert "YT_SMZR_SUMMARIZATION_MODEL" in str(
            screen.query_one("#settings-overrides", Static).content
        )
        await pilot.press("enter", "j", "j", "enter", "tab")
        assert screen.query_one("#settings-model", Input).value == ""
        await pilot.press("home", "ctrl+k", *list("local:tag"))
        await pilot.press("tab", "home", "ctrl+k", *list("http://server:11434"))
        await pilot.press("tab", "home", "ctrl+k", "6", "0")
        await pilot.press("tab", "tab", "enter")
        await pilot.pause()
        assert app.focused is app.query_one("#prepare")
        assert "ollama / local:tag" in str(
            app.query_one("#provider-status", Static).content
        )
        assert not (tmp_path / ".yt-smzr.toml").exists()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        # Model input, server, timeout, refresh, Apply, Save.
        await pilot.press("tab", "tab", "tab", "tab", "tab", "tab", "enter")
        await pilot.pause()
        assert (tmp_path / ".yt-smzr.toml").is_file()
    restarted = SummarizerApp()
    async with restarted.run_test(size=(80, 24)):
        assert "ollama / environment-model" in str(
            restarted.query_one("#provider-status", Static).content
        )
    monkeypatch.delenv("YT_SMZR_SUMMARIZATION_MODEL")
    assert Settings.from_env().summarization_model == "local:tag"


@pytest.mark.asyncio
async def test_app_configuration_error_preserves_safe_file_field_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from yt_smzr.tui import SummarizerApp

    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".yt-smzr.toml"
    path.write_text('[summarization]\nprovider="secret-invalid"\n')
    app = SummarizerApp()
    async with app.run_test(size=(80, 24)) as pilot:
        app.query_one("#url", Input).value = "https://youtu.be/dQw4w9WgXcQ"
        await pilot.press("enter")
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        message = str(app.query_one("#stage", Static).content)
        assert "Invalid settings" in message
        assert "F2 full error" in message
        await pilot.press("f2")
        message = str(app.screen.query_one("#error-message", Static).content)
        assert str(path) in message
        assert "summarization.provider" in message
        assert "secret" not in message
