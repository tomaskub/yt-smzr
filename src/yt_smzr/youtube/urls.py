"""Validate MVP YouTube inputs before handing a single video to yt-dlp."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


class InvalidYouTubeURL(ValueError):
    """The input does not identify an MVP-supported YouTube video."""


@dataclass(frozen=True)
class YouTubeVideoURL:
    video_id: str
    original_url: str

    @property
    def canonical_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def parse_video_url(url: str) -> YouTubeVideoURL:
    """Accept one watch/short-link ID, ignoring playlist and tracking parameters."""
    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise InvalidYouTubeURL("Invalid YouTube URL.") from exc
    if (
        parsed.scheme not in {"https", "http"}
        or host not in _YOUTUBE_HOSTS | {"youtu.be", "www.youtu.be"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
    ):
        raise InvalidYouTubeURL("Use a YouTube watch URL or youtu.be video link.")
    if parsed.path.split("/")[1:2] == ["shorts"]:
        raise InvalidYouTubeURL("YouTube Shorts URLs are not supported.")
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.removeprefix("/")
    else:
        ids = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
        if len(ids) != 1:
            raise InvalidYouTubeURL("The YouTube URL must identify a single video.")
        video_id = ids[0]
    if _VIDEO_ID.fullmatch(video_id) is None:
        raise InvalidYouTubeURL("The YouTube URL has an invalid video ID.")
    return YouTubeVideoURL(video_id=video_id, original_url=url)
