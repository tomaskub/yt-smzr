"""CLI workflow checks with the real pipeline and fake external adapters."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from yt_smzr import cli
from yt_smzr.config import Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import Pipeline, PipelineEvent
from yt_smzr.storage.paths import artifact_paths
from yt_smzr.storage.sqlite import CacheRecord, CacheStore, StorageError
from yt_smzr.summarization.base import Summary
from yt_smzr.transcription.base import Transcript, TranscriptSegment
from yt_smzr.youtube.urls import parse_video_url

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://youtu.be/{VIDEO_ID}"
SECRET = "private-provider-error-token"


class FakeAdapters:
    def __init__(self, capsys: pytest.CaptureFixture[str]) -> None:
        self.capsys = capsys
        self.calls: list[str] = []
        self.failure: str | None = None
        self.duration = 20
        self.input_limit = 100_000
        self.before_download = ""

    def call(self, stage: str) -> None:
        self.calls.append(stage)
        if self.failure == stage:
            raise RuntimeError(SECRET)

    def preflight(self) -> None:
        self.call("preflight")

    def extract(self, url: str) -> Mapping[str, object]:
        self.call("metadata")
        assert parse_video_url(url).video_id == VIDEO_ID
        return {
            "id": VIDEO_ID,
            "title": "A video",
            "channel": "A channel",
            "duration": self.duration,
        }

    def download(self, url: str, directory: Path) -> Path:
        self.before_download += self.capsys.readouterr().out
        assert "Title: A video" in self.before_download
        assert "Channel: A channel" in self.before_download
        assert "Duration: 20 seconds" in self.before_download
        assert f"Video ID: {VIDEO_ID}" in self.before_download
        self.call("download")
        audio = directory / "audio.m4a"
        audio.write_bytes(b"fake compressed audio")
        return audio

    def transcribe(self, audio_path: Path) -> Transcript:
        self.call("transcription")
        assert audio_path.read_bytes() == b"fake compressed audio"
        return Transcript(
            provider="fake",
            model="fake",
            segments=(
                TranscriptSegment(start_seconds=0, end_seconds=1, text="Hello."),
            ),
        )

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary:
        self.call("summarization")
        return Summary(
            short_summary="A greeting.",
            detailed_summary="The video says hello.",
            key_points=("Hello.",),
            chapters=(),
            notable_claims=(),
            notable_quotes=(),
            provider="fake",
            model="fake",
        )


@pytest.fixture
def adapters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> FakeAdapters:
    fake = FakeAdapters(capsys)

    def create_pipeline(*, on_event: Callable[[PipelineEvent], None]) -> Pipeline:
        return Pipeline(
            Settings(
                output_dir=tmp_path / "output",
                summarizer_max_input_bytes=fake.input_limit,
            ),
            extractor=fake.extract,
            downloader=fake.download,
            audio_preflight=fake.preflight,
            transcriber=fake,
            summarizer=fake,
            on_event=on_event,
        )

    monkeypatch.setattr(cli, "Pipeline", create_pipeline)
    return fake


def answer(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    def confirm(prompt: str) -> str:
        assert prompt == "Process this video? [y/N] "
        return value

    monkeypatch.setattr("builtins.input", confirm)


@pytest.mark.parametrize("reply", ["", "n", "no", "maybe"])
def test_declining_does_no_processing_or_file_writes(
    adapters: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reply: str,
) -> None:
    answer(monkeypatch, reply)
    assert cli.main(["summarize", URL]) == 0
    assert "metadata" in adapters.calls
    assert not {"download", "transcription", "summarization"}.intersection(
        adapters.calls
    )
    assert not (tmp_path / "output").exists()
    captured = capsys.readouterr()
    assert "Title: A video" in captured.out
    assert "Cancelled" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize("reply", ["y", "yes", " YES "])
def test_confirmation_runs_shared_pipeline_and_prints_stages_and_paths(
    adapters: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    reply: str,
) -> None:
    def confirm(prompt: str) -> str:
        assert prompt == "Process this video? [y/N] "
        adapters.before_download += capsys.readouterr().out
        assert "Title: A video" in adapters.before_download
        assert f"Video ID: {VIDEO_ID}" in adapters.before_download
        assert "download" not in adapters.calls
        return reply

    monkeypatch.setattr("builtins.input", confirm)
    assert cli.main(["summarize", URL]) == 0
    assert {"download", "transcription", "summarization"}.issubset(adapters.calls)
    captured = capsys.readouterr()
    output = adapters.before_download + captured.out
    for stage in (
        "preflight",
        "metadata",
        "confirmation",
        "download",
        "transcription",
        "summarization",
        "cache_export",
        "completion",
    ):
        assert f"[{stage}]" in output
    paths = artifact_paths(tmp_path / "output", VIDEO_ID)
    for path in (
        paths.transcript,
        paths.transcript_json,
        paths.summary,
        paths.summary_json,
    ):
        assert str(path) in output
        assert path.is_file()
    assert captured.err == ""


def test_yes_skips_prompt_and_force_refreshes_without_cache_reuse(
    adapters: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_prompt(prompt: str) -> str:
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr("builtins.input", unexpected_prompt)
    assert cli.main(["summarize", URL, "--yes"]) == 0
    adapters.calls.clear()
    assert cli.main(["summarize", URL, "--yes"]) == 0
    assert not {"metadata", "download", "transcription", "summarization"}.intersection(
        adapters.calls
    )
    adapters.calls.clear()
    assert cli.main(["summarize", URL, "--yes", "--force"]) == 0
    assert {"metadata", "download", "transcription", "summarization"}.issubset(
        adapters.calls
    )


@pytest.mark.parametrize(
    "stage", ["preflight", "metadata", "download", "transcription", "summarization"]
)
def test_contextual_failures_exit_nonzero_without_raw_errors_or_success_paths(
    adapters: FakeAdapters,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    adapters.failure = stage
    assert cli.main(["summarize", URL, "--yes"]) == 1
    captured = capsys.readouterr()
    assert f"Error [{stage}]" in captured.err
    assert SECRET not in adapters.before_download + captured.out + captured.err
    assert "Traceback" not in captured.err
    assert "Transcript:" not in captured.out
    assert "Summary:" not in captured.out


@pytest.mark.parametrize("force", [False, True])
def test_force_does_not_override_duration_validation(
    adapters: FakeAdapters,
    capsys: pytest.CaptureFixture[str],
    force: bool,
) -> None:
    adapters.duration = 7201
    assert cli.main(["summarize", URL, "--yes", *(["--force"] if force else [])]) == 1
    assert "2-hour" in capsys.readouterr().err
    assert "download" not in adapters.calls


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_closed_input_and_keyboard_interrupt_never_start_processing(
    adapters: FakeAdapters,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interruption: type[BaseException],
) -> None:
    def closed_input(prompt: str) -> str:
        raise interruption()

    monkeypatch.setattr("builtins.input", closed_input)
    assert cli.main(["summarize", URL]) == (
        130 if interruption is KeyboardInterrupt else 0
    )
    assert "download" not in adapters.calls
    assert not (tmp_path / "output").exists()


def test_summarize_help_needs_no_pipeline_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as caught:
        cli.main(["summarize", "--help"])
    assert caught.value.code == 0
    assert "--yes" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("url", "message"),
    [("invalid", "URL"), (f"https://youtube.com/shorts/{VIDEO_ID}", "Shorts")],
)
def test_invalid_urls_have_actionable_cli_errors(
    adapters: FakeAdapters, capsys: pytest.CaptureFixture[str], url: str, message: str
) -> None:
    assert cli.main(["summarize", url, "--yes"]) == 1
    output = capsys.readouterr()
    assert message in output.err
    assert "Error [preflight]" in output.err
    assert "Traceback" not in output.err
    assert adapters.calls == []


def test_input_limit_cli_error_prevents_provider_call_and_removes_new_artifacts(
    adapters: FakeAdapters, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    adapters.input_limit = 1
    assert cli.main(["summarize", URL, "--yes"]) == 1
    output = capsys.readouterr()
    assert "Error [summarization]" in output.err
    assert "single-call" in output.err
    assert "summarization" not in adapters.calls
    assert not (tmp_path / "output" / "videos" / VIDEO_ID).exists()
    assert not list((tmp_path / "output").glob(".pipeline-*"))


def test_failed_write_cli_error_preserves_successful_files_and_cache(
    adapters: FakeAdapters,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli.main(["summarize", URL, "--yes"]) == 0
    capsys.readouterr()
    store = CacheStore(tmp_path / "output")
    paths = store.paths(VIDEO_ID)
    before = {p.name: p.read_bytes() for p in paths.directory.iterdir()}
    record = store.lookup(VIDEO_ID)

    def fail_metadata(self: CacheStore, metadata: VideoMetadata) -> CacheRecord:
        raise StorageError(SECRET)

    monkeypatch.setattr(CacheStore, "save_metadata", fail_metadata)
    assert cli.main(["summarize", URL, "--yes", "--force"]) == 1
    output = capsys.readouterr()
    assert "Error [cache_export]" in output.err
    assert "Could not read or save cache files or database" in output.err
    assert SECRET not in output.out + output.err
    assert "Transcript:" not in output.out
    assert store.lookup(VIDEO_ID) == record
    assert {p.name: p.read_bytes() for p in paths.directory.iterdir()} == before
    assert not list(store.output_dir.glob(".pipeline-*"))
