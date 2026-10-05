"""Synchronous frontend-neutral workflow with whole-run staged publication."""

import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from tempfile import mkdtemp

from pydantic import TypeAdapter, ValidationError

from yt_smzr.config import ConfigurationError, Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.storage.export import (
    summary_json,
    summary_markdown,
    transcript_json,
    transcript_markdown,
)
from yt_smzr.storage.paths import ArtifactPaths
from yt_smzr.storage.sqlite import CacheRecord, CacheStore, StorageError
from yt_smzr.summarization.base import (
    SummarizationError,
    Summarizer,
    Summary,
    validate_grounding,
)
from yt_smzr.summarization.factory import create_summarizer
from yt_smzr.summarization.prompts import check_input_limit, prepare_input
from yt_smzr.summarization.service import summarize_transcript
from yt_smzr.transcription.base import Transcriber, Transcript, TranscriptionError
from yt_smzr.transcription.faster_whisper import FasterWhisperTranscriber
from yt_smzr.transcription.service import transcribe_audio
from yt_smzr.youtube.audio import (
    AudioDownloader,
    AudioError,
    Preflight,
    check_audio_dependencies,
    download_audio,
    extract_audio,
)
from yt_smzr.youtube.metadata import (
    MAX_DURATION_SECONDS,
    MetadataError,
    MetadataExtractor,
    extract_metadata,
    fetch_metadata,
)
from yt_smzr.youtube.urls import InvalidYouTubeURL, parse_video_url


class PipelineStage(StrEnum):
    PREFLIGHT = "preflight"
    METADATA = "metadata"
    CONFIRMATION = "confirmation"
    DOWNLOAD = "download"
    TRANSCRIPTION = "transcription"
    SUMMARIZATION = "summarization"
    CACHE_EXPORT = "cache_export"
    COMPLETION = "completion"
    FAILURE = "failure"


@dataclass(frozen=True)
class PipelineEvent:
    stage: PipelineStage
    message: str


class PipelineError(RuntimeError):
    """A safe display message and the stage that failed. No provider traceback."""

    def __init__(self, stage: PipelineStage, message: str) -> None:
        self.stage = stage
        super().__init__(message)


@dataclass(frozen=True)
class PreparedVideo:
    metadata: VideoMetadata
    force: bool = False
    metadata_cached: bool = False
    settings: Settings | None = None


@dataclass(frozen=True)
class PipelineResult:
    metadata: VideoMetadata
    transcript: Transcript
    summary: Summary
    paths: ArtifactPaths
    audio_path: Path
    cache_record: CacheRecord
    reused_stages: tuple[PipelineStage, ...]

    @property
    def cache_hit(self) -> bool:
        return all(
            stage in self.reused_stages
            for stage in (
                PipelineStage.DOWNLOAD,
                PipelineStage.TRANSCRIPTION,
                PipelineStage.SUMMARIZATION,
            )
        )


def prepare_video(
    url: str, *, extractor: MetadataExtractor = extract_metadata
) -> VideoMetadata:
    """Compatibility metadata-only helper. Use Pipeline.prepare for full preflight."""
    return fetch_metadata(url, extractor=extractor)


def _validate_metadata(metadata: VideoMetadata) -> None:
    video = parse_video_url(metadata.original_url)
    if video.video_id != metadata.video_id:
        raise MetadataError("Confirmed metadata does not match the video URL.")
    duration = metadata.duration_seconds
    if not math.isfinite(duration) or duration <= 0:
        raise MetadataError(
            "Video duration is unavailable; cannot check the 2-hour limit."
        )
    if duration > MAX_DURATION_SECONDS:
        raise MetadataError("Video exceeds the 2-hour duration limit.")


