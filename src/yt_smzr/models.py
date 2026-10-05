"""Typed video metadata shared by the pipeline and its frontends."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoChapter:
    title: str
    start_seconds: float
    end_seconds: float | None = None


@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    original_url: str
    title: str
    channel: str
    duration_seconds: float
    chapters: tuple[VideoChapter, ...] = ()
