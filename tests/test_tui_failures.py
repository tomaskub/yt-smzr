"""Real pipeline failures retain caller context and render one TUI stage label."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from textual.widgets import Input, RichLog, Static

from yt_smzr.config import ConfigurationError, Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.pipeline import Pipeline, PipelineError, PipelineEvent, PipelineStage
from yt_smzr.storage.sqlite import CacheRecord, CacheStore, StorageError
from yt_smzr.summarization.base import Summary
from yt_smzr.transcription.base import Transcript, TranscriptionError
from yt_smzr.tui import SummarizerApp

URL = "https://youtu.be/dQw4w9WgXcQ"


class FailingTranscriber:
    def preflight(self) -> None:
        pass

    def transcribe(self, audio_path: Path) -> Transcript:
        raise TranscriptionError("faster-whisper could not transcribe audio.")


class UnusedSummarizer:
    def preflight(self) -> None:
        pass

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary:
        raise AssertionError("A failed transcription must not reach summarization")


def metadata(url: str) -> Mapping[str, object]:
    return {
        "id": "dQw4w9WgXcQ",
        "title": "A video",
        "channel": "A channel",
        "duration": 30,
    }


def download(url: str, directory: Path) -> Path:
    audio = directory / "audio.m4a"
    audio.write_bytes(b"audio")
    return audio


@pytest.mark.parametrize(
    ("failure", "stage", "message"),
    [
        (
            "prepare",
            PipelineStage.PREFLIGHT,
            "Summarization requires OPENROUTER_API_KEY.",
        ),
        (
            "process",
            PipelineStage.TRANSCRIPTION,
            "faster-whisper could not transcribe audio.",
        ),
        (
            "cache",
            PipelineStage.CACHE_EXPORT,
            "Could not read or save cache files or database during cache_export.",
        ),
        (
            "unexpected",
            PipelineStage.PREFLIGHT,
            "Could not complete preflight stage.",
        ),
        ("external", PipelineStage.PREFLIGHT, "Missing ffmpeg."),
    ],
)
async def test_pipeline_failures_render_once_in_status_and_real_event_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    stage: PipelineStage,
    message: str,
) -> None:
    store = CacheStore(tmp_path / "output")

    def preflight() -> None:
        if failure == "prepare":
            raise ConfigurationError(message)
        if failure == "unexpected":
            raise RuntimeError("private backend detail")
        if failure == "external":
            raise PipelineError(stage, message)

    pipeline = Pipeline(
        Settings(output_dir=store.output_dir),
        store=store,
        extractor=metadata,
        downloader=download,
        audio_preflight=preflight,
        transcriber=FailingTranscriber(),
        summarizer=UnusedSummarizer(),
    )

    def factory(callback: Callable[[PipelineEvent], None]) -> Pipeline:
        pipeline.on_event = callback
        return pipeline

    app = SummarizerApp(pipeline_factory=factory)
    async with app.run_test(size=(160, 40)) as pilot:
        app.query_one("#url", Input).value = URL
        await pilot.press("enter")
        await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
        await pilot.pause()
        if failure in {"process", "cache"}:
            if failure == "cache":

                def fail_lookup(video_id: str) -> CacheRecord | None:
                    raise StorageError("private database detail")

                monkeypatch.setattr(store, "lookup", fail_lookup)
            await pilot.click("#confirm")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()

        expected = f"{stage.value.replace('_', '/')}: Failed: {message}"
        assert str(app.query_one("#stage", Static).content) == expected
        lines = [line.text.rstrip() for line in app.query_one("#events", RichLog).lines]
        log_text = " ".join(lines)
        assert log_text.endswith(expected)
        assert log_text.count(expected) == 1
        assert "private" not in log_text
        if failure in {"prepare", "process"}:
            # The pipeline's failure event keeps its existing contextual string.
            assert f"failure: {stage.value}: {message}" in log_text


def test_contextual_error_string_and_plain_external_error_are_preserved() -> None:
    message = "Summarization requires OPENROUTER_API_KEY."

    def fail_preflight() -> None:
        raise ConfigurationError(message)

    with pytest.raises(PipelineError) as caught:
        Pipeline(Settings(), audio_preflight=fail_preflight).prepare(URL)
    error = caught.value
    assert str(error) == f"preflight: {message}"
    assert error.display_message == message
    assert error.stage == PipelineStage.PREFLIGHT

    external = PipelineError(PipelineStage.DOWNLOAD, "Missing audio.")
    assert str(external) == "Missing audio."
    assert external.display_message == "Missing audio."

    def external_failure() -> None:
        raise external

    with pytest.raises(PipelineError) as caught:
        Pipeline(Settings(), audio_preflight=external_failure).prepare(URL)
    assert caught.value is external
