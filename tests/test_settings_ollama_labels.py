"""Ollama discovery labels and keyboard behavior across provider drafts."""

import threading
from typing import cast

import pytest
from textual.app import App
from textual.widgets import Button, Input, Select, Static

from yt_smzr.config import Settings
from yt_smzr.settings_screen import SettingsScreen

PROVIDERS = ("openai", "openrouter", "ollama")


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_ollama_refresh_preserves_provider_drafts(size: tuple[int, int]) -> None:
    calls: list[Settings] = []

    def load(settings: Settings) -> tuple[str, ...]:
        calls.append(settings)
        return ("qwen3:8b",)

    active = Settings(summarization_provider="openrouter", summarization_model="active")
    app: App[None] = App()
    async with app.run_test(size=size) as pilot:
        screen = SettingsScreen(active, model_loader=load)
        app.push_screen(screen)
        await pilot.pause()
        provider = cast(Select[str], screen.query_one("#settings-provider", Select))
        model = screen.query_one("#settings-model", Input)
        refresh = screen.query_one("#settings-refresh", Button)
        listing = screen.query_one("#settings-models", Static)
        assert str(refresh.label) == "Refresh Ollama models"
        assert "installed Ollama models" in str(listing.content)

        # Visit every provider twice using only the provider dropdown's keys.
        for visit in range(2):
            for index, name in enumerate(PROVIDERS):
                current = PROVIDERS.index(str(provider.value))
                provider.focus()
                key = "j" if index > current else "k"
                await pilot.press("enter", *([key] * abs(index - current)), "enter")
                assert provider.value == name
                await pilot.press("tab")
                assert screen.focused is model
                if visit == 0:
                    await pilot.press("home", "ctrl+k", *list(f"{name}-draft"))
                assert model.value == f"{name}-draft"
                # Model -> server -> timeout -> refresh, including scroll at 80x24.
                await pilot.press("tab", "tab", "tab")
                assert screen.focused is refresh
                assert refresh.region.intersection(screen.region) == refresh.region
                await pilot.press("enter")
                await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
                await pilot.pause()
                assert str(listing.content) == "Installed Ollama models: qwen3:8b"
                assert provider.value == name
                assert model.value == f"{name}-draft"
                assert calls[-1].summarization_provider == name
                assert calls[-1].summarization_model == f"{name}-draft"
                assert screen.settings == active
                assert "Active: openrouter / active" in str(
                    screen.query_one("#settings-active", Static).content
                )


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_ollama_refresh_keeps_hosted_draft_editable(
    size: tuple[int, int],
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def load(settings: Settings) -> tuple[str, ...]:
        assert settings.ollama_timeout_seconds == 3
        entered.set()
        release.wait(timeout=5)
        return ()

    app: App[None] = App()
    async with app.run_test(size=size) as pilot:
        screen = SettingsScreen(
            Settings(
                summarization_provider="openrouter",
                summarization_model="original",
                ollama_timeout_seconds=3,
            ),
            model_loader=load,
        )
        app.push_screen(screen)
        await pilot.pause()
        try:
            await pilot.press("tab", "tab", "tab", "tab", "enter")
            await pilot.pause()
            assert entered.is_set()
            assert screen.query_one("#settings-refresh", Button).disabled
            assert "Checking installed Ollama models" in str(
                screen.query_one("#settings-models", Static).content
            )
            model = screen.query_one("#settings-model", Input)
            model.focus()
            await pilot.press("home", "ctrl+k", *list("edited"))
            assert model.value == "edited"
        finally:
            release.set()
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        assert not screen.query_one("#settings-refresh", Button).disabled
        assert "No installed models on the Ollama server" in str(
            screen.query_one("#settings-models", Static).content
        )
        assert model.value == "edited"
