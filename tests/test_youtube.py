"""URL and metadata preparation checks without YouTube requests or downloads."""

import importlib
from collections.abc import Mapping
from types import TracebackType

import pytest

from yt_smzr.models import VideoChapter
from yt_smzr.pipeline import prepare_video
from yt_smzr.youtube.metadata import MetadataError, extract_metadata
from yt_smzr.youtube.urls import InvalidYouTubeURL, parse_video_url

VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def info(**changes: object) -> dict[str, object]:
    return {
        "id": VIDEO_ID,
        "title": "A useful talk",
        "channel": "Speaker",
        "duration": 600,
        **changes,
    }


@pytest.mark.parametrize(
    "url",
    [
        CANONICAL,
        f"http://m.youtube.com/watch?v={VIDEO_ID}&list=PL123&index=2",
        f"https://youtu.be/{VIDEO_ID}?t=12&list=PL123",
        f"https://www.youtube.com/playlist?list=PL123&v={VIDEO_ID}",
        f"https://youtube.com/results?search_query=talk&v={VIDEO_ID}",
    ],
)
def test_accept_single_video_and_canonicalize(url: str) -> None:
    video = parse_video_url(url)
    assert video.video_id == VIDEO_ID
    assert video.canonical_url == CANONICAL
    assert video.original_url == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        VIDEO_ID,
        "youtube.com/watch?v=" + VIDEO_ID,
        "https://youtube.com/watch",
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/@channel",
        "https://youtube.com/results?search_query=talk",
        "https://youtube.com/embed/" + VIDEO_ID,
        "https://youtube.com/watch?v=short",
        "https://youtube.com/watch?v=",
        CANONICAL + "&v=" + VIDEO_ID,
        CANONICAL + "&v=",
        "https://youtube.com/watch?v=abcdefghij%2F",
        "https://youtu.be/" + VIDEO_ID + "/extra",
        "https://youtube.com.evil.example/watch?v=" + VIDEO_ID,
        "https://evil.example/watch?v=" + VIDEO_ID,
        "https://youtube.com@evil.example/watch?v=" + VIDEO_ID,
        "https://user@youtube.com/watch?v=" + VIDEO_ID,
        "https://youtube.com:443/watch?v=" + VIDEO_ID,
        "https://youtube.com:bad/watch?v=" + VIDEO_ID,
        "ftp://youtube.com/watch?v=" + VIDEO_ID,
        "https://[broken/watch?v=" + VIDEO_ID,
    ],
)
def test_reject_unsupported_urls_without_fetching(url: str) -> None:
    def unexpected_fetch(_: str) -> Mapping[str, object]:
        pytest.fail("invalid input reached metadata extraction")

    with pytest.raises(InvalidYouTubeURL):
        prepare_video(url, extractor=unexpected_fetch)


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/shorts/{VIDEO_ID}",
        f"https://m.youtube.com/shorts/{VIDEO_ID}?v={VIDEO_ID}",
    ],
)
def test_explicit_shorts_are_rejected(url: str) -> None:
    with pytest.raises(InvalidYouTubeURL, match="Shorts"):
        parse_video_url(url)


def test_preparation_preserves_metadata_and_supplied_chapters() -> None:
    requested: list[str] = []
    original = f" https://youtu.be/{VIDEO_ID}?list=PL123&t=10 "

    def fake(url: str) -> Mapping[str, object]:
        requested.append(url)
        return info(
            chapters=[
                {"title": "Introduction", "start_time": 0.0, "end_time": 100.5},
                {"title": "Discussion", "start_time": 100.5},
            ]
        )

    metadata = prepare_video(original, extractor=fake)
    assert requested == [CANONICAL]
    assert metadata.video_id == VIDEO_ID
    assert metadata.original_url == original
    assert metadata.title == "A useful talk"
    assert metadata.channel == "Speaker"
    assert metadata.duration_seconds == 600
    assert metadata.chapters == (
        VideoChapter("Introduction", 0, 100.5),
        VideoChapter("Discussion", 100.5),
    )


def test_missing_chapters_are_not_inferred_and_duration_cap_is_inclusive() -> None:
    metadata = prepare_video(CANONICAL, extractor=lambda _: info(duration=7200))
    assert metadata.duration_seconds == 7200
    assert metadata.chapters == ()


@pytest.mark.parametrize("duration", [7200.01, 7201])
def test_over_two_hours_rejected_after_metadata_fetch(duration: float) -> None:
    with pytest.raises(MetadataError, match="2-hour"):
        prepare_video(CANONICAL, extractor=lambda _: info(duration=duration))


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"duration": None}, "duration is unavailable"),
        ({"duration": 0}, "duration is unavailable"),
        ({"duration": -1}, "duration is unavailable"),
        ({"duration": True}, "duration is unavailable"),
        ({"duration": "600"}, "duration is unavailable"),
        ({"duration": float("nan")}, "duration is unavailable"),
        ({"duration": float("inf")}, "duration is unavailable"),
        ({"is_live": True}, "Live"),
        ({"live_status": "is_upcoming"}, "Live"),
        ({"live_status": "is_live"}, "Live"),
        ({"live_status": "post_live"}, "Live"),
        ({"id": "abcdefghijk"}, "different video"),
        ({"_type": "playlist"}, "single-video"),
        ({"title": ""}, "missing title"),
        ({"channel": None}, "missing channel"),
        ({"chapters": [{"title": "Bad", "start_time": -1}]}, "timestamps"),
    ],
)
def test_reject_unusable_metadata(changes: dict[str, object], message: str) -> None:
    with pytest.raises(MetadataError, match=message):
        prepare_video(CANONICAL, extractor=lambda _: info(**changes))


def test_empty_metadata_is_contextual_error() -> None:
    with pytest.raises(MetadataError, match="single-video"):
        prepare_video(CANONICAL, extractor=lambda _: None)


def test_extractor_error_has_context_and_cause() -> None:
    failure = RuntimeError("unavailable")

    def fake(_: str) -> Mapping[str, object]:
        raise failure

    with pytest.raises(MetadataError, match="Could not fetch") as caught:
        prepare_video(CANONICAL, extractor=fake)
    assert caught.value.__cause__ is failure


def test_yt_dlp_python_api_never_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            calls.append(options)

        def __enter__(self) -> "FakeYoutubeDL":
            return self

        def __exit__(
            self,
            kind: type[BaseException] | None,
            error: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            calls.append("closed")

        def extract_info(self, url: str, *, download: bool) -> dict[str, object]:
            calls.append((url, download))
            return info()

    monkeypatch.setattr(importlib.import_module("yt_dlp"), "YoutubeDL", FakeYoutubeDL)
    assert extract_metadata(CANONICAL) == info()
    assert calls == [
        {
            "noplaylist": True,
            "skip_download": True,
            "quiet": True,
            "js_runtimes": {"deno": {}, "node": {}},
        },
        (CANONICAL, False),
        "closed",
    ]