def _cached_metadata(record: CacheRecord | None, url: str) -> VideoMetadata | None:
    if record is None or record.metadata_path is None:
        return None
    try:
        cached = TypeAdapter(VideoMetadata).validate_json(
            record.metadata_path.read_text(encoding="utf-8"), strict=True
        )
        if (
            parse_video_url(cached.original_url).video_id
            != parse_video_url(url).video_id
        ):
            return None
        info: dict[str, object] = {
            "id": cached.video_id,
            "title": cached.title,
            "channel": cached.channel,
            "duration": cached.duration_seconds,
            "chapters": [
                {
                    "title": chapter.title,
                    "start_time": chapter.start_seconds,
                    "end_time": chapter.end_seconds,
                }
                for chapter in cached.chapters
            ],
        }
        return fetch_metadata(url, extractor=lambda _: info)
    except (OSError, ValueError, ValidationError):
        return None


def _cached_transcript(record: CacheRecord | None) -> Transcript | None:
    if record is None or record.transcript_json_path is None:
        return None
    try:
        return Transcript.model_validate_json(
            record.transcript_json_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError):
        return None


def _cached_summary(
    record: CacheRecord | None, metadata: VideoMetadata, transcript: Transcript
) -> Summary | None:
    if record is None or record.summary_json_path is None:
        return None
    try:
        summary = Summary.model_validate_json(
            record.summary_json_path.read_text(encoding="utf-8")
        )
        validate_grounding(summary, metadata, transcript)
        return summary
    except (OSError, ValueError, ValidationError, SummarizationError):
        return None


def _retain_exports(
    staging: CacheStore,
    video_id: str,
    *,
    transcript: Transcript | None = None,
    summary: Summary | None = None,
) -> None:
    """Regenerate exports from validated JSON, including missing Markdown."""
    record = staging.lookup(video_id)
    assert record is not None
    paths = staging.paths(video_id)
    if transcript is not None:
        paths.transcript.write_text(transcript_markdown(transcript), encoding="utf-8")
        paths.transcript_json.write_text(transcript_json(transcript), encoding="utf-8")
        record = replace(
            record,
            transcript_path=paths.transcript,
            transcript_json_path=paths.transcript_json,
            transcription_provider=transcript.provider,
            transcription_model=transcript.model,
        )
    if summary is not None:
        paths.summary.write_text(summary_markdown(summary), encoding="utf-8")
        paths.summary_json.write_text(summary_json(summary), encoding="utf-8")
        record = replace(
            record,
            summary_path=paths.summary,
            summary_json_path=paths.summary_json,
            summarization_provider=summary.provider,
            summarization_model=summary.model,
        )
    staging.save(record)


def _publish(
    staging: CacheStore, store: CacheStore, video_id: str, work: Path
) -> CacheRecord:
    """Swap the video directory, then commit its record; restore on failure.

    A rollback failure retains the previous directory in work/previous-video.
    The caller must preserve that backup instead of deleting its only copy.
    """
    record = staging.lookup(video_id)
    assert record is not None and record.audio_path is not None
    source = staging.paths(video_id).directory
    paths = store.paths(video_id)
    record = replace(
        record,
        audio_path=paths.audio(record.audio_path.suffix.lstrip(".")),
        metadata_path=paths.metadata,
        transcript_path=paths.transcript,
        transcript_json_path=paths.transcript_json,
        summary_path=paths.summary,
        summary_json_path=paths.summary_json,
        run_state="completed",
    )
    backup = work / "previous-video"
    moved_previous = False
    published = False
    try:
        paths.directory.parent.mkdir(parents=True, exist_ok=True)
        if paths.directory.exists():
            paths.directory.replace(backup)
            moved_previous = True
        source.replace(paths.directory)
        published = True
        return store.save(record)
    except BaseException:
        try:
            if published:
                # Move the replacement away before restoring the old directory.
                paths.directory.replace(source)
            if moved_previous:
                backup.replace(paths.directory)
        except BaseException:
            raise PipelineError(
                PipelineStage.CACHE_EXPORT,
                f"Could not publish or restore cached outputs for {video_id}. "
                f"Previous artifacts, if present, are retained at {backup}.",
            ) from None
        raise


