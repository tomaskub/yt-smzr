"""Ollama tests use in-memory HTTP only, without a server or model."""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from yt_smzr.config import Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.summarization.base import SummarizationError, SummaryContent
from yt_smzr.summarization.ollama_provider import OllamaSummarizer, list_models
from yt_smzr.transcription.base import Transcript, TranscriptSegment

SETTINGS = Settings(summarization_provider="ollama", summarization_model="local:tag")
METADATA = VideoMetadata("dQw4w9WgXcQ", "url", "Title", "Channel", 10)
TRANSCRIPT = Transcript(
    provider="fake",
    model="fake",
    segments=(TranscriptSegment(start_seconds=0, end_seconds=2, text="Exact speech."),),
)
CONTENT = SummaryContent(
    short_summary="Notes.",
    detailed_summary="The speaker explains an idea.",
    key_points=("An idea.",),
    chapters=(),
    notable_claims=(),
    notable_quotes=(),
)


def test_chat_destination_schema_input_and_provenance() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "local:tag"
        assert body["stream"] is False
        assert body["format"] == SummaryContent.model_json_schema()
        assert "start_seconds" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": CONTENT.model_dump_json()},
            },
        )

    backend = OllamaSummarizer(SETTINGS, transport=httpx.MockTransport(respond))
    backend.preflight()
    assert requests == []
    summary = backend.summarize(METADATA, TRANSCRIPT)
    assert str(requests[0].url) == "http://localhost:11434/api/chat"
    assert summary.provider == "ollama"
    assert summary.model == "local:tag"


def test_input_limit_precedes_request() -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("No server calls allowed")

    backend = OllamaSummarizer(
        replace(SETTINGS, summarizer_max_input_bytes=1),
        transport=httpx.MockTransport(unexpected),
    )
    with pytest.raises(SummarizationError, match="single-call"):
        backend.summarize(METADATA, TRANSCRIPT)


@pytest.mark.parametrize(
    "body",
    [
        {"done": False},
        {"done": True, "done_reason": "length"},
        {"done": True, "message": {"content": "secret invalid JSON"}},
        {"done": True, "message": {"content": "{}"}},
    ],
)
def test_invalid_output_is_safe(body: dict[str, object]) -> None:
    backend = OllamaSummarizer(
        SETTINGS,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    with pytest.raises(SummarizationError) as caught:
        backend.summarize(METADATA, TRANSCRIPT)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "message"), [(404, "installed model"), (500, "context size")]
)
def test_server_rejection_is_actionable_and_safe(status: int, message: str) -> None:
    backend = OllamaSummarizer(
        SETTINGS,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, json={"error": "remote secret"}),
        ),
    )
    with pytest.raises(SummarizationError, match=message) as caught:
        backend.summarize(METADATA, TRANSCRIPT)
    assert "remote secret" not in str(caught.value)


def test_timeout_and_connection_failure() -> None:
    for failure, message in [
        (httpx.ReadTimeout("secret"), "timed out"),
        (httpx.ConnectError("secret"), "Cannot reach"),
    ]:

        def fail(request: httpx.Request, error: Exception = failure) -> httpx.Response:
            raise error

        with pytest.raises(SummarizationError, match=message):
            list_models(SETTINGS, transport=httpx.MockTransport(fail))


@pytest.mark.parametrize("names", [[], ["local:tag", "second:tag"]])
def test_explicit_installed_model_listing(names: list[str]) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": name} for name in names]})

    assert list_models(SETTINGS, transport=httpx.MockTransport(respond)) == tuple(names)


