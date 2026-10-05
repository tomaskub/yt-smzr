"""Transcribe cached audio independently of UI, with paired export rollback."""

import shutil
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from yt_smzr.storage.export import transcript_json, transcript_markdown
from yt_smzr.storage.sqlite import CacheStore
from yt_smzr.transcription.base import Transcriber, Transcript, TranscriptionError
from yt_smzr.transcription.faster_whisper import FasterWhisperTranscriber


def transcribe_audio(
    video_id: str,
    store: CacheStore,
    *,
    transcriber: Transcriber | None = None,
    force: bool = False,
) -> Transcript:
    """Reuse transcripts or publish new files and cache metadata together.

    A whole-run force refresh must supply a staging CacheStore until all later
    stages succeed. This boundary restores its own files on replacement failure.
    """
    try:
        previous = store.lookup(video_id)
        if previous is None:
            raise TranscriptionError("No cached video is available to transcribe.")
        if (
            not force
            and previous.transcript_path is not None
            and previous.transcript_path.is_file()
            and previous.transcript_json_path is not None
            and previous.transcript_json_path.is_file()
        ):
            return Transcript.model_validate_json(
                previous.transcript_json_path.read_text(encoding="utf-8")
            )
        audio = previous.audio_path
        if audio is None or not audio.is_file() or audio.stat().st_size == 0:
            raise TranscriptionError("Cached audio is missing or empty.")
        backend = transcriber or FasterWhisperTranscriber()
        backend.preflight()
        transcript = backend.transcribe(audio)
        paths = store.paths(video_id)
        paths.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=".transcript-", dir=paths.directory, ignore_cleanup_errors=True
        ) as temporary:
            staging = Path(temporary)
            destinations = (paths.transcript, paths.transcript_json)
            contents = (transcript_markdown(transcript), transcript_json(transcript))
            staged: list[Path] = []
            backups: list[Path | None] = []
            for index, (destination, content) in enumerate(
                zip(destinations, contents, strict=True)
            ):
                source = staging / f"new-{index}"
                source.write_text(content, encoding="utf-8")
                staged.append(source)
                backup = staging / f"previous-{index}"
                if destination.exists():
                    shutil.copy2(destination, backup)
                    backups.append(backup)
                else:
                    backups.append(None)
            published: list[int] = []
            try:
                for index, (source, destination) in enumerate(
                    zip(staged, destinations, strict=True)
                ):
                    source.replace(destination)
                    published.append(index)
                store.save(
                    replace(
                        previous,
                        transcript_path=paths.transcript,
                        transcript_json_path=paths.transcript_json,
                        transcription_provider=transcript.provider,
                        transcription_model=transcript.model,
                    )
                )
            except Exception:
                for index in reversed(published):
                    backup = backups[index]
                    if backup is None:
                        destinations[index].unlink(missing_ok=True)
                    else:
                        backup.replace(destinations[index])
                raise
        return transcript
    except Exception as exc:
        if isinstance(exc, TranscriptionError):
            raise
        raise TranscriptionError(
            f"Could not transcribe or save transcript for {video_id}."
        ) from exc
