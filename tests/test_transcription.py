"""No model downloads: test the provider and publication seams with fakes."""

import importlib.util
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from yt_smzr.config import Settings
from yt_smzr.storage.export import timestamp, transcript_json, transcript_markdown
from yt_smzr.storage.sqlite import CacheRecord, CacheStore, StorageError
from yt_smzr.transcription.base import Transcript, TranscriptionError, TranscriptSegment
from yt_smzr.transcription.faster_whisper import (
    FasterWhisperTranscriber,
    WhisperModel,
    WhisperSegment,
)
from yt_smzr.transcription.service import transcribe_audio

VIDEO_ID = "dQw4w9WgXcQ"


def sample_transcript(text: str = "Hello world.") -> Transcript:
    return Transcript(
        provider="fake",
        model="test-model",
        segments=(
            TranscriptSegment(start_seconds=0.125, end_seconds=2.75, text=text),
            TranscriptSegment(start_seconds=3600.5, end_seconds=3601, text="Bye."),
        ),
    )


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        (-1, 2, "hi"),
        (1, 0, "hi"),
        (float("nan"), 2, "hi"),
        (0, float("inf"), "hi"),
        (0, 1, ""),
        (0, 1, " "),
    ],
)
def test_segment_validation(start: float, end: float, text: str) -> None:
    with pytest.raises(ValidationError):
        TranscriptSegment(start_seconds=start, end_seconds=end, text=text)


@pytest.mark.parametrize("start", [0, 1.5])
def test_segments_cannot_reorder_or_overlap(start: float) -> None:
    with pytest.raises(ValidationError, match="ordered"):
        Transcript(
            provider="fake",
            model="test",
            segments=(
                TranscriptSegment(start_seconds=1, end_seconds=2, text="A"),
                TranscriptSegment(start_seconds=start, end_seconds=3, text="B"),
            ),
        )


def test_transcript_requires_speech_and_english() -> None:
    with pytest.raises(ValidationError):
        Transcript(provider="fake", model="test", segments=())
    with pytest.raises(ValidationError):
        Transcript(
            provider="fake",
            model="test",
            language="fr",
            segments=sample_transcript().segments,
        )
    with pytest.raises(ValidationError):
        Transcript(provider=" ", model="test", segments=sample_transcript().segments)


def test_exports_preserve_timestamps() -> None:
    transcript = sample_transcript()
    markdown = transcript_markdown(transcript)
    assert "[00:00:00.125 - 00:00:02.750] Hello world." in markdown
    assert "[01:00:00.500 - 01:00:01.000] Bye." in markdown
    assert "Model: test-model" in markdown
    assert Transcript.model_validate_json(transcript_json(transcript)) == transcript
    assert json.loads(transcript_json(transcript))["segments"][0] == {
        "start_seconds": 0.125,
        "end_seconds": 2.75,
        "text": "Hello world.",
    }
    assert timestamp(59.9999) == "00:01:00.000"


@dataclass
class FakeWhisperSegment:
    start: float
    end: float
    text: str


class FakeModel:
    def __init__(self, failure: bool = False) -> None:
        self.failure = failure

    def transcribe(
        self, audio: str, *, language: str, log_progress: bool
    ) -> tuple[Iterator[WhisperSegment], object]:
        assert audio == "audio.m4a"
        assert language == "en"
        assert log_progress is False
        return self.segments(), object()

    def segments(self) -> Iterator[WhisperSegment]:
        yield FakeWhisperSegment(0.25, 1.5, " hello ")
        if self.failure:
            raise RuntimeError("inference failed during iteration")
        yield FakeWhisperSegment(2, 3.75, "world")


def test_default_adapter_materializes_segments() -> None:
    calls: list[tuple[str, str, str]] = []

    def factory(model: str, *, device: str, compute_type: str) -> WhisperModel:
        calls.append((model, device, compute_type))
        return FakeModel()

    backend = FasterWhisperTranscriber(Settings(), factory=factory)
    backend.preflight()
    assert calls == []
    transcript = backend.transcribe(Path("audio.m4a"))
    assert calls == [("small.en", "cpu", "int8")]
    assert transcript.provider == "faster-whisper"
    assert transcript.segments[0].start_seconds == 0.25
    assert transcript.segments[1].end_seconds == 3.75
    assert transcript.segments[0].text == "hello"


