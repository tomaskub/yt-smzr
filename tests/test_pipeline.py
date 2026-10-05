"""Whole-workflow tests with fake extraction, download, inference, and providers."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from yt_smzr.config import Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import (
    Pipeline,
    PipelineError,
    PipelineEvent,
    PipelineResult,
    PipelineStage,
    PreparedVideo,
)
from yt_smzr.storage.sqlite import CacheRecord, CacheStore, StorageError
from yt_smzr.summarization.base import Summary
from yt_smzr.transcription.base import Transcript, TranscriptSegment

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://youtu.be/{VIDEO_ID}"
SECRET = "secret-provider-token"


class FakeTranscriber:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.failure: str | None = None
        self.text = "Hello world."

    def preflight(self) -> None:
        self.calls.append("transcription_preflight")
        if self.failure == "preflight":
            raise RuntimeError(SECRET)

    def transcribe(self, audio_path: Path) -> Transcript:
        self.calls.append("transcribe")
        assert audio_path.read_bytes() == b"new audio"
        if self.failure == "transcribe":
            raise RuntimeError(SECRET)
        return Transcript(
            provider="fake-whisper",
            model="test-transcription",
            segments=(
                TranscriptSegment(start_seconds=0.25, end_seconds=2, text=self.text),
            ),
        )


class FakeSummarizer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.failure: str | None = None
        self.text = "A useful summary."

    def preflight(self) -> None:
        self.calls.append("summary_preflight")
        if self.failure == "preflight":
            raise RuntimeError(SECRET)

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary:
        self.calls.append("summarize")
        assert metadata.video_id == VIDEO_ID
        assert transcript.segments
        if self.failure == "summarize":
            raise RuntimeError(SECRET)
        return Summary(
            short_summary=self.text,
            detailed_summary="The video says hello.",
            key_points=("Hello.",),
            chapters=(),
            notable_claims=(),
            notable_quotes=(),
            provider="fake-openai",
            model="test-summary",
        )


class Workflow:
    def __init__(self, tmp_path: Path) -> None:
        self.calls: list[str] = []
        self.events: list[PipelineEvent] = []
        self.store = CacheStore(tmp_path / "output")
        self.settings = Settings(output_dir=self.store.output_dir)
        self.transcriber = FakeTranscriber(self.calls)
        self.summarizer = FakeSummarizer(self.calls)
        self.failure: str | None = None
        self.duration: float = 20
        self.title = "A video"
        self.pipeline = Pipeline(
            self.settings,
            store=self.store,
            extractor=self.extract,
            downloader=self.download,
            audio_preflight=self.preflight,
            transcriber=self.transcriber,
            summarizer=self.summarizer,
            on_event=self.events.append,
        )

    def preflight(self) -> None:
        self.calls.append("audio_preflight")
        if self.failure == "preflight":
            raise RuntimeError(SECRET)

    def extract(self, url: str) -> Mapping[str, object]:
        self.calls.append("metadata")
        if self.failure == "metadata":
            raise RuntimeError(SECRET)
        return {
            "id": VIDEO_ID,
            "title": self.title,
            "channel": "A channel",
            "duration": self.duration,
        }

    def download(self, url: str, directory: Path) -> Path:
        self.calls.append("download")
        audio = directory / "audio.m4a"
        audio.write_bytes(b"new audio")
        if self.failure == "download":
            raise RuntimeError(SECRET)
        return audio

    def run(self, *, force: bool = False) -> PipelineResult:
        return self.pipeline.process(
            self.pipeline.prepare(URL, force=force), confirmed=True
        )

    def files(self) -> dict[str, bytes]:
        directory = self.store.paths(VIDEO_ID).directory
        return (
            {
                path.name: path.read_bytes()
                for path in directory.iterdir()
                if path.is_file()
            }
            if directory.exists()
            else {}
        )

    def assert_clean(self) -> None:
        assert not list(self.store.output_dir.glob(".pipeline-*"))


@pytest.fixture
def workflow(tmp_path: Path) -> Workflow:
    return Workflow(tmp_path)


def test_happy_path_full_frontend_contract_and_events(workflow: Workflow) -> None:
    prepared = workflow.pipeline.prepare(URL)
    assert workflow.calls == [
        "audio_preflight",
        "transcription_preflight",
        "summary_preflight",
        "metadata",
    ]
    assert workflow.files() == {}
    result = workflow.pipeline.run(prepared, confirmed=True)
    assert result.metadata == prepared.metadata
    assert result.transcript.segments[0].start_seconds == 0.25
    assert result.summary.short_summary == "A useful summary."
    assert result.cache_record == workflow.store.lookup(VIDEO_ID)
    assert result.cache_record.run_state == "completed"
    assert result.cache_record.transcription_model == "test-transcription"
    assert result.cache_record.summarization_model == "test-summary"
    assert result.audio_path == result.paths.audio("m4a")
    assert not result.cache_hit
    assert result.reused_stages == ()
    assert set(workflow.files()) == {
        "metadata.json",
        "audio.m4a",
        "transcript.md",
        "transcript.json",
        "summary.md",
        "summary.json",
    }
    assert [event.stage for event in workflow.events] == [
        PipelineStage.PREFLIGHT,
        PipelineStage.METADATA,
        PipelineStage.CONFIRMATION,
        PipelineStage.DOWNLOAD,
        PipelineStage.TRANSCRIPTION,
        PipelineStage.SUMMARIZATION,
        PipelineStage.CACHE_EXPORT,
        PipelineStage.COMPLETION,
    ]
    for path in (
        result.cache_record.metadata_path,
        result.cache_record.audio_path,
        result.cache_record.transcript_path,
        result.cache_record.transcript_json_path,
        result.cache_record.summary_path,
        result.cache_record.summary_json_path,
    ):
        assert path is not None and path.is_file()
        assert ".pipeline-" not in str(path)
    workflow.assert_clean()


def test_declined_confirmation_does_no_audio_or_provider_work(
    workflow: Workflow,
) -> None:
    prepared = workflow.pipeline.prepare(URL)
    before = list(workflow.calls)
    with pytest.raises(PipelineError, match="Confirm") as caught:
        workflow.pipeline.process(prepared, confirmed=False)
    assert caught.value.stage == PipelineStage.CONFIRMATION
    assert workflow.calls == before
    assert workflow.store.lookup(VIDEO_ID) is None
    assert workflow.files() == {}
    assert workflow.events[-1].stage == PipelineStage.FAILURE
    workflow.assert_clean()


def test_full_cache_reuses_all_work_but_checks_preflight_and_metadata(
    workflow: Workflow,
) -> None:
    first = workflow.run()
    before_files = workflow.files()
    workflow.calls.clear()
    second = workflow.run()
    assert second.cache_hit
    assert second.summary == first.summary
    assert second.transcript == first.transcript
    assert second.cache_record.created_at == first.cache_record.created_at
    assert workflow.files() == before_files
    assert "metadata" not in workflow.calls
    assert "audio_preflight" in workflow.calls
    assert "summary_preflight" in workflow.calls
    assert not {"download", "transcribe", "summarize"}.intersection(workflow.calls)
    workflow.assert_clean()


@pytest.mark.parametrize("missing", ["audio", "transcript", "summary", "markdown"])
def test_partial_cache_and_missing_markdown_recover_without_unnecessary_calls(
    workflow: Workflow, missing: str
) -> None:
    first = workflow.run()
    if missing == "audio":
        first.audio_path.unlink()
    elif missing == "transcript":
        first.paths.transcript_json.unlink()
    elif missing == "summary":
        first.paths.summary_json.unlink()
    else:
        first.paths.transcript.unlink()
        first.paths.summary.unlink()
    workflow.calls.clear()
    result = workflow.run()
    work_calls = set(workflow.calls).intersection(
        {"download", "transcribe", "summarize"}
    )
    assert (
        work_calls
        == {
            "audio": {"download"},
            "transcript": {"transcribe"},
            "summary": {"summarize"},
            "markdown": set(),
        }[missing]
    )
    assert result.paths.transcript.is_file()
    assert result.paths.summary.is_file()
    assert result.cache_hit == (missing == "markdown")
    workflow.assert_clean()


@pytest.mark.parametrize("artifact", ["transcript", "summary"])
def test_corrupt_json_is_a_cache_miss(workflow: Workflow, artifact: str) -> None:
    first = workflow.run()
    path = (
        first.paths.transcript_json
        if artifact == "transcript"
        else first.paths.summary_json
    )
    path.write_text("not valid JSON")
    workflow.calls.clear()
    workflow.run()
    assert ("transcribe" if artifact == "transcript" else "summarize") in workflow.calls
    workflow.assert_clean()


def test_force_refresh_keeps_old_outputs_until_commit(workflow: Workflow) -> None:
    first = workflow.run()
    before = workflow.files()
    original_summary = workflow.summarizer.summarize

    def inspect(metadata: VideoMetadata, transcript: Transcript) -> Summary:
        assert workflow.store.lookup(VIDEO_ID) == first.cache_record
        assert workflow.files() == before
        return original_summary(metadata, transcript)

    workflow.summarizer.summarize = inspect
    workflow.title = "A refreshed title"
    workflow.transcriber.text = "New speech."
    workflow.summarizer.text = "A refreshed summary."
    workflow.calls.clear()
    result = workflow.run(force=True)
    assert {"download", "transcribe", "summarize"}.issubset(workflow.calls)
    assert result.reused_stages == ()
    assert result.metadata.title == "A refreshed title"
    assert result.transcript.segments[0].text == "New speech."
    assert result.summary.short_summary == "A refreshed summary."
    assert result.cache_record.created_at == first.cache_record.created_at
    assert result.cache_record.updated_at != first.cache_record.updated_at
    assert workflow.files() != before
    workflow.assert_clean()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    ("failure", "stage"),
    [
        ("metadata", PipelineStage.METADATA),
        ("download", PipelineStage.DOWNLOAD),
        ("transcribe", PipelineStage.TRANSCRIPTION),
        ("summarize", PipelineStage.SUMMARIZATION),
        ("metadata_write", PipelineStage.CACHE_EXPORT),
        ("transcript_write", PipelineStage.TRANSCRIPTION),
        ("summary_write", PipelineStage.SUMMARIZATION),
        ("publish", PipelineStage.CACHE_EXPORT),
        ("database", PipelineStage.CACHE_EXPORT),
    ],
)
def test_failed_runs_preserve_previous_record_and_every_file(
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
    failure: str,
    stage: PipelineStage,
) -> None:
    if existing:
        workflow.run()
    before_record = workflow.store.lookup(VIDEO_ID)
    before_files = workflow.files()
    workflow.title = "Replacement title"
    if failure in {"metadata", "download"}:
        workflow.failure = failure
    elif failure == "transcribe":
        workflow.transcriber.failure = failure
    elif failure == "summarize":
        workflow.summarizer.failure = failure
    elif failure == "metadata_write":

        def fail_metadata(self: CacheStore, metadata: VideoMetadata) -> CacheRecord:
            raise StorageError(SECRET)

        monkeypatch.setattr(CacheStore, "save_metadata", fail_metadata)
    elif failure in {"transcript_write", "summary_write"}:
        original_write = Path.write_text

        def fail_write(path: Path, data: str, *, encoding: str | None = None) -> int:
            prefix = ".transcript-" if failure == "transcript_write" else ".summary-"
            if path.parent.name.startswith(prefix):
                raise OSError(SECRET)
            return original_write(path, data, encoding=encoding)

        monkeypatch.setattr(Path, "write_text", fail_write)
    elif failure == "publish":
        original_replace = Path.replace

        def fail_publish(path: Path, target: Path) -> Path:
            if path == workflow.store.paths(VIDEO_ID).directory:
                return original_replace(path, target)
            if path.name == VIDEO_ID and path.parent.parent.name == "staged":
                raise OSError(SECRET)
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_publish)
    else:

        def fail_save(record: CacheRecord) -> CacheRecord:
            raise StorageError(SECRET)

        monkeypatch.setattr(workflow.store, "save", fail_save)
    with pytest.raises(PipelineError) as caught:
        workflow.run(force=True)
    assert caught.value.stage == stage
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert SECRET not in " ".join(event.message for event in workflow.events)
    assert workflow.store.lookup(VIDEO_ID) == before_record
    assert workflow.files() == before_files
    workflow.assert_clean()


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("duration", [7201, 0, float("inf")])
def test_duration_limit_applies_to_cached_and_forced_videos(
    workflow: Workflow,
    force: bool,
    duration: float,
) -> None:
    workflow.run()
    before = workflow.files()
    record = workflow.store.lookup(VIDEO_ID)
    workflow.duration = duration
    # Cached metadata must also be validated; malformed duration falls back to
    # fresh extraction, which independently enforces the same cap.
    paths = workflow.store.paths(VIDEO_ID)
    paths.metadata.write_text(
        paths.metadata.read_text().replace(
            '"duration_seconds": 20.0', f'"duration_seconds": {duration}'
        )
    )
    before = workflow.files()
    workflow.calls.clear()
    with pytest.raises(PipelineError, match="duration"):
        workflow.run(force=force)
    assert not {"download", "transcribe", "summarize"}.intersection(workflow.calls)
    assert workflow.files() == before
    assert workflow.store.lookup(VIDEO_ID) == record


@pytest.mark.parametrize("force", [False, True])
def test_input_limit_runs_before_provider_even_when_cached_or_forced(
    workflow: Workflow,
    force: bool,
) -> None:
    workflow.run()
    before = workflow.files()
    record = workflow.store.lookup(VIDEO_ID)
    workflow.pipeline.settings = replace(
        workflow.settings, summarizer_max_input_bytes=1
    )
    workflow.calls.clear()
    with pytest.raises(PipelineError, match="single-call") as caught:
        workflow.run(force=force)
    assert caught.value.stage == PipelineStage.SUMMARIZATION
    assert "summarize" not in workflow.calls
    assert workflow.files() == before
    assert workflow.store.lookup(VIDEO_ID) == record
    workflow.assert_clean()


@pytest.mark.parametrize("which", ["audio", "transcriber", "summarizer"])
def test_all_preflights_run_before_metadata(workflow: Workflow, which: str) -> None:
    if which == "audio":
        workflow.failure = "preflight"
    elif which == "transcriber":
        workflow.transcriber.failure = "preflight"
    else:
        workflow.summarizer.failure = "preflight"
    with pytest.raises(PipelineError) as caught:
        workflow.pipeline.prepare(URL)
    assert caught.value.stage == PipelineStage.PREFLIGHT
    assert SECRET not in str(caught.value)
    assert "metadata" not in workflow.calls
    assert workflow.files() == {}


def test_default_openai_configuration_is_required_before_metadata(
    workflow: Workflow,
) -> None:
    workflow.pipeline.summarizer = None
    with pytest.raises(PipelineError, match="OPENAI_API_KEY"):
        workflow.pipeline.prepare(URL)
    assert "metadata" not in workflow.calls


def test_invalid_environment_configuration_is_contextual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", SECRET)
    pipeline = Pipeline()
    with pytest.raises(PipelineError, match="positive integer") as caught:
        pipeline.prepare(URL)
    assert caught.value.stage == PipelineStage.PREFLIGHT
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("url", [f"https://youtube.com/shorts/{VIDEO_ID}", "invalid"])
def test_invalid_urls_never_start_external_work(
    workflow: Workflow,
    force: bool,
    url: str,
) -> None:
    with pytest.raises(PipelineError):
        workflow.pipeline.prepare(url, force=force)
    assert workflow.calls == []


def test_manually_constructed_prepared_metadata_still_enforces_limits(
    workflow: Workflow,
) -> None:
    prepared = workflow.pipeline.prepare(URL, force=True)
    invalid = PreparedVideo(replace(prepared.metadata, duration_seconds=7201), True)
    workflow.calls.clear()
    with pytest.raises(PipelineError, match="2-hour"):
        workflow.pipeline.process(invalid, confirmed=True)
    assert workflow.calls == []


def test_observer_errors_cannot_break_committed_success(workflow: Workflow) -> None:
    def broken(event: PipelineEvent) -> None:
        raise RuntimeError(SECRET)

    workflow.pipeline.on_event = broken
    result = workflow.run()
    assert result.cache_record.run_state == "completed"
    assert workflow.store.lookup(VIDEO_ID) == result.cache_record
    workflow.assert_clean()


def test_cleanup_failure_after_commit_does_not_report_failure(
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow.run()
    from yt_smzr import pipeline as module

    original = module.shutil.rmtree

    def fail_cleanup(
        path: Path | str,
        *,
        ignore_errors: bool = False,
        onerror: object = None,
        onexc: object = None,
        dir_fd: int | None = None,
    ) -> None:
        if Path(path).name.startswith(".pipeline-"):
            raise OSError("cleanup failed")
        original(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(module.shutil, "rmtree", fail_cleanup)
    result = workflow.run(force=True)
    assert workflow.store.lookup(VIDEO_ID) == result.cache_record
    assert workflow.events[-1].stage == PipelineStage.COMPLETION


def test_failed_rollback_preserves_only_previous_backup(
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = workflow.run()
    before = workflow.files()
    original = Path.replace

    def fail_restore(path: Path, target: Path) -> Path:
        if path.name == "previous-video":
            raise OSError("restore blocked")
        return original(path, target)

    def fail_save(record: CacheRecord) -> CacheRecord:
        raise StorageError(SECRET)

    monkeypatch.setattr(Path, "replace", fail_restore)
    monkeypatch.setattr(workflow.store, "save", fail_save)
    with pytest.raises(PipelineError, match="retained at") as caught:
        workflow.run(force=True)
    assert caught.value.stage == PipelineStage.CACHE_EXPORT
    assert workflow.store.lookup(VIDEO_ID) == first.cache_record
    backups = list(workflow.store.output_dir.glob(".pipeline-*/previous-video"))
    assert len(backups) == 1
    assert {p.name: p.read_bytes() for p in backups[0].iterdir()} == before


@pytest.mark.parametrize(
    "problem", ["missing", "corrupt", "wrong_video", "invalid_chapter"]
)
def test_unusable_metadata_cache_falls_back_to_extraction(
    workflow: Workflow,
    problem: str,
) -> None:
    first = workflow.run()
    if problem == "missing":
        first.paths.metadata.unlink()
    elif problem == "corrupt":
        first.paths.metadata.write_text("invalid JSON")
    elif problem == "wrong_video":
        first.paths.metadata.write_text(
            first.paths.metadata.read_text().replace(VIDEO_ID, "aaaaaaaaaaa")
        )
    else:
        first.paths.metadata.write_text(
            first.paths.metadata.read_text().replace(
                '"chapters": []',
                '"chapters": [{"title":"Bad", "start_seconds":-1, "end_seconds":2}]',
            )
        )
    workflow.calls.clear()
    result = workflow.run()
    assert "metadata" in workflow.calls
    assert not {"download", "transcribe", "summarize"}.intersection(workflow.calls)
    assert PipelineStage.METADATA not in result.reused_stages
    workflow.assert_clean()


def test_cached_metadata_uses_current_input_url_and_enforces_force(
    workflow: Workflow,
) -> None:
    workflow.run()
    workflow.calls.clear()
    watch_url = f"https://www.youtube.com/watch?v={VIDEO_ID}"
    prepared = workflow.pipeline.prepare(watch_url)
    assert prepared.metadata.original_url == watch_url
    assert prepared.metadata_cached
    assert "metadata" not in workflow.calls
    prepared = workflow.pipeline.prepare(watch_url, force=True)
    assert not prepared.metadata_cached
    assert "metadata" in workflow.calls


def test_cache_read_failure_has_storage_context_and_no_provider_output(
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_lookup(video_id: str) -> CacheRecord | None:
        raise StorageError(SECRET)

    monkeypatch.setattr(workflow.store, "lookup", fail_lookup)
    with pytest.raises(PipelineError, match="cache files or database") as caught:
        workflow.pipeline.prepare(URL)
    assert caught.value.stage == PipelineStage.METADATA
    assert SECRET not in str(caught.value)
    assert "metadata" not in workflow.calls


def test_missing_ffmpeg_is_reported_before_metadata(
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    from yt_smzr.youtube.audio import check_audio_dependencies

    def missing_executable(name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", missing_executable)
    workflow.pipeline.audio_preflight = check_audio_dependencies
    with pytest.raises(PipelineError, match="ffmpeg") as caught:
        workflow.pipeline.prepare(URL)
    assert caught.value.stage == PipelineStage.PREFLIGHT
    assert "metadata" not in workflow.calls


def test_invalid_default_transcription_model_fails_before_metadata(
    workflow: Workflow,
) -> None:
    workflow.pipeline.transcriber = None
    workflow.pipeline.settings = replace(workflow.settings, transcription_model=" ")
    with pytest.raises(PipelineError, match="TRANSCRIPTION_MODEL") as caught:
        workflow.pipeline.prepare(URL)
    assert caught.value.stage == PipelineStage.PREFLIGHT
    assert "metadata" not in workflow.calls


def test_unconfirmed_prepare_does_not_create_output_directory(
    workflow: Workflow,
    tmp_path: Path,
) -> None:
    output = tmp_path / "uncreated"
    pipeline = Pipeline(
        replace(workflow.settings, output_dir=output),
        extractor=workflow.extract,
        downloader=workflow.download,
        audio_preflight=workflow.preflight,
        transcriber=workflow.transcriber,
        summarizer=workflow.summarizer,
    )
    prepared = pipeline.prepare(URL)
    assert not output.exists()
    with pytest.raises(PipelineError, match="Confirm"):
        pipeline.process(prepared, confirmed=False)
    assert not output.exists()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_publication_interruption_restores_record_and_active_artifacts(
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
    interruption: type[BaseException],
) -> None:
    if existing:
        workflow.run()
    before_record = workflow.store.lookup(VIDEO_ID)
    before_files = workflow.files()
    workflow.transcriber.text = "Interrupted replacement speech."
    workflow.summarizer.text = "Interrupted replacement summary."

    def interrupt_save(record: CacheRecord) -> CacheRecord:
        raise interruption()

    monkeypatch.setattr(workflow.store, "save", interrupt_save)
    with pytest.raises(interruption):
        workflow.run(force=True)
    assert workflow.store.lookup(VIDEO_ID) == before_record
    assert workflow.files() == before_files
    workflow.assert_clean()


def test_unsupported_cached_audio_is_downloaded_again(workflow: Workflow) -> None:
    first = workflow.run()
    bad_audio = first.audio_path.with_suffix(".txt")
    first.audio_path.replace(bad_audio)
    workflow.store.save(replace(first.cache_record, audio_path=bad_audio))
    workflow.calls.clear()
    result = workflow.run()
    assert "download" in workflow.calls
    assert "transcribe" not in workflow.calls
    assert "summarize" not in workflow.calls
    assert result.audio_path.suffix == ".m4a"
    assert PipelineStage.DOWNLOAD not in result.reused_stages
    workflow.assert_clean()