class Pipeline:
    """Prepare metadata, obtain explicit confirmation, then process one video.

    Calls block, so frontends should invoke them in a worker and marshal events
    to their UI thread. Callbacks receive immutable events on the calling thread;
    callback exceptions are ignored so presentation cannot break publication.
    Use one active run per video/output directory. There is no background queue.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: CacheStore | None = None,
        extractor: MetadataExtractor = extract_metadata,
        downloader: AudioDownloader = extract_audio,
        audio_preflight: Preflight = check_audio_dependencies,
        transcriber: Transcriber | None = None,
        summarizer: Summarizer | None = None,
        on_event: Callable[[PipelineEvent], None] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.extractor = extractor
        self.downloader = downloader
        self.audio_preflight = audio_preflight
        self.transcriber = transcriber
        self.summarizer = summarizer
        self.on_event = on_event

    def _event(self, stage: PipelineStage, message: str) -> None:
        if self.on_event is not None:
            try:
                self.on_event(PipelineEvent(stage, message))
            except Exception:
                pass

    def _failure(self, stage: PipelineStage, exc: Exception) -> PipelineError:
        if isinstance(exc, PipelineError):
            error = exc
        elif isinstance(exc, StorageError):
            error = PipelineError(
                stage,
                f"Could not read or save cache files or database during {stage.value}.",
            )
        elif isinstance(
            exc,
            (
                ConfigurationError,
                InvalidYouTubeURL,
                MetadataError,
                AudioError,
                TranscriptionError,
                SummarizationError,
            ),
        ):
            error = PipelineError(stage, f"{stage.value}: {exc}")
        else:
            error = PipelineError(stage, f"Could not complete {stage.value} stage.")
        self._event(PipelineStage.FAILURE, str(error))
        return error

    def _dependencies(
        self, settings: Settings | None = None
    ) -> tuple[Settings, Transcriber, Summarizer]:
        configuration = settings or self.settings or Settings.from_env()
        transcriber = self.transcriber or FasterWhisperTranscriber(configuration)
        summarizer = self.summarizer or create_summarizer(configuration)
        self.audio_preflight()
        transcriber.preflight()
        summarizer.preflight()
        limit = configuration.summarizer_max_input_bytes
        if isinstance(limit, bool) or limit <= 0:
            raise ConfigurationError(
                "YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES must be a positive integer."
            )
        return configuration, transcriber, summarizer

    def prepare(self, url: str, *, force: bool = False) -> PreparedVideo:
        """Full local preflight then validated metadata, without writing artifacts."""
        stage = PipelineStage.PREFLIGHT
        try:
            self._event(stage, "Checking workflow dependencies and configuration.")
            parse_video_url(url)
            configuration, _, _ = self._dependencies()
            stage = PipelineStage.METADATA
            self._event(stage, "Preparing YouTube video metadata.")
            metadata: VideoMetadata | None = None
            if not force:
                store = self.store
                if (
                    store is None
                    and (configuration.output_dir / "yt-smzr.sqlite").is_file()
                ):
                    store = CacheStore(configuration.output_dir)
                if store is not None:
                    metadata = _cached_metadata(
                        store.lookup(parse_video_url(url).video_id), url
                    )
            cached = metadata is not None
            if metadata is None:
                metadata = prepare_video(url, extractor=self.extractor)
            else:
                self._event(stage, "Reusing cached metadata.")
            self._event(PipelineStage.CONFIRMATION, "Confirm this video to continue.")
            return PreparedVideo(metadata, force, cached, configuration)
        except Exception as exc:
            raise self._failure(stage, exc) from None

    def process(self, prepared: PreparedVideo, *, confirmed: bool) -> PipelineResult:
        """Run confirmed work entirely in staging; force bypasses all artifact reuse."""
        stage = PipelineStage.CONFIRMATION
        work: Path | None = None
        committed = False
        try:
            if not confirmed:
                raise PipelineError(stage, "Confirm video metadata before processing.")
            _validate_metadata(prepared.metadata)
            stage = PipelineStage.PREFLIGHT
            configuration, transcriber, summarizer = self._dependencies(
                prepared.settings
            )
            metadata = prepared.metadata
            stage = PipelineStage.CACHE_EXPORT
            store = self.store or CacheStore(configuration.output_dir)
            previous = None if prepared.force else store.lookup(metadata.video_id)
            work = Path(mkdtemp(prefix=".pipeline-", dir=store.output_dir))
            staging = CacheStore(work / "staged")
            staging.save_metadata(metadata)
            reused: list[PipelineStage] = (
                [PipelineStage.METADATA]
                if prepared.metadata_cached and not prepared.force
                else []
            )
            stage = PipelineStage.DOWNLOAD
            self._event(stage, "Preparing compressed audio.")
            if previous is not None and previous.audio_path is not None:
                audio = previous.audio_path
                if (
                    audio.suffix.lstrip(".")
                    in {"m4a", "webm", "opus", "mp3", "ogg", "flac", "aac"}
                    and audio.is_file()
                    and audio.stat().st_size > 0
                ):
                    destination = staging.paths(metadata.video_id).audio(
                        audio.suffix.lstrip(".")
                    )
                    shutil.copy2(audio, destination)
                    record = staging.lookup(metadata.video_id)
                    assert record is not None
                    staging.save(replace(record, audio_path=destination))
                    reused.append(stage)
                    self._event(stage, "Reusing cached audio.")
            download_audio(
                metadata,
                staging,
                confirmed=True,
                downloader=self.downloader,
                preflight=self.audio_preflight,
            )
            stage = PipelineStage.TRANSCRIPTION
            self._event(stage, "Preparing timestamped transcript.")
            transcript = _cached_transcript(previous)
            if transcript is not None:
                _retain_exports(staging, metadata.video_id, transcript=transcript)
                reused.append(stage)
                self._event(stage, "Reusing cached transcript.")
            else:
                transcript = transcribe_audio(
                    metadata.video_id, staging, transcriber=transcriber
                )
            stage = PipelineStage.SUMMARIZATION
            self._event(stage, "Preparing structured summary.")
            check_input_limit(
                prepare_input(metadata, transcript),
                configuration.summarizer_max_input_bytes,
            )
            summary = _cached_summary(previous, metadata, transcript)
            if summary is not None:
                _retain_exports(staging, metadata.video_id, summary=summary)
                reused.append(stage)
                self._event(stage, "Reusing cached summary.")
            else:
                summary = summarize_transcript(
                    metadata,
                    transcript,
                    staging,
                    settings=configuration,
                    summarizer=summarizer,
                )
            stage = PipelineStage.CACHE_EXPORT
            self._event(stage, "Publishing completed outputs.")
            record = _publish(staging, store, metadata.video_id, work)
            committed = True
            assert record.audio_path is not None
            result = PipelineResult(
                metadata,
                transcript,
                summary,
                store.paths(metadata.video_id),
                record.audio_path,
                record,
                tuple(reused),
            )
            self._event(PipelineStage.COMPLETION, "Video processing completed.")
            return result
        except Exception as exc:
            raise self._failure(stage, exc) from None
        finally:
            if work is not None:
                # Never remove the only previous copy after a failed rollback.
                backup = work / "previous-video"
                preserve = backup.exists() and stage == PipelineStage.CACHE_EXPORT
                # A successful publication may safely dispose of its old backup.
                if committed or not preserve:
                    try:
                        shutil.rmtree(work, ignore_errors=True)
                    except OSError:
                        pass

    def run(self, prepared: PreparedVideo, *, confirmed: bool) -> PipelineResult:
        """Alias for process for callers that name the confirmed step run."""
        return self.process(prepared, confirmed=confirmed)
