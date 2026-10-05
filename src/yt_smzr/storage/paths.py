"""Deterministic locations for file artifacts kept outside SQLite."""

import re
from dataclasses import dataclass
from pathlib import Path


def validate_video_id(video_id: str) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) is None:
        raise ValueError("Invalid YouTube video ID for cache storage.")


@dataclass(frozen=True)
class ArtifactPaths:
    directory: Path
    metadata: Path
    transcript: Path
    transcript_json: Path
    summary: Path
    summary_json: Path

    def audio(self, extension: str) -> Path:
        """Choose the retained compressed audio extension after extraction."""
        if re.fullmatch(r"[A-Za-z0-9]+", extension) is None:
            raise ValueError("Invalid audio file extension.")
        return self.directory / f"audio.{extension}"


def artifact_paths(output_dir: Path, video_id: str) -> ArtifactPaths:
    """Return paths without creating files or directories."""
    validate_video_id(video_id)
    directory = output_dir / "videos" / video_id
    return ArtifactPaths(
        directory=directory,
        metadata=directory / "metadata.json",
        transcript=directory / "transcript.md",
        transcript_json=directory / "transcript.json",
        summary=directory / "summary.md",
        summary_json=directory / "summary.json",
    )
