"""Metadata-only yt-dlp integration. No audio is downloaded here."""

import math
from collections.abc import Mapping
from typing import Protocol, cast

from yt_smzr.models import VideoChapter, VideoMetadata
from yt_smzr.youtube.urls import parse_video_url

MAX_DURATION_SECONDS = 2 * 60 * 60


class MetadataError(ValueError):
    """Metadata could not be fetched or is unsafe to continue processing."""


class MetadataExtractor(Protocol):
    def __call__(self, url: str, /) -> Mapping[str, object] | None: ...


def extract_metadata(url: str) -> Mapping[str, object] | None:
    """Use the Python API with playlist processing and downloads disabled."""
    from yt_dlp import YoutubeDL  # pyright: ignore[reportMissingTypeStubs]

    with YoutubeDL(
        {"noplaylist": True, "skip_download": True, "quiet": True}  # pyright: ignore[reportArgumentType]
    ) as ydl:
        return cast(
            Mapping[str, object] | None,
            ydl.extract_info(url, download=False),  # pyright: ignore[reportUnknownMemberType]
        )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _text(info: Mapping[str, object], key: str) -> str:
    value = info.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MetadataError(f"Video metadata is missing {key}.")
    return value


def fetch_metadata(
    url: str, *, extractor: MetadataExtractor = extract_metadata
) -> VideoMetadata:
    """Return metadata eligible for confirmation before any audio work."""
    video = parse_video_url(url)
    try:
        info = extractor(video.canonical_url)
    except Exception as exc:
        raise MetadataError("Could not fetch YouTube video metadata.") from exc
    if not info or info.get("_type", "video") != "video":
        raise MetadataError("YouTube did not return single-video metadata.")
    if info.get("id") != video.video_id:
        raise MetadataError("YouTube returned metadata for a different video.")
    if info.get("is_live") or info.get("live_status") in {
        "is_live",
        "is_upcoming",
        "post_live",
    }:
        raise MetadataError("Live or upcoming videos are not supported.")
    duration = _number(info.get("duration"))
    if duration is None or duration <= 0:
        raise MetadataError(
            "Video duration is unavailable; cannot check the 2-hour limit."
        )
    if duration > MAX_DURATION_SECONDS:
        raise MetadataError("Video exceeds the 2-hour duration limit.")
    chapters: list[VideoChapter] = []
    raw_chapters = info.get("chapters")
    if raw_chapters is not None:
        if not isinstance(raw_chapters, list):
            raise MetadataError("YouTube returned invalid chapter metadata.")
        for raw in cast(list[object], raw_chapters):
            if not isinstance(raw, dict):
                raise MetadataError("YouTube returned invalid chapter metadata.")
            chapter = cast(dict[str, object], raw)
            start = _number(chapter.get("start_time"))
            end = _number(chapter.get("end_time"))
            if (
                start is None
                or start < 0
                or start > duration
                or (chapter.get("end_time") is not None and end is None)
                or (end is not None and (end < start or end > duration))
            ):
                raise MetadataError("YouTube returned invalid chapter timestamps.")
            chapters.append(VideoChapter(_text(chapter, "title"), start, end))
    return VideoMetadata(
        video_id=video.video_id,
        original_url=video.original_url,
        title=_text(info, "title"),
        channel=_text(info, "channel"),
        duration_seconds=duration,
        chapters=tuple(chapters),
    )
