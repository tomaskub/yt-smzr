"""Audio workflows use fake downloads and real local cache files."""

from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest

from yt_smzr.models import VideoMetadata
from yt_smzr.storage.sqlite import CacheStore, StorageError
from yt_smzr.youtube.audio import (
    AudioError,
    check_audio_dependencies,
    download_audio,
    extract_audio,
)

VIDEO = VideoMetadata(
    "dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ", "Title", "Channel", 42
)


def skip_preflight() -> None:
    pass


def fake_download(url: str, directory: Path) -> Path:
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    audio = directory / "audio.opus"
    audio.write_bytes(b"new audio")
    return audio


def run(store: CacheStore, **kwargs: Any) -> Path:
    return download_audio(
        VIDEO,
        store,
        confirmed=True,
        preflight=skip_preflight,
        downloader=fake_download,
        **kwargs,
    )


def test_download_cache_reuse_and_force(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    path = run(store)
    assert path == store.paths(VIDEO.video_id).audio("opus")
    path.write_bytes(b"previous")
    assert run(store).read_bytes() == b"previous"
    assert run(store, force=True).read_bytes() == b"new audio"
    record = store.lookup(VIDEO.video_id)
    assert record is not None
    assert record.audio_path == path
    assert list(path.parent.glob(".audio-*")) == []


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("duration", [7201, float("nan"), 0])
def test_duration_cannot_be_bypassed(
    tmp_path: Path, duration: float, force: bool
) -> None:
    with pytest.raises(AudioError, match="duration"):
        download_audio(
            replace(VIDEO, duration_seconds=duration),
            CacheStore(tmp_path),
            confirmed=True,
            force=force,
            downloader=fake_download,
            preflight=skip_preflight,
        )


def test_declined_metadata_does_no_work(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="Confirm"):
        download_audio(VIDEO, CacheStore(tmp_path), confirmed=False)
    assert not (tmp_path / "videos").exists()


def test_mismatched_video_rejected(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="does not match"):
        download_audio(
            replace(VIDEO, video_id="abcdefghijk"), CacheStore(tmp_path), confirmed=True
        )


@pytest.mark.parametrize("different_extension", [False, True])
def test_cache_write_failure_restores_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, different_extension: bool
) -> None:
    store = CacheStore(tmp_path)
    path = run(store)
    path.write_bytes(b"previous")
    previous = store.lookup(VIDEO.video_id)
    metadata_path = store.paths(VIDEO.video_id).metadata
    metadata_path.write_bytes(b"previous metadata")

    def failing_save(*args: object) -> None:
        raise StorageError("database failure")

    def download(url: str, directory: Path) -> Path:
        result = directory / ("audio.m4a" if different_extension else "audio.opus")
        result.write_bytes(b"replacement")
        return result

    monkeypatch.setattr(store, "save", failing_save)
    with pytest.raises(AudioError, match="save"):
        download_audio(
            VIDEO,
            store,
            confirmed=True,
            force=True,
            downloader=download,
            preflight=skip_preflight,
        )
    assert path.read_bytes() == b"previous"
    assert store.lookup(VIDEO.video_id) == previous
    assert metadata_path.read_bytes() == b"previous metadata"
    assert not store.paths(VIDEO.video_id).audio("m4a").exists()
    assert list(path.parent.glob(".audio-*")) == []


