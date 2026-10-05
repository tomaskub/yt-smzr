"""Reusable workflow boundaries, independent of CLI and TUI code."""

from yt_smzr.models import VideoMetadata
from yt_smzr.youtube.metadata import MetadataExtractor, extract_metadata, fetch_metadata


def prepare_video(
    url: str, *, extractor: MetadataExtractor = extract_metadata
) -> VideoMetadata:
    """Fetch validated metadata for confirmation. Audio starts in a later step."""
    return fetch_metadata(url, extractor=extractor)
