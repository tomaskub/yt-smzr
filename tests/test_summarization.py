"""Fake HTTP and provider seams; no real model, download, or provider calls."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import pytest
from openai import OpenAI
from pydantic import ValidationError

from yt_smzr.config import ConfigurationError, Settings
from yt_smzr.models import VideoChapter, VideoMetadata
from yt_smzr.storage.export import summary_json, summary_markdown
from yt_smzr.storage.sqlite import CacheRecord, CacheStore, StorageError
from yt_smzr.summarization.base import (
    Chapter,
    NotableClaim,
    NotableQuote,
    SummarizationError,
    Summary,
    SummaryContent,
    validate_grounding,
)
from yt_smzr.summarization.openai_provider import OpenAISummarizer
from yt_smzr.summarization.prompts import INSTRUCTIONS, check_input_limit, prepare_input
from yt_smzr.summarization.service import summarize_transcript
from yt_smzr.transcription.base import Transcript, TranscriptSegment

VIDEO_ID = "dQw4w9WgXcQ"
METADATA = VideoMetadata(VIDEO_ID, "url", "Title", "Channel", 10)
SETTINGS = Settings(openai_api_key="test-secret")


def transcript(text: str = "A useful idea.") -> Transcript:
    return Transcript(
        provider="fake",
        model="transcriber",
        segments=(
            TranscriptSegment(start_seconds=0.25, end_seconds=2, text=text),
            TranscriptSegment(start_seconds=4, end_seconds=7, text="Try it today."),
        ),
    )


def content(metadata: VideoMetadata = METADATA) -> SummaryContent:
    return SummaryContent(
        short_summary="An idea to try.",
        detailed_summary="The speaker introduces an idea and suggests trying it.",
        key_points=("Consider the idea.",),
        chapters=tuple(
            Chapter(
                title=chapter.title,
                start_seconds=chapter.start_seconds,
                end_seconds=chapter.end_seconds,
                summary="The speaker explains the idea.",
            )
            for chapter in metadata.chapters
        ),
        notable_claims=(
            NotableClaim(
                claim="The speaker calls the idea useful.", timestamp_seconds=0
            ),
        ),
        notable_quotes=(NotableQuote(quote="A useful idea.", timestamp_seconds=0),),
    )


def summary(metadata: VideoMetadata = METADATA) -> Summary:
    return Summary(**content(metadata).model_dump(), provider="fake", model="test")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("short_summary", ""),
        ("detailed_summary", " "),
        ("key_points", [" "]),
        (
            "chapters",
            [{"title": "", "start_seconds": 0, "end_seconds": None, "summary": "hi"}],
        ),
        ("notable_claims", [{"claim": " ", "timestamp_seconds": None}]),
        ("notable_claims", [{"claim": "hi", "timestamp_seconds": -1}]),
        ("notable_claims", [{"claim": "hi", "timestamp_seconds": True}]),
        ("notable_quotes", [{"quote": "hi", "timestamp_seconds": 1.5}]),
        ("notable_quotes", [{"quote": " ", "timestamp_seconds": None}]),
        ("unexpected", "extra"),
    ],
)
def test_summary_output_validation(field: str, value: object) -> None:
    data = content().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        SummaryContent.model_validate(data)


@pytest.mark.parametrize("end", [-1, float("nan"), float("inf")])
def test_chapter_times_validated(end: float) -> None:
    with pytest.raises(ValidationError):
        Chapter(title="Chapter", start_seconds=0, end_seconds=end, summary="Hi")


def test_required_fields_and_empty_optional_quotes() -> None:
    data = content().model_dump()
    del data["short_summary"]
    with pytest.raises(ValidationError):
        SummaryContent.model_validate(data)
    data = content().model_dump()
    data["notable_quotes"] = []
    assert SummaryContent.model_validate(data).notable_quotes == ()


@pytest.mark.parametrize(
    "alteration", ["missing", "extra", "title", "start", "end", "order"]
)
def test_outline_must_match_youtube_exactly(alteration: str) -> None:
    metadata = replace(
        METADATA,
        chapters=(VideoChapter("Intro", 0.25, 4.5), VideoChapter("Idea", 4.5, None)),
    )
    result = content(metadata).model_dump()
    chapters = [chapter.model_dump() for chapter in content(metadata).chapters]
    if alteration == "missing":
        chapters.pop()
    elif alteration == "extra":
        chapters.append(chapters[0])
    elif alteration == "order":
        chapters.reverse()
    else:
        chapters[0][
            {"title": "title", "start": "start_seconds", "end": "end_seconds"}[
                alteration
            ]
        ] = "Invented" if alteration == "title" else 1.5
    result["chapters"] = chapters
    with pytest.raises(SummarizationError, match="YouTube-provided"):
        validate_grounding(
            SummaryContent.model_validate(result), metadata, transcript()
        )


def test_no_chapters_are_inferred_and_fractional_outline_survives() -> None:
    validate_grounding(content(), METADATA, transcript())
    metadata = replace(METADATA, chapters=(VideoChapter("Intro", 0.25, None),))
    validate_grounding(content(metadata), metadata, transcript())
    assert content(metadata).chapters[0].start_seconds == 0.25
    with pytest.raises(SummarizationError, match="outline"):
        validate_grounding(content(metadata), METADATA, transcript())


@pytest.mark.parametrize(
    ("words", "time"),
    [
        ("Invented quote", None),
        ("A useful idea.", 4),
        ("A useful idea.", 11),
        ("use", None),
        ("a useful idea.", 0),
    ],
)
def test_quotes_require_source_text_and_its_timestamp(
    words: str, time: int | None
) -> None:
    result = content().model_copy(
        update={"notable_quotes": (NotableQuote(quote=words, timestamp_seconds=time),)}
    )
    with pytest.raises(SummarizationError, match="quote"):
        validate_grounding(result, METADATA, transcript())


def test_quotes_can_span_segments_and_match_repeated_speech() -> None:
    speech = transcript("A\n useful   idea.")
    result = content().model_copy(
        update={
            "notable_quotes": (
                NotableQuote(quote="useful idea. Try it", timestamp_seconds=0),
            )
        }
    )
    validate_grounding(result, METADATA, speech)
    repeated = transcript("Try it today.")
    result = content().model_copy(
        update={
            "notable_quotes": (
                NotableQuote(quote="Try it today.", timestamp_seconds=4),
            )
        }
    )
    validate_grounding(result, METADATA, repeated)
    result = result.model_copy(
        update={
            "notable_quotes": (
                NotableQuote(quote="Try it today.", timestamp_seconds=None),
            )
        }
    )
    validate_grounding(result, METADATA, repeated)


def test_claim_timestamp_cannot_exceed_video() -> None:
    result = content().model_copy(
        update={
            "notable_claims": (
                NotableClaim(claim="Video says hi", timestamp_seconds=11),
            )
        }
    )
    with pytest.raises(SummarizationError, match="claim timestamp"):
        validate_grounding(result, METADATA, transcript())


def test_input_limit_counts_utf8_timestamps_metadata_and_instructions() -> None:
    source = prepare_input(METADATA, transcript('"é"\\'))
    expected = json.dumps(
        {"instructions": INSTRUCTIONS, "input": source.source},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert source.size_bytes == len(expected)
    assert source.size_bytes > len('"é"\\'.encode())
    assert json.loads(source.source)["transcript_segments"][0]["start_seconds"] == 0.25
    check_input_limit(source, source.size_bytes)
    with pytest.raises(SummarizationError, match="single-call"):
        check_input_limit(source, source.size_bytes - 1)


def test_settings_read_environment_and_hide_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_PROVIDER", "openai")
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "configured-model")
    monkeypatch.setenv("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", "12345")
    monkeypatch.setenv("OPENAI_API_KEY", "never-print-this")
    settings = Settings.from_env()
    assert settings.summarization_model == "configured-model"
    assert settings.summarizer_max_input_bytes == 12345
    assert settings.openai_api_key == "never-print-this"
    assert "never-print-this" not in repr(settings)
    monkeypatch.setenv("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", "invalid-secret")
    with pytest.raises(ConfigurationError, match="positive integer") as caught:
        Settings.from_env()
    assert "invalid-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (Settings(), "OPENAI_API_KEY"),
        (replace(SETTINGS, openai_api_key=" "), "OPENAI_API_KEY"),
        (replace(SETTINGS, summarization_model=" "), "MODEL"),
        (replace(SETTINGS, summarization_provider="other"), "PROVIDER"),
        (replace(SETTINGS, summarizer_max_input_bytes=0), "positive integer"),
        (replace(SETTINGS, summarizer_max_input_bytes=-1), "positive integer"),
    ],
)
def test_preflight_configuration_without_constructing_client(
    settings: Settings, message: str
) -> None:
    def never_construct(key: str) -> OpenAI:
        pytest.fail("Preflight must not construct a client.")

    with pytest.raises(SummarizationError, match=message):
        OpenAISummarizer(settings, factory=never_construct).preflight()


def test_valid_preflight_and_missing_dependency_without_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never_construct(key: str) -> OpenAI:
        pytest.fail("Preflight must not construct a client.")

    provider = OpenAISummarizer(SETTINGS, factory=never_construct)
    provider.preflight()

    def missing_module(name: str) -> None:
        return None

    monkeypatch.setattr(importlib.util, "find_spec", missing_module)
    with pytest.raises(SummarizationError, match="run uv sync"):
        provider.preflight()


def response_body(result: SummaryContent, mode: str = "success") -> dict[str, object]:
    output: list[dict[str, object]] = [
        {
            "type": "message",
            "id": "msg_test",
            "status": "completed",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": result.model_dump_json(),
                    "annotations": [],
                }
            ],
        }
    ]
    if mode == "refusal":
        output[0]["content"] = [
            {"type": "refusal", "refusal": "private remote refusal"}
        ]
    elif mode == "no-output":
        output = []
    elif mode == "invalid":
        output[0]["content"] = [
            {
                "type": "output_text",
                "text": '{"short_summary":"only"}',
                "annotations": [],
            }
        ]
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": "configured-model",
        "status": "incomplete" if mode == "incomplete" else "completed",
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "text": {"format": {"type": "text"}},
        "incomplete_details": {"reason": "max_output_tokens"}
        if mode == "incomplete"
        else None,
    }


def fake_factory(
    requests: list[dict[str, object]], result: SummaryContent, mode: str = "success"
):
    def factory(key: str) -> OpenAI:
        assert key == "test-secret"

        def respond(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/responses"
            requests.append(json.loads(request.content))
            if mode == "failure":
                return httpx.Response(
                    500,
                    json={
                        "error": {
                            "message": "remote test-secret",
                            "type": "server_error",
                        }
                    },
                )
            return httpx.Response(200, json=response_body(result, mode))

        return OpenAI(
            api_key=key,
            max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )

    return factory


def test_openai_actual_sdk_request_and_structured_parse() -> None:
    requests: list[dict[str, object]] = []
    metadata = replace(METADATA, chapters=(VideoChapter("Intro", 0.25, None),))
    speech = transcript()
    provider = OpenAISummarizer(
        replace(SETTINGS, summarization_model="configured-model"),
        factory=fake_factory(requests, content(metadata)),
    )
    result = provider.summarize(metadata, speech)
    assert result.provider == "openai"
    assert result.model == "configured-model"
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "configured-model"
    assert request["instructions"] == INSTRUCTIONS
    assert "untrusted" in str(request["instructions"])
    source = json.loads(cast(str, request["input"]))
    assert source["transcript_segments"] == [
        segment.model_dump() for segment in speech.segments
    ]
    assert source["video"]["chapters"] == [
        {"title": "Intro", "start_seconds": 0.25, "end_seconds": None}
    ]
    assert request["store"] is False
    assert request["truncation"] == "disabled"
    text = cast(dict[str, object], request["text"])
    format_ = cast(dict[str, object], text["format"])
    assert format_["type"] == "json_schema"
    assert format_["strict"] is True
    schema = cast(dict[str, object], format_["schema"])
    assert schema["additionalProperties"] is False
    assert set(cast(list[str], schema["required"])) == set(SummaryContent.model_fields)
    assert "test-secret" not in summary_json(result)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("refusal", "refused"),
        ("incomplete", "incomplete"),
        ("no-output", "no structured"),
        ("invalid", "valid structured"),
        ("failure", "valid structured"),
    ],
)
def test_openai_failures_are_contextual_and_redacted(mode: str, message: str) -> None:
    requests: list[dict[str, object]] = []
    provider = OpenAISummarizer(
        SETTINGS, factory=fake_factory(requests, content(), mode)
    )
    with pytest.raises(SummarizationError, match=message) as caught:
        provider.summarize(METADATA, transcript())
    assert len(requests) == 1
    assert "test-secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_oversized_provider_input_never_constructs_client() -> None:
    def never_construct(key: str) -> OpenAI:
        pytest.fail("Oversized transcript must not construct a client.")

    provider = OpenAISummarizer(
        replace(SETTINGS, summarizer_max_input_bytes=1), factory=never_construct
    )
    with pytest.raises(SummarizationError, match="single-call"):
        provider.summarize(METADATA, transcript())


class FakeSummarizer:
    def __init__(self, failure: bool = False, result: Summary | None = None) -> None:
        self.calls = 0
        self.preflights = 0
        self.failure = failure
        self.result = result

    def preflight(self) -> None:
        self.preflights += 1

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary:
        self.calls += 1
        if self.failure:
            raise RuntimeError("private provider error")
        assert transcript.segments[0].start_seconds == 0.25
        return self.result or summary(metadata)


@pytest.fixture
def store(tmp_path: Path) -> CacheStore:
    result = CacheStore(tmp_path)
    result.save_metadata(METADATA)
    return result


def test_service_exports_cache_reuse_and_force(store: CacheStore) -> None:
    provider = FakeSummarizer()
    result = summarize_transcript(METADATA, transcript(), store, summarizer=provider)
    paths = store.paths(VIDEO_ID)
    assert paths.summary.read_text() == summary_markdown(result)
    assert Summary.model_validate_json(paths.summary_json.read_text()) == result
    record = store.lookup(VIDEO_ID)
    assert record is not None
    assert record.summary_path == paths.summary
    assert record.summary_json_path == paths.summary_json
    assert record.summarization_provider == "fake"
    assert record.summarization_model == "test"
    assert (
        summarize_transcript(METADATA, transcript(), store, summarizer=provider)
        == result
    )
    assert provider.calls == 1
    summarize_transcript(METADATA, transcript(), store, summarizer=provider, force=True)
    assert provider.calls == 2


def test_export_is_separate_and_has_all_sections() -> None:
    metadata = replace(METADATA, chapters=(VideoChapter("Intro", 0.25, 7),))
    result = summary(metadata)
    markdown = summary_markdown(result)
    assert "## Short summary" in markdown
    assert "## Detailed summary" in markdown
    assert "## Key points" in markdown
    assert "[00:00:00.250 - 00:00:07.000] Intro" in markdown
    assert "Video claims:" in markdown
    assert '"A useful idea."' in markdown
    assert "Try it today." not in markdown
    assert "transcript_segments" not in summary_json(result)
    assert Summary.model_validate_json(summary_json(result)) == result
    result = result.model_copy(update={"notable_quotes": ()})
    assert "Notable quotes" not in summary_markdown(result)


def test_service_checks_limit_before_any_provider_work(store: CacheStore) -> None:
    provider = FakeSummarizer()
    size = prepare_input(METADATA, transcript()).size_bytes
    with pytest.raises(SummarizationError, match="single-call"):
        summarize_transcript(
            METADATA,
            transcript(),
            store,
            settings=replace(SETTINGS, summarizer_max_input_bytes=size - 1),
            summarizer=provider,
        )
    assert provider.calls == provider.preflights == 0
    assert not store.paths(VIDEO_ID).summary.exists()
    summarize_transcript(
        METADATA,
        transcript(),
        store,
        settings=replace(SETTINGS, summarizer_max_input_bytes=size),
        summarizer=provider,
    )
    assert provider.calls == 1
    with pytest.raises(SummarizationError, match="single-call"):
        summarize_transcript(
            METADATA,
            transcript(),
            store,
            settings=replace(SETTINGS, summarizer_max_input_bytes=1),
            summarizer=provider,
            force=True,
        )
    assert provider.calls == 1


@pytest.mark.parametrize(
    "failure", ["provider", "write", "replace", "cache", "grounding", "preflight"]
)
@pytest.mark.parametrize("existing", [False, True])
def test_service_failure_preserves_previous_artifacts(
    store: CacheStore, monkeypatch: pytest.MonkeyPatch, failure: str, existing: bool
) -> None:
    if existing:
        summarize_transcript(METADATA, transcript(), store, summarizer=FakeSummarizer())
    paths = store.paths(VIDEO_ID)
    before_record = store.lookup(VIDEO_ID)
    before_files = {
        path: path.read_bytes() if path.exists() else None
        for path in (paths.summary, paths.summary_json)
    }
    provider = FakeSummarizer(failure=failure == "provider")
    if failure == "cache":

        def fail_save(record: CacheRecord) -> CacheRecord:
            raise StorageError("cache failure")

        monkeypatch.setattr(store, "save", fail_save)
    elif failure == "write":
        original_write = Path.write_text

        def fail_write(path: Path, data: str, *args: object, **kwargs: object) -> int:
            if path.name == "new-1":
                raise OSError("write failed")
            return original_write(path, data, encoding="utf-8")

        monkeypatch.setattr(Path, "write_text", fail_write)
    elif failure == "replace":
        original_replace = Path.replace

        def fail_replace(path: Path, target: Path) -> Path:
            if path.name == "new-1":
                raise OSError("second replacement failed")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_replace)
    elif failure == "grounding":
        provider.result = summary().model_copy(
            update={
                "notable_quotes": (NotableQuote(quote="invented", timestamp_seconds=0),)
            }
        )
    elif failure == "preflight":

        def fail_preflight() -> None:
            raise SummarizationError("Missing config")

        monkeypatch.setattr(provider, "preflight", fail_preflight)
    with pytest.raises(SummarizationError):
        summarize_transcript(
            METADATA, transcript(), store, summarizer=provider, force=True
        )
    assert store.lookup(VIDEO_ID) == before_record
    for path, original in before_files.items():
        assert (path.read_bytes() if path.exists() else None) == original
    assert not list(paths.directory.glob(".summary-*"))


def test_missing_record_fails_before_provider(tmp_path: Path) -> None:
    provider = FakeSummarizer()
    with pytest.raises(SummarizationError, match="No cached video"):
        summarize_transcript(
            METADATA, transcript(), CacheStore(tmp_path), summarizer=provider
        )
    assert provider.calls == 0


def test_service_missing_openai_configuration_writes_nothing(
    store: CacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SummarizationError, match="OPENAI_API_KEY"):
        summarize_transcript(METADATA, transcript(), store)
    assert not store.paths(VIDEO_ID).summary.exists()
    assert not store.paths(VIDEO_ID).summary_json.exists()


def test_service_invalid_environment_limit_has_clear_error(
    store: CacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", "invalid")
    provider = FakeSummarizer()
    with pytest.raises(SummarizationError, match="MAX_INPUT_BYTES.*positive integer"):
        summarize_transcript(METADATA, transcript(), store, summarizer=provider)
    assert provider.calls == provider.preflights == 0