def test_failed_download_removes_parts_preserves_completed_run(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    path = run(store)
    previous = store.lookup(VIDEO.video_id)

    def fail(url: str, directory: Path) -> Path:
        (directory / "audio.opus.part").write_bytes(b"partial")
        (directory / "audio.opus").write_bytes(b"incomplete")
        raise RuntimeError("network failed")

    with pytest.raises(AudioError, match="download"):
        download_audio(
            VIDEO,
            store,
            confirmed=True,
            force=True,
            downloader=fail,
            preflight=skip_preflight,
        )
    assert path.read_bytes() == b"new audio"
    assert store.lookup(VIDEO.video_id) == previous
    assert list(path.parent.iterdir()) == [path]


def test_pipeline_can_download_to_separate_store(tmp_path: Path) -> None:
    live = CacheStore(tmp_path / "live")
    old = run(live)
    old.write_bytes(b"completed run")
    staged = CacheStore(tmp_path / "staged")
    assert run(staged, force=True).read_bytes() == b"new audio"
    assert old.read_bytes() == b"completed run"


@pytest.mark.parametrize("missing", ["yt_dlp", "yt_dlp_ejs", "ffmpeg", "runtime"])
def test_dependency_failures(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    def find_spec(name: str) -> object | None:
        return None if name == missing else object()

    def which(name: str) -> str | None:
        if name == missing or (missing == "runtime" and name != "ffmpeg"):
            return None
        return "/fake/" + name

    def execute(*args: object, **kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess([], 0, stdout="v22.0.0")

    monkeypatch.setattr("importlib.util.find_spec", find_spec)
    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("subprocess.run", execute)
    with pytest.raises(AudioError):
        check_audio_dependencies()


@pytest.mark.parametrize("version", ["v21.9.0", "unknown version"])
def test_unsupported_runtime_version(
    monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    def find_spec(name: str) -> object:
        return object()

    def which(name: str) -> str | None:
        return "/fake/" + name if name in {"ffmpeg", "node"} else None

    def execute(*args: object, **kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess([], 0, stdout=version)

    monkeypatch.setattr("importlib.util.find_spec", find_spec)
    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("subprocess.run", execute)
    with pytest.raises(AudioError, match="Node >=22"):
        check_audio_dependencies()


def test_dependencies_execute_without_exposing_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def find_spec(name: str) -> object:
        return object()

    def which(name: str) -> str | None:
        return "/fake/" + name if name in {"ffmpeg", "node"} else None

    calls: list[object] = []

    def execute(args: object, **kwargs: object) -> CompletedProcess[str]:
        calls.append(args)
        assert kwargs["capture_output"] is True
        return CompletedProcess([], 0, stdout="v22.0.0")

    monkeypatch.setattr("importlib.util.find_spec", find_spec)
    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("subprocess.run", execute)
    check_audio_dependencies()
    assert calls == [["/fake/ffmpeg", "-version"], ["/fake/node", "--version"]]


def test_ytdlp_adapter_uses_python_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class FakeYDL:
        def __init__(self, options: dict[str, Any]) -> None:
            captured.update(options)

        def __enter__(self) -> "FakeYDL":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def extract_info(self, url: str, *, download: bool) -> None:
            assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            assert download
            (tmp_path / "audio.m4a").write_bytes(b"compressed")

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    result = extract_audio("https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)
    assert result.read_bytes() == b"compressed"
    assert captured["noplaylist"] is True
    assert captured["format"] == "bestaudio/best"
    assert captured["postprocessors"] == [
        {"key": "FFmpegExtractAudio", "preferredcodec": "best"}
    ]
    assert captured["outtmpl"] == str(tmp_path / "audio.%(ext)s")


def test_publication_failure_preserves_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CacheStore(tmp_path)
    audio = run(store)
    audio.write_bytes(b"previous")
    previous = store.lookup(VIDEO.video_id)
    original_replace = Path.replace

    def fail_publish(self: Path, target: str | Path) -> Path:
        if self.parent.name.startswith(".audio-"):
            raise OSError("cannot publish")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(AudioError):
        run(store, force=True)
    assert audio.read_bytes() == b"previous"
    assert store.lookup(VIDEO.video_id) == previous
    assert list(audio.parent.glob(".audio-*")) == []


def test_preflight_failure_leaves_cache_untouched(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    audio = run(store)
    previous = store.lookup(VIDEO.video_id)

    def fail() -> None:
        raise AudioError("ffmpeg unavailable")

    with pytest.raises(AudioError, match="ffmpeg"):
        download_audio(VIDEO, store, confirmed=True, force=True, preflight=fail)
    assert audio.read_bytes() == b"new audio"
    assert store.lookup(VIDEO.video_id) == previous
    assert list(audio.parent.glob(".audio-*")) == []


@pytest.mark.parametrize("extension,content", [("wav", b"raw"), ("opus", b"")])
def test_invalid_artifact_not_published(
    tmp_path: Path, extension: str, content: bytes
) -> None:
    store = CacheStore(tmp_path)

    def download(url: str, directory: Path) -> Path:
        path = directory / f"audio.{extension}"
        path.write_bytes(content)
        return path

    with pytest.raises(AudioError):
        download_audio(
            VIDEO, store, confirmed=True, downloader=download, preflight=skip_preflight
        )
    assert store.lookup(VIDEO.video_id) is None
    assert list(store.paths(VIDEO.video_id).directory.iterdir()) == []


@pytest.mark.parametrize(
    "version,allowed", [("deno 2.2.9", False), ("deno 2.3.0", True)]
)
def test_deno_minimum_version(
    monkeypatch: pytest.MonkeyPatch, version: str, allowed: bool
) -> None:
    def find_spec(name: str) -> object:
        return object()

    def which(name: str) -> str | None:
        return "/fake/" + name if name in {"ffmpeg", "deno"} else None

    def execute(*args: object, **kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess([], 0, stdout=version)

    monkeypatch.setattr("importlib.util.find_spec", find_spec)
    monkeypatch.setattr("shutil.which", which)
    monkeypatch.setattr("subprocess.run", execute)
    if allowed:
        check_audio_dependencies()
    else:
        with pytest.raises(AudioError, match="Deno >=2.3"):
            check_audio_dependencies()