@pytest.mark.parametrize("failure", ["load", "call", "iterate", "invalid"])
def test_adapter_wraps_backend_failures(failure: str) -> None:
    class BrokenModel(FakeModel):
        def transcribe(
            self, audio: str, *, language: str, log_progress: bool
        ) -> tuple[Iterator[WhisperSegment], object]:
            if failure == "call":
                raise OSError("decode failure")
            return super().transcribe(
                audio, language=language, log_progress=log_progress
            )

        def segments(self) -> Iterator[WhisperSegment]:
            if failure == "invalid":
                yield FakeWhisperSegment(-1, 2, "bad")
            else:
                yield from super().segments()

    def factory(model: str, *, device: str, compute_type: str) -> WhisperModel:
        if failure == "load":
            raise OSError("model failure")
        return BrokenModel(failure=failure == "iterate")

    with pytest.raises(TranscriptionError, match="faster-whisper") as caught:
        FasterWhisperTranscriber(factory=factory).transcribe(Path("audio.m4a"))
    assert caught.value.__cause__ is not None


def test_preflight_checks_config_and_dependency_without_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(TranscriptionError, match="must not be empty"):
        FasterWhisperTranscriber(Settings(transcription_model=" ")).preflight()

    def missing_module(name: str) -> None:
        return None

    monkeypatch.setattr(importlib.util, "find_spec", missing_module)
    with pytest.raises(TranscriptionError, match="run uv sync"):
        FasterWhisperTranscriber().preflight()


def test_transcription_model_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YT_SMZR_TRANSCRIPTION_MODEL", "tiny.en")
    assert Settings.from_env().transcription_model == "tiny.en"


class FakeTranscriber:
    def __init__(self, text: str = "Hello world.", failure: bool = False) -> None:
        self.text = text
        self.failure = failure
        self.calls = 0

    def preflight(self) -> None:
        pass

    def transcribe(self, audio_path: Path) -> Transcript:
        assert audio_path.is_file()
        self.calls += 1
        if self.failure:
            raise RuntimeError("backend failed")
        return sample_transcript(self.text)


@pytest.fixture
def store(tmp_path: Path) -> CacheStore:
    cache = CacheStore(tmp_path)
    audio = cache.paths(VIDEO_ID).audio("m4a")
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"compressed audio")
    cache.save(CacheRecord(VIDEO_ID, "url", "title", "channel", 10, audio_path=audio))
    return cache


def test_service_saves_paths_provider_and_model_and_reuses(store: CacheStore) -> None:
    backend = FakeTranscriber()
    transcript = transcribe_audio(VIDEO_ID, store, transcriber=backend)
    record = store.lookup(VIDEO_ID)
    assert record is not None
    assert record.transcript_path == store.paths(VIDEO_ID).transcript
    assert record.transcript_json_path == store.paths(VIDEO_ID).transcript_json
    assert record.transcription_provider == "fake"
    assert record.transcription_model == "test-model"
    assert record.audio_path is not None
    assert record.transcript_path is not None
    assert record.transcript_json_path is not None
    assert record.transcript_path.read_text() == transcript_markdown(transcript)
    assert record.transcript_json_path.read_text() == transcript_json(transcript)
    assert transcribe_audio(VIDEO_ID, store, transcriber=backend) == transcript
    assert backend.calls == 1
    transcribe_audio(VIDEO_ID, store, transcriber=backend, force=True)
    assert backend.calls == 2


@pytest.mark.parametrize("failure", ["backend", "write", "replace", "cache"])
@pytest.mark.parametrize("existing", [False, True])
def test_service_failure_preserves_previous_artifacts(
    store: CacheStore, monkeypatch: pytest.MonkeyPatch, failure: str, existing: bool
) -> None:
    if existing:
        transcribe_audio(VIDEO_ID, store, transcriber=FakeTranscriber())
    paths = store.paths(VIDEO_ID)
    before_record = store.lookup(VIDEO_ID)
    before_files = {
        path: path.read_bytes() if path.exists() else None
        for path in (paths.transcript, paths.transcript_json)
    }
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
    with pytest.raises(TranscriptionError):
        transcribe_audio(
            VIDEO_ID,
            store,
            force=True,
            transcriber=FakeTranscriber("replacement", failure == "backend"),
        )
    assert store.lookup(VIDEO_ID) == before_record
    for path, content in before_files.items():
        assert (path.read_bytes() if path.exists() else None) == content
    assert not list(paths.directory.glob(".transcript-*"))


def test_missing_audio_fails_before_backend(store: CacheStore) -> None:
    backend = FakeTranscriber()
    record = store.lookup(VIDEO_ID)
    assert record is not None and record.audio_path is not None
    record.audio_path.unlink()
    with pytest.raises(TranscriptionError, match="missing or empty"):
        transcribe_audio(VIDEO_ID, store, transcriber=backend)
    assert backend.calls == 0


def test_missing_record_fails(tmp_path: Path) -> None:
    with pytest.raises(TranscriptionError, match="No cached video"):
        transcribe_audio(VIDEO_ID, CacheStore(tmp_path), transcriber=FakeTranscriber())