@pytest.mark.parametrize("frontend", ["cli", "tui", "service"])
async def test_entrypoints_exports_cache_force_and_failed_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frontend: str,
) -> None:
    from collections.abc import Callable
    from pathlib import Path

    from textual.widgets import Input

    from yt_smzr import cli
    from yt_smzr.pipeline import Pipeline, PipelineError, PipelineEvent
    from yt_smzr.storage.sqlite import CacheStore
    from yt_smzr.summarization.service import summarize_transcript
    from yt_smzr.tui import SummarizerApp

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".yt-smzr.toml").write_text(
        '[summarization]\nprovider="ollama"\nmodel="local:tag"\n'
    )
    monkeypatch.setenv("YT_SMZR_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    requests: list[httpx.Request] = []
    failure = False

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure:
            return httpx.Response(500, json={"error": "secret"})
        return httpx.Response(
            200,
            json={
                "done": True,
                "done_reason": "stop",
                "message": {"content": CONTENT.model_dump_json()},
            },
        )

    def build(settings: Settings) -> OllamaSummarizer:
        return OllamaSummarizer(settings, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("yt_smzr.summarization.ollama_provider.OllamaSummarizer", build)

    class Speech:
        def preflight(self) -> None:
            pass

        def transcribe(self, audio_path: Path) -> Transcript:
            return TRANSCRIPT

    def download(url: str, directory: Path) -> Path:
        path = directory / "audio.m4a"
        path.write_bytes(b"audio")
        return path

    def workflow(on_event: Callable[[PipelineEvent], None] | None = None) -> Pipeline:
        return Pipeline(
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
    store = CacheStore(tmp_path / "outputs")
    if frontend == "cli":
        monkeypatch.setattr(cli, "Pipeline", workflow)
        assert cli.main(["summarize", url, "--yes"]) == 0
    elif frontend == "tui":
        app = SummarizerApp(pipeline_factory=workflow)
        async with app.run_test(size=(100, 35)) as pilot:
            app.query_one("#url", Input).value = url
            await pilot.click("#prepare")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()
    else:
        store.save_metadata(METADATA)
        summarize_transcript(METADATA, TRANSCRIPT, store)
    record = store.lookup(METADATA.video_id)
    assert record is not None and record.summarization_provider == "ollama"
    assert record.summarization_model == "local:tag"
    assert record.summary_path is not None and record.summary_json_path is not None
    old = record.summary_json_path.read_bytes()
    assert len(requests) == 1
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "second:tag")
    cached = summarize_transcript(METADATA, TRANSCRIPT, store)
    assert cached.model == "local:tag" and len(requests) == 1
    failure = True
    with pytest.raises(SummarizationError):
        summarize_transcript(METADATA, TRANSCRIPT, store, force=True)
    assert record.summary_json_path.read_bytes() == old
    assert store.lookup(METADATA.video_id) == record
    failure = False
    refreshed = summarize_transcript(METADATA, TRANSCRIPT, store, force=True)
    assert refreshed.model == "second:tag"
    if frontend != "service":
        pipeline = workflow()
        prepared = pipeline.prepare(url, force=True)
        monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "changed-after-prepare:tag")
        result = pipeline.process(prepared, confirmed=True)
        assert result.summary.model == "second:tag"
        before = {
            path.name: path.read_bytes() for path in result.paths.directory.iterdir()
        }
        record_before = store.lookup(METADATA.video_id)
        failure = True
        with pytest.raises(PipelineError):
            pipeline.process(pipeline.prepare(url, force=True), confirmed=True)
        assert {
            path.name: path.read_bytes() for path in result.paths.directory.iterdir()
        } == before
        assert store.lookup(METADATA.video_id) == record_before


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_timeout_rejected_before_network(timeout: float) -> None:
    settings = replace(SETTINGS, ollama_timeout_seconds=timeout)
    with pytest.raises(SummarizationError, match="finite and positive"):
        OllamaSummarizer(settings).preflight()
    with pytest.raises(SummarizationError, match="finite and positive"):
        list_models(settings)


def test_credentials_in_manual_url_are_never_echoed() -> None:
    settings = replace(SETTINGS, ollama_base_url="http://name:private-secret@localhost")
    with pytest.raises(SummarizationError, match="without credentials") as caught:
        OllamaSummarizer(settings).preflight()
    assert "private-secret" not in str(caught.value)
