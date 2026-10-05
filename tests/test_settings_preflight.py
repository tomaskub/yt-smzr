"""Settings submission exercises the real pipeline and hosted local preflight."""

import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import pytest
from openai import OpenAI
from textual.widgets import Input, Select, Static

from yt_smzr import config, tui
from yt_smzr.config import ENV_FIELDS, Settings
from yt_smzr.pipeline import Pipeline, PipelineEvent
from yt_smzr.settings_screen import SettingsScreen
from yt_smzr.summarization.base import SummaryContent
from yt_smzr.summarization.openrouter_provider import OpenRouterSummarizer
from yt_smzr.transcription.base import Transcript, TranscriptSegment

URL = "https://youtu.be/dQw4w9WgXcQ"
MODEL = "openai/gpt-4.1-mini"


class LocalTranscriber:
    def preflight(self) -> None:
        pass

    def transcribe(self, audio_path: Path) -> Transcript:
        return Transcript(
            provider="fake",
            model="test",
            segments=(
                TranscriptSegment(start_seconds=0, end_seconds=1, text="Speech."),
            ),
        )


@pytest.fixture
def local_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[Pipeline], list[str]]:
    monkeypatch.setattr(config, "config_path", lambda: tmp_path / "settings.toml")
    for variable in (*ENV_FIELDS.values(), "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("YT_SMZR_OUTPUT_DIR", str(tmp_path / "output"))
    pipelines: list[Pipeline] = []
    external: list[str] = []

    def extract(url: str) -> Mapping[str, object]:
        external.append("metadata")
        return {
            "id": "dQw4w9WgXcQ",
            "title": "Video",
            "channel": "Channel",
            "duration": 20,
        }

    def create(
        settings: Settings, *, on_event: Callable[[PipelineEvent], None]
    ) -> Pipeline:
        pipeline = Pipeline(
            settings,
            extractor=extract,
            audio_preflight=lambda: None,
            transcriber=LocalTranscriber(),
            on_event=on_event,
        )
        pipelines.append(pipeline)
        return pipeline

    # Keep SummarizerApp's actual configuration factory and the real summarizer.
    monkeypatch.setattr(tui, "Pipeline", create)
    return pipelines, external


@pytest.mark.parametrize("action", ["apply", "save", "cancel", "escape"])
@pytest.mark.parametrize("has_key", [False, True])
async def test_settings_selection_to_real_preflight(
    monkeypatch: pytest.MonkeyPatch,
    local_pipeline: tuple[list[Pipeline], list[str]],
    action: str,
    has_key: bool,
) -> None:
    pipelines, external = local_pipeline
    if has_key:
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-never-sent")
    app = tui.SummarizerApp()
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.press("tab", "s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        screen = app.screen
        assert "Active: openai / gpt-4.1-mini" in str(
            screen.query_one("#settings-active", Static).content
        )
        assert "draft until Apply or Save" in str(
            screen.query_one("#settings-notice", Static).content
        )
        await pilot.press("enter", "j", "enter")
        assert (
            cast(Select[str], screen.query_one("#settings-provider", Select)).value
            == "openrouter"
        )
        screen.query_one("#settings-model", Input).value = MODEL
        if action == "escape":
            await pilot.press("escape")
        else:
            await pilot.click(f"#settings-{action}")
        await pilot.pause()
        applied = action in {"apply", "save"}
        provider = "openrouter" if applied else "openai"
        model = MODEL if applied else "gpt-4.1-mini"
        assert (
            str(app.query_one("#provider-status", Static).content)
            == f"{provider} / {model}"
        )
        assert config.config_path().exists() == (action == "save")
        app.query_one("#url", Input).value = URL
        await pilot.click("#prepare")
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        assert pipelines[-1].settings is not None
        assert pipelines[-1].settings.summarization_provider == provider
        if applied and has_key:
            assert external == ["metadata"]
            prepared = app._prepared  # pyright: ignore[reportPrivateUsage]
            assert prepared is not None and prepared.settings == pipelines[-1].settings
            assert "confirmation" in str(app.query_one("#stage", Static).content)
            # Settings stay closed both during confirmation and during processing.
            app.action_settings()
            assert not isinstance(app.screen, SettingsScreen)
        else:
            message = str(app.query_one("#stage", Static).content)
            assert ("OPENROUTER_API_KEY" if applied else "OPENAI_API_KEY") in message
            assert "fake-never-sent" not in message
            assert external == []


@pytest.mark.parametrize("override", [False, True])
async def test_save_restart_effective_label_matches_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    local_pipeline: tuple[list[Pipeline], list[str]],
    override: bool,
) -> None:
    pipelines, external = local_pipeline
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-never-sent")
    if override:
        monkeypatch.setenv("YT_SMZR_SUMMARIZATION_PROVIDER", "openai")
        monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "environment-model")
    app = tui.SummarizerApp()
    async with app.run_test(size=(100, 35)) as pilot:
        await pilot.press("tab", "s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        screen = app.screen
        await pilot.press("enter", "j", "enter")
        screen.query_one("#settings-model", Input).value = MODEL
        await pilot.click("#settings-save")
        await pilot.pause()
        assert (
            str(app.query_one("#provider-status", Static).content)
            == f"openrouter / {MODEL}"
        )
        app.query_one("#url", Input).value = URL
        await pilot.click("#prepare")
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        assert external == ["metadata"]
    external.clear()
    restarted = tui.SummarizerApp()
    async with restarted.run_test(size=(100, 35)) as pilot:
        label = "openai / environment-model" if override else f"openrouter / {MODEL}"
        assert str(restarted.query_one("#provider-status", Static).content) == label
        restarted.query_one("#url", Input).value = URL
        await pilot.press("enter")
        await restarted.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        settings = pipelines[-1].settings
        assert settings is not None
        assert (
            label
            == f"{settings.summarization_provider} / {settings.summarization_model}"
        )
        if override:
            assert "OPENAI_API_KEY" in str(
                restarted.query_one("#stage", Static).content
            )
            assert external == []
        else:
            assert external == ["metadata"]


async def test_prepared_openrouter_process_uses_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    local_pipeline: tuple[list[Pipeline], list[str]],
) -> None:
    pipelines, _ = local_pipeline
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-never-sent")
    requests: list[httpx.Request] = []
    entered, release = threading.Event(), threading.Event()

    def build(settings: Settings) -> OpenRouterSummarizer:
        def client(key: str) -> OpenAI:
            def respond(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                return httpx.Response(
                    200,
                    json={
                        "id": "test",
                        "object": "chat.completion",
                        "created": 1,
                        "model": MODEL,
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": SummaryContent(
                                        short_summary="Notes.",
                                        detailed_summary="Detailed notes.",
                                        key_points=(),
                                        chapters=(),
                                        notable_claims=(),
                                        notable_quotes=(),
                                    ).model_dump_json(),
                                },
                            }
                        ],
                    },
                )

            return OpenAI(
                api_key=key,
                base_url="https://openrouter.ai/api/v1",
                http_client=httpx.Client(transport=httpx.MockTransport(respond)),
            )

        return OpenRouterSummarizer(settings, factory=client)

    monkeypatch.setattr(
        "yt_smzr.summarization.openrouter_provider.OpenRouterSummarizer", build
    )

    def download(url: str, directory: Path) -> Path:
        entered.set()
        assert release.wait(timeout=5)
        path = directory / "audio.m4a"
        path.write_bytes(b"fake audio")
        return path

    app = tui.SummarizerApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("tab", "s")
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("enter", "j", "enter", "tab")
        await pilot.press(*list(MODEL))
        # Model -> server -> timeout -> refresh -> Apply. Small-terminal scroll
        # follows focus so the action remains reachable by keyboard.
        await pilot.press("tab", "tab", "tab", "tab", "enter")
        await pilot.pause()
        assert not isinstance(app.screen, SettingsScreen)
        app.query_one("#url", Input).value = URL
        await pilot.click("#prepare")
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        prepared = app._prepared  # pyright: ignore[reportPrivateUsage]
        assert prepared is not None and prepared.settings is not None
        pipelines[-1].settings = replace(
            prepared.settings, summarization_provider="openai", openrouter_api_key=None
        )
        monkeypatch.setenv("YT_SMZR_SUMMARIZATION_PROVIDER", "openai")
        monkeypatch.delenv("OPENROUTER_API_KEY")
        pipelines[-1].downloader = download
        await pilot.click("#confirm")
        await pilot.pause()
        assert entered.is_set()
        app.action_settings()
        assert not isinstance(app.screen, SettingsScreen)
        release.set()
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        assert "completion" in str(app.query_one("#stage", Static).content)
        assert len(requests) == 1
        assert MODEL in requests[0].content.decode()
        assert "openrouter" in str(app.query_one("#summary", Static).content)
