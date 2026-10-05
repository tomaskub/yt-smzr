"""Summarize transcripts independently of UI, with paired export rollback."""

import shutil
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from yt_smzr.config import ConfigurationError, Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.storage.export import summary_json, summary_markdown
from yt_smzr.storage.sqlite import CacheStore
from yt_smzr.summarization.base import (
    SummarizationError,
    Summarizer,
    Summary,
    validate_grounding,
)
from yt_smzr.summarization.openai_provider import OpenAISummarizer
from yt_smzr.summarization.prompts import check_input_limit, prepare_input
from yt_smzr.transcription.base import Transcript


def summarize_transcript(
    metadata: VideoMetadata,
    transcript: Transcript,
    store: CacheStore,
    *,
    settings: Settings | None = None,
    summarizer: Summarizer | None = None,
    force: bool = False,
) -> Summary:
    """Reuse summaries or publish new files and cache metadata together.

    A whole-run force refresh must supply a staging CacheStore until all later
    stages succeed. This boundary restores its own files on replacement failure.
    """
    video_id = metadata.video_id
    try:
        previous = store.lookup(video_id)
        if previous is None:
            raise SummarizationError("No cached video is available to summarize.")
        if (
            not force
            and previous.summary_path is not None
            and previous.summary_path.is_file()
            and previous.summary_json_path is not None
            and previous.summary_json_path.is_file()
        ):
            cached = Summary.model_validate_json(
                previous.summary_json_path.read_text(encoding="utf-8")
            )
            validate_grounding(cached, metadata, transcript)
            return cached
        configuration = settings or Settings.from_env()
        check_input_limit(
            prepare_input(metadata, transcript),
            configuration.summarizer_max_input_bytes,
        )
        backend = summarizer or OpenAISummarizer(configuration)
        backend.preflight()
        summary = Summary.model_validate(
            backend.summarize(metadata, transcript).model_dump()
        )
        validate_grounding(summary, metadata, transcript)
        paths = store.paths(video_id)
        paths.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=".summary-", dir=paths.directory, ignore_cleanup_errors=True
        ) as temporary:
            staging = Path(temporary)
            destinations = (paths.summary, paths.summary_json)
            contents = (summary_markdown(summary), summary_json(summary))
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
                        summary_path=paths.summary,
                        summary_json_path=paths.summary_json,
                        summarization_provider=summary.provider,
                        summarization_model=summary.model,
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
        return summary
    except Exception as exc:
        if isinstance(exc, SummarizationError):
            raise
        if isinstance(exc, ConfigurationError):
            raise SummarizationError(str(exc)) from None
        raise SummarizationError(
            f"Could not summarize or save summary for {video_id}."
        ) from None
