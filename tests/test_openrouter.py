"""OpenRouter coverage with actual SDK requests and an in-memory transport."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from openai import OpenAI
from test_summarization import METADATA, content, transcript

from yt_smzr.config import Settings
from yt_smzr.pipeline import Pipeline
from yt_smzr.storage.sqlite import CacheStore
from yt_smzr.summarization.base import SummarizationError, Summary
from yt_smzr.summarization.factory import create_summarizer
from yt_smzr.summarization.openai_provider import OpenAISummarizer
from yt_smzr.summarization.openrouter_provider import (
    OpenRouterSummarizer,
    create_client,
)
from yt_smzr.summarization.service import summarize_transcript
from yt_smzr.transcription.base import Transcript

SETTINGS = Settings(
    summarization_provider="openrouter",
    summarization_model="vendor/structured-model",
    openrouter_api_key="router-secret",
    openai_api_key="unused-openai-secret",
)


def factory(requests: list[httpx.Request], mode: str = "success"):
    def build(key: str) -> OpenAI:
        assert key == "router-secret"

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if mode in {"authentication", "unsupported", "provider"}:
                status = {"authentication": 401, "unsupported": 400, "provider": 503}
                return httpx.Response(
                    status[mode],
                    json={"error": {"message": "router-secret private remote error"}},
                )
            message: dict[str, object] = {
                "role": "assistant",
                "content": content().model_dump_json(),
            }
            if mode == "invalid-json":
                message["content"] = "router-secret not JSON"
            if mode == "invalid-schema":
                message["content"] = '{"short_summary":"router-secret"}'
            if mode == "refusal":
                message["refusal"] = "router-secret"
            if mode == "no-content":
                message["content"] = None
            if mode == "grounding":
                data = content().model_dump()
                data["notable_quotes"] = [
                    {"quote": "Invented words", "timestamp_seconds": 0}
                ]
                message["content"] = json.dumps(data)
            if mode == "chapters":
                data = content().model_dump()
                data["chapters"] = [
                    {
                        "title": "Invented",
                        "start_seconds": 0,
                        "end_seconds": None,
                        "summary": "Invented outline",
                    }
                ]
                message["content"] = json.dumps(data)
            choices = [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "length"
                    if mode == "incomplete"
                    else "content_filter"
                    if mode == "filtered"
                    else "stop",
                }
            ]
            return httpx.Response(
                200,
                json={
                    "id": "chat-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "vendor/structured-model",
                    "choices": [] if mode == "no-output" else choices,
                },
            )

        return OpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )

    return build


def test_sdk_request_exports_and_provenance(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    provider = OpenRouterSummarizer(SETTINGS, factory=factory(requests))
    store = CacheStore(tmp_path)
    store.save_metadata(METADATA)
    summary = summarize_transcript(
        METADATA, transcript(), store, settings=SETTINGS, summarizer=provider
    )
    assert summary.provider == "openrouter"
    assert summary.model == SETTINGS.summarization_model
    request = requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer router-secret"
    body = json.loads(request.content)
    assert body["model"] == SETTINGS.summarization_model
    assert body["provider"] == {"require_parameters": True}
    schema_request = body["response_format"]
    assert schema_request["type"] == "json_schema"
    assert schema_request["json_schema"]["strict"] is True
    assert schema_request["json_schema"]["schema"]["additionalProperties"] is False
    assert body["messages"][0]["role"] == "system"
    source = json.loads(body["messages"][1]["content"])
    assert source["transcript_segments"] == [
        segment.model_dump() for segment in transcript().segments
    ]
    paths = store.paths(METADATA.video_id)
    assert Summary.model_validate_json(paths.summary_json.read_text()) == summary
    assert "openrouter" in paths.summary.read_text()
    assert "secret" not in paths.summary_json.read_text()
    record = store.lookup(METADATA.video_id)
    assert record is not None
    assert record.summarization_provider == "openrouter"
    assert record.summarization_model == SETTINGS.summarization_model
    summarize_transcript(METADATA, transcript(), store, settings=SETTINGS)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "mode",
    [
        "authentication",
        "unsupported",
        "provider",
        "invalid-json",
        "invalid-schema",
        "refusal",
        "incomplete",
        "filtered",
        "no-content",
        "no-output",
        "grounding",
        "chapters",
    ],
)
def test_failed_refresh_preserves_exports(tmp_path: Path, mode: str) -> None:
    requests: list[httpx.Request] = []
    store = CacheStore(tmp_path)
    store.save_metadata(METADATA)
    summarize_transcript(
        METADATA,
        transcript(),
        store,
        settings=SETTINGS,
        summarizer=OpenRouterSummarizer(SETTINGS, factory=factory(requests)),
    )
    paths = store.paths(METADATA.video_id)
    before = (paths.summary.read_bytes(), paths.summary_json.read_bytes())
    record = store.lookup(METADATA.video_id)
    provider = OpenRouterSummarizer(SETTINGS, factory=factory(requests, mode))
    with pytest.raises(SummarizationError) as caught:
        summarize_transcript(
            METADATA,
            transcript(),
            store,
            settings=SETTINGS,
            summarizer=provider,
            force=True,
        )
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert (paths.summary.read_bytes(), paths.summary_json.read_bytes()) == before
    assert store.lookup(METADATA.video_id) == record
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("key", "model", "match"),
    [
        (None, "vendor/model", "OPENROUTER_API_KEY"),
        (" ", "vendor/model", "OPENROUTER_API_KEY"),
        ("router-secret", None, "SUMMARIZATION_MODEL"),
        ("router-secret", " ", "SUMMARIZATION_MODEL"),
    ],
)
def test_invalid_local_config(key: str | None, model: str | None, match: str) -> None:
    settings = Settings(
        summarization_provider="openrouter",
        openrouter_api_key=key,
        summarization_model=model,
    )
    with pytest.raises(SummarizationError, match=match):
        create_summarizer(settings).preflight()


def test_local_preflight_defaults_and_selected_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_PROVIDER", "openrouter")
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "vendor/model")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings.from_env()
    provider = create_summarizer(settings)
    assert isinstance(provider, OpenRouterSummarizer)

    def forbidden(key: str) -> OpenAI:
        pytest.fail("preflight and oversized input must never construct a client")

    provider.factory = forbidden
    provider.preflight()
    assert "secret" not in repr(settings)
    provider.settings = replace(settings, summarizer_max_input_bytes=1)
    with pytest.raises(SummarizationError, match="single-call"):
        provider.summarize(METADATA, transcript())
    monkeypatch.delenv("YT_SMZR_SUMMARIZATION_MODEL")
    with pytest.raises(SummarizationError, match="SUMMARIZATION_MODEL"):
        create_summarizer(Settings.from_env()).preflight()
    assert isinstance(create_summarizer(Settings()), OpenAISummarizer)
    assert Settings().summarization_model == "gpt-4.1-mini"
    with pytest.raises(SummarizationError, match="PROVIDER") as caught:
        create_summarizer(replace(settings, summarization_provider="secret-provider"))
    assert "secret-provider" not in str(caught.value)


def test_default_client_endpoint_and_local_construction() -> None:
    with create_client("router-secret") as client:
        assert str(client.base_url) == "https://openrouter.ai/api/v1/"
        assert client.api_key == "router-secret"
        assert client.max_retries == 0


def test_pipeline_and_service_share_provider_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    provider = OpenRouterSummarizer(SETTINGS, factory=factory(requests))

    selected: list[Settings] = []

    def build(settings: Settings) -> OpenRouterSummarizer:
        selected.append(settings)
        assert settings == SETTINGS
        return provider

    # Patch the lazy import's class, leaving both entrypoint factories real.
    monkeypatch.setattr(
        "yt_smzr.summarization.openrouter_provider.OpenRouterSummarizer", build
    )

    class Transcriber:
        def preflight(self) -> None:
            pass

        def transcribe(self, audio_path: Path) -> Transcript:
            return transcript()

    pipeline = Pipeline(
        SETTINGS,
        transcriber=Transcriber(),
        audio_preflight=lambda: None,
        extractor=lambda url: {
            "id": METADATA.video_id,
            "title": "Title",
            "channel": "Channel",
            "duration": 10,
        },
    )
    prepared = pipeline.prepare(f"https://youtu.be/{METADATA.video_id}")
    assert prepared.metadata.video_id == METADATA.video_id
    assert selected == [SETTINGS]
    store = CacheStore(tmp_path)
    store.save_metadata(METADATA)
    result = summarize_transcript(METADATA, transcript(), store, settings=SETTINGS)
    assert result.provider == "openrouter"
    assert len(requests) == 1


@pytest.mark.parametrize("frontend", ["cli", "tui"])
async def test_frontends_run_selected_openrouter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frontend: str,
) -> None:
    from collections.abc import Callable

    from textual.widgets import Input

    from yt_smzr import cli
    from yt_smzr.pipeline import PipelineEvent
    from yt_smzr.tui import SummarizerApp

    requests: list[httpx.Request] = []
    settings = replace(SETTINGS, output_dir=tmp_path / "output")

    def build(configuration: Settings) -> OpenRouterSummarizer:
        return OpenRouterSummarizer(configuration, factory=factory(requests))

    monkeypatch.setattr(
        "yt_smzr.summarization.openrouter_provider.OpenRouterSummarizer", build
    )

    class Speech:
        def preflight(self) -> None:
            pass

        def transcribe(self, audio_path: Path) -> Transcript:
            return transcript()

    def download(url: str, directory: Path) -> Path:
        path = directory / "audio.m4a"
        path.write_bytes(b"fake audio")
        return path

    def workflow(on_event: Callable[[PipelineEvent], None]) -> Pipeline:
        return Pipeline(
            settings,
            on_event=on_event,
            transcriber=Speech(),
            audio_preflight=lambda: None,
            downloader=download,
            extractor=lambda url: {
                "id": METADATA.video_id,
                "title": "Title",
                "channel": "Channel",
                "duration": 10,
            },
        )

    url = f"https://youtu.be/{METADATA.video_id}"
    if frontend == "cli":
        monkeypatch.setattr(cli, "Pipeline", workflow)
        assert cli.main(["summarize", url, "--yes"]) == 0
    else:
        app = SummarizerApp(pipeline_factory=workflow)
        async with app.run_test(size=(100, 35)) as pilot:
            app.query_one("#url", Input).value = url
            await pilot.click("#prepare")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()
    assert len(requests) == 1
    store = CacheStore(settings.output_dir)
    record = store.lookup(METADATA.video_id)
    assert record is not None and record.summarization_provider == "openrouter"
    assert record.summarization_model == settings.summarization_model
