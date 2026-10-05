"""Confirmed audio download with local staging and rollback on publication failure.

A full pipeline refresh must pass a separate staging CacheStore, then publish all
artifacts only after transcription and summarization succeed.
"""

import importlib.util
import math
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from yt_smzr.models import VideoMetadata
from yt_smzr.storage.sqlite import CacheRecord, CacheStore
from yt_smzr.youtube.logging import SILENT_LOGGER
from yt_smzr.youtube.metadata import MAX_DURATION_SECONDS
from yt_smzr.youtube.urls import parse_video_url


class AudioError(RuntimeError):
    """Audio preparation failed; safe to display to a caller."""


class AudioDownloader(Protocol):
    def __call__(self, url: str, directory: Path, /) -> Path: ...


class Preflight(Protocol):
    def __call__(self) -> None: ...


def check_audio_dependencies() -> None:
    """Check the Python extractor, ffmpeg, and local YouTube JS support."""
    for module, installation in (
        ("yt_dlp", "yt-dlp[default]"),
        ("yt_dlp_ejs", "yt-dlp[default] including yt-dlp-ejs"),
    ):
        if importlib.util.find_spec(module) is None:
            raise AudioError(f"Audio download requires {installation}; run uv sync.")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioError("Audio download requires ffmpeg on PATH.")
    _check_executable(ffmpeg, "-version", "ffmpeg")
    for runtime, minimum in (("deno", (2, 3, 0)), ("node", (22, 0, 0))):
        executable = shutil.which(runtime)
        if executable is None:
            continue
        try:
            output = _check_executable(executable, "--version", runtime)
        except AudioError:
            continue
        match = re.search(r"(?:^|\s|v)(\d+)\.(\d+)\.(\d+)", output)
        if match and tuple(int(part) for part in match.groups()) >= minimum:
            return
    raise AudioError(
        "YouTube audio requires Deno >=2.3.0 or Node >=22.0.0 on PATH "
        "and yt-dlp-ejs. Install or upgrade deno or node."
    )


def _check_executable(executable: str, flag: str, name: str) -> str:
    try:
        result = subprocess.run(
            [executable, flag],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise AudioError(f"Could not run required audio dependency {name}.") from exc


def extract_audio(url: str, directory: Path) -> Path:
    """Low-level adapter; callers must use download_audio for confirmation/limits."""
    from yt_dlp import YoutubeDL  # pyright: ignore[reportMissingTypeStubs]

    video = parse_video_url(url)

    # Deno and Node version support follows the yt-dlp EJS setup guide.
    # EJS is installed with yt-dlp[default], never fetched.
    options: dict[str, object] = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "logger": SILENT_LOGGER,
        "outtmpl": str(directory / "audio.%(ext)s"),
        "js_runtimes": {name: {} for name in ("deno", "node")},
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
    }
    with YoutubeDL(options) as ydl:  # pyright: ignore[reportArgumentType]
        ydl.extract_info(video.canonical_url, download=True)  # pyright: ignore[reportUnknownMemberType]
    candidates = [
        path
        for path in directory.glob("audio.*")
        if path.suffix.lstrip(".")
        in {"m4a", "webm", "opus", "mp3", "ogg", "flac", "aac"}
        and path.is_file()
    ]
    if len(candidates) != 1 or candidates[0].stat().st_size == 0:
        raise AudioError("YouTube download did not produce one compressed audio file.")
    return candidates[0]


def download_audio(
    metadata: VideoMetadata,
    store: CacheStore,
    *,
    confirmed: bool,
    force: bool = False,
    downloader: AudioDownloader = extract_audio,
    preflight: Preflight = check_audio_dependencies,
) -> Path:
    """Download confirmed metadata, or reuse audio. Force never bypasses limits.

    Audio publication alone is rolled back on errors. Use a staging store when
    later pipeline stages must also succeed before replacing a completed run.
    """
    if not confirmed:
        raise AudioError("Confirm video metadata before downloading audio.")
    video = parse_video_url(metadata.original_url)
    if video.video_id != metadata.video_id:
        raise AudioError("Confirmed metadata does not match the video URL.")
    duration = metadata.duration_seconds
    if not math.isfinite(duration) or duration <= 0:
        raise AudioError(
            "Video duration is unavailable; cannot check the 2-hour limit."
        )
    if duration > MAX_DURATION_SECONDS:
        raise AudioError("Video exceeds the 2-hour duration limit.")
    previous = store.lookup(metadata.video_id)
    if (
        not force
        and previous is not None
        and previous.audio_path is not None
        and previous.audio_path.is_file()
        and previous.audio_path.stat().st_size > 0
    ):
        return previous.audio_path
    preflight()
    paths = store.paths(metadata.video_id)
    destination: Path | None = None
    published = False
    try:
        paths.directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=".audio-", dir=paths.directory, ignore_cleanup_errors=True
        ) as temporary:
            staging = Path(temporary)
            source = downloader(video.canonical_url, staging)
            if source.parent.resolve() != staging.resolve() or not source.is_file():
                raise AudioError(
                    "Audio downloader returned an invalid staged artifact."
                )
            if source.stat().st_size == 0:
                raise AudioError("Audio downloader returned an empty artifact.")
            extension = source.suffix.lstrip(".")
            if extension not in {"m4a", "webm", "opus", "mp3", "ogg", "flac", "aac"}:
                raise AudioError("Audio downloader did not return compressed audio.")
            destination = paths.audio(extension)
            backup = staging / "previous-audio"
            if destination.exists():
                shutil.copy2(destination, backup)
            try:
                source.replace(destination)
                published = True
                base = previous or CacheRecord(
                    metadata.video_id,
                    metadata.original_url,
                    metadata.title,
                    metadata.channel,
                    duration,
                )
                store.save(replace(base, audio_path=destination))
            except Exception:
                if published:
                    if backup.exists():
                        backup.replace(destination)
                    else:
                        destination.unlink(missing_ok=True)
                raise
            return destination
    except Exception as exc:
        if isinstance(exc, AudioError):
            raise
        raise AudioError("Could not download or save YouTube audio.") from exc
