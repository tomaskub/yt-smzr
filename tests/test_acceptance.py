"""Final frontend and default-adapter seams, with no network or model loading."""

import importlib
from collections.abc import Callable, Mapping
from pathlib import Path
from subprocess import CompletedProcess
from typing import Protocol

import pytest
from textual.widgets import Checkbox, Input, Static

from yt_smzr import cli
from yt_smzr.config import Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import Pipeline, PipelineEvent
from yt_smzr.storage.sqlite import CacheStore
from yt_smzr.summarization.base import Summary
from yt_smzr.transcription.base import Transcript, TranscriptSegment
from yt_smzr.tui import SummarizerApp
from yt_smzr.youtube.audio import extract_audio
from yt_smzr.youtube.metadata import extract_metadata

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://youtu.be/{VIDEO_ID}"
SECRET = "PRIVATE-DIAGNOSTIC-SENTINEL"


class Adapters:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    def preflight(self) -> None:
        pass

    def extract(self, url: str) -> Mapping[str, object]:
        self.calls.append("metadata")
        return {
            "id": VIDEO_ID,
            "title": "Acceptance video",
            "channel": "Channel",
            "duration": 20,
        }

    def download(self, url: str, directory: Path) -> Path:
        self.calls.append("download")
        path = directory / "audio.m4a"
        path.write_bytes(b"audio")
        return path

    def transcribe(self, audio_path: Path) -> Transcript:
        self.calls.append("transcription")
        assert audio_path.read_bytes() == b"audio"
        return Transcript(
            provider="fake",
            model="test",
            segments=(
                TranscriptSegment(
                    start_seconds=0.25, end_seconds=2, text="Unique spoken content."
                ),
            ),
        )

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary:
        self.calls.append("summarization")
        if self.fail:
            raise RuntimeError(SECRET)
        return Summary(
            provider="fake",
            model="test",
            short_summary="Useful notes.",
            detailed_summary="Detailed notes.",
            key_points=(),
            chapters=(),
            notable_claims=(),
            notable_quotes=(),
        )


