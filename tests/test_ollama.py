"""Ollama tests use in-memory HTTP only, without a server or model."""

import json
from dataclasses import replace

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