async def test_tui_real_pipeline_confirmation_cache_and_failed_force(
    tmp_path: Path,
) -> None:
    adapters = Adapters()
    store = CacheStore(tmp_path / "output")

    def factory(on_event: Callable[[PipelineEvent], None]) -> Pipeline:
        return Pipeline(
            Settings(output_dir=store.output_dir),
            store=store,
            extractor=adapters.extract,
            downloader=adapters.download,
            audio_preflight=adapters.preflight,
            transcriber=adapters,
            summarizer=adapters,
            on_event=on_event,
        )

    app = SummarizerApp(pipeline_factory=factory)
    async with app.run_test(size=(100, 35)) as pilot:
        app.query_one("#url", Input).value = URL

        async def prepare() -> None:
            await pilot.click("#prepare")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()
            assert "Acceptance video" in str(app.query_one("#metadata", Static).content)

        async def confirm() -> None:
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()

        await prepare()
        assert adapters.calls == ["metadata"]
        assert not store.paths(VIDEO_ID).directory.exists()
        await confirm()
        assert "completion" in str(app.query_one("#stage", Static).content)
        assert "Useful notes." in str(app.query_one("#summary", Static).content)
        assert "Unique spoken content." in str(
            app.query_one("#transcript", Static).content
        )
        paths = store.paths(VIDEO_ID)
        before = {p.name: p.read_bytes() for p in paths.directory.iterdir()}
        record = store.lookup(VIDEO_ID)
        assert record is not None and record.run_state == "completed"
        assert "Unique spoken content." not in paths.summary.read_text()
        assert "segments" not in paths.summary_json.read_text()
        assert "00:00:00.250" in paths.transcript.read_text()
        for path in (
            paths.transcript,
            paths.transcript_json,
            paths.summary,
            paths.summary_json,
        ):
            assert str(path) in str(app.query_one("#paths", Static).content)
        adapters.calls.clear()
        await prepare()
        await confirm()
        assert adapters.calls == []
        record = store.lookup(VIDEO_ID)
        adapters.fail = True
        app.query_one("#force", Checkbox).value = True
        await prepare()
        await confirm()
        stage = str(app.query_one("#stage", Static).content)
        assert "summarization" in stage and SECRET not in stage
        assert "Useful notes." in str(app.query_one("#summary", Static).content)
        assert store.lookup(VIDEO_ID) == record
        assert {p.name: p.read_bytes() for p in paths.directory.iterdir()} == before
        assert not list(store.output_dir.glob(".pipeline-*"))
        assert adapters.calls == [
            "metadata",
            "download",
            "transcription",
            "summarization",
        ]


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("ffmpeg", "requires ffmpeg"),
        ("key", "OPENAI_API_KEY"),
        ("limit", "positive integer"),
        ("model", "TRANSCRIPTION_MODEL"),
    ],
)
def test_cli_default_preflight_configuration_before_external_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    message: str,
) -> None:
    monkeypatch.setenv("YT_SMZR_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("YT_SMZR_TRANSCRIPTION_MODEL", "small.en")
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_PROVIDER", "openai")
    monkeypatch.setenv("YT_SMZR_SUMMARIZATION_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", "100000")
    monkeypatch.setenv("OPENAI_API_KEY", "test-never-sent")
    if failure == "key":
        monkeypatch.delenv("OPENAI_API_KEY")
    elif failure == "limit":
        monkeypatch.setenv("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", SECRET)
    elif failure == "model":
        monkeypatch.setenv("YT_SMZR_TRANSCRIPTION_MODEL", " ")

    def which(name: str) -> str | None:
        return None if failure == "ffmpeg" else f"/fake/{name}"

    monkeypatch.setattr("shutil.which", which)

    def execute(args: list[str], **kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args, 0, stdout="deno 2.3.0")

    # Full default Pipeline and actual adapter preflights. Loading a model or
    # creating an OpenAI client must never be necessary to reject these runs.
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("preflight reached external work")

    monkeypatch.setattr("yt_dlp.YoutubeDL.extract_info", unexpected)
    monkeypatch.setattr("faster_whisper.WhisperModel", unexpected)
    monkeypatch.setattr("openai.OpenAI", unexpected)
    monkeypatch.setattr("subprocess.run", execute)
    assert cli.main(["summarize", URL, "--yes"]) == 1
    output = capsys.readouterr()
    assert "Error [preflight]" in output.err and message in output.err
    assert SECRET not in output.err
    assert "Traceback" not in output.err
    assert not (tmp_path / "output").exists()


class Diagnostics(Protocol):
    def report_warning(self, message: str) -> None: ...
    def report_error(self, message: str) -> None: ...


@pytest.mark.parametrize("stage", ["metadata", "download"])
def test_real_ytdlp_diagnostics_do_not_escape_cli_error_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    adapters = Adapters()

    def diagnostic(self: Diagnostics, url: str, *, download: bool) -> None:
        self.report_warning(SECRET)
        self.report_error(SECRET)

    monkeypatch.setattr(
        importlib.import_module("yt_dlp").YoutubeDL, "extract_info", diagnostic
    )

    def factory(*, on_event: Callable[[PipelineEvent], None]) -> Pipeline:
        return Pipeline(
            Settings(output_dir=tmp_path / "output"),
            extractor=extract_metadata if stage == "metadata" else adapters.extract,
            downloader=extract_audio,
            audio_preflight=adapters.preflight,
            transcriber=adapters,
            summarizer=adapters,
            on_event=on_event,
        )

    monkeypatch.setattr(cli, "Pipeline", factory)
    assert cli.main(["summarize", URL, "--yes"]) == 1
    output = capsys.readouterr()
    assert f"Error [{stage}]" in output.err
    assert (
        "Could not fetch YouTube video metadata" in output.err
        if stage == "metadata"
        else "Could not download or save YouTube audio" in output.err
    )
    assert SECRET not in output.out + output.err
    assert "Traceback" not in output.err
    assert not list((tmp_path / "output").glob(".pipeline-*"))
    assert not (tmp_path / "output" / "videos" / VIDEO_ID).exists()
