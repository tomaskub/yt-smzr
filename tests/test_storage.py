"""Cache and file storage checks with isolated temporary directories."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yt_smzr.models import VideoChapter, VideoMetadata
from yt_smzr.storage import CacheRecord, CacheStore, StorageError, artifact_paths

VIDEO_ID = "dQw4w9WgXcQ"


def metadata(title: str = "A talk") -> VideoMetadata:
    return VideoMetadata(
        VIDEO_ID,
        f"https://youtu.be/{VIDEO_ID}",
        title,
        "Speaker",
        60,
        (VideoChapter("Start", 0, 60),),
    )


def test_default_store_uses_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = CacheStore()
    assert store.output_dir == tmp_path / ".yt-smzr"
    assert store.database_path.is_file()
    assert store.lookup(VIDEO_ID) is None


def test_artifact_paths_and_audio_extension(tmp_path: Path) -> None:
    paths = artifact_paths(tmp_path, VIDEO_ID)
    assert paths.directory == tmp_path / "videos" / VIDEO_ID
    assert paths.metadata == paths.directory / "metadata.json"
    assert paths.audio("webm") == paths.directory / "audio.webm"
    assert paths.audio("m4a") == paths.directory / "audio.m4a"
    assert paths.transcript == paths.directory / "transcript.md"
    assert paths.transcript_json == paths.directory / "transcript.json"
    assert paths.summary == paths.directory / "summary.md"
    assert paths.summary_json == paths.directory / "summary.json"
    assert not paths.directory.exists()


@pytest.mark.parametrize(
    "video_id", ["", "../outside", "a" * 12, "abcdefghij/", "a" * 10]
)
def test_invalid_ids_never_reach_files_or_database(
    tmp_path: Path, video_id: str
) -> None:
    store = CacheStore(tmp_path)
    with pytest.raises(ValueError, match="video ID"):
        store.paths(video_id)
    with pytest.raises(ValueError, match="video ID"):
        store.lookup(video_id)
    with pytest.raises(ValueError, match="video ID"):
        store.save(CacheRecord(video_id, "url", "title", "channel", 60))
    with pytest.raises(ValueError, match="video ID"):
        store.save_metadata(replace(metadata(), video_id=video_id))
    assert not (tmp_path / "videos").exists()


@pytest.mark.parametrize("extension", ["", ".webm", "../mp3", "a/b", "wav;rm", "a\\b"])
def test_invalid_audio_extensions(tmp_path: Path, extension: str) -> None:
    with pytest.raises(ValueError, match="extension"):
        artifact_paths(tmp_path, VIDEO_ID).audio(extension)


def test_snapshot_roundtrip_update_and_preserved_creation_time(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    paths = store.paths(VIDEO_ID)
    saved = store.save(
        CacheRecord(
            VIDEO_ID,
            "url",
            "title",
            "channel",
            60,
            audio_path=paths.audio("webm"),
            transcript_path=paths.transcript,
            transcript_json_path=paths.transcript_json,
            summary_path=paths.summary,
            summary_json_path=paths.summary_json,
            metadata_path=paths.metadata,
            transcription_provider="faster-whisper",
            transcription_model="small",
            summarization_provider="openai",
            summarization_model="model",
            run_state="completed",
        )
    )
    assert CacheStore(tmp_path).lookup(VIDEO_ID) == saved
    assert saved.created_at is not None and saved.created_at.tzinfo == UTC
    assert saved.updated_at == saved.created_at
    assert saved.updated_at is not None
    updated = store.save(
        replace(
            saved,
            title="Changed",
            summarization_model="another",
            summary_path=None,
            created_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
    )
    assert updated.created_at == saved.created_at
    assert updated.updated_at is not None and updated.updated_at >= saved.updated_at
    assert updated.audio_path == saved.audio_path
    assert updated.summary_path is None
    assert store.lookup(VIDEO_ID) == updated
    assert store.lookup("abcdefghijk") is None


def test_metadata_update_preserves_successful_artifacts(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    first = store.save_metadata(metadata())
    paths = store.paths(VIDEO_ID)
    for path in (paths.audio("m4a"), paths.transcript, paths.summary):
        path.write_text("existing output", encoding="utf-8")
    success = store.save(
        replace(
            first,
            audio_path=paths.audio("m4a"),
            transcript_path=paths.transcript,
            summary_path=paths.summary,
            run_state="completed",
            summarization_provider="openai",
            summarization_model="model",
        )
    )
    refreshed = store.save_metadata(metadata("New title"))
    assert refreshed.created_at == success.created_at
    assert refreshed.audio_path == success.audio_path
    assert refreshed.transcript_path == success.transcript_path
    assert refreshed.summary_path == success.summary_path
    assert refreshed.run_state == "completed"
    assert refreshed.summarization_model == "model"
    assert store.lookup(VIDEO_ID) == refreshed
    payload = json.loads(paths.metadata.read_text(encoding="utf-8"))
    assert payload["title"] == "New title"
    assert payload["chapters"] == [
        {"title": "Start", "start_seconds": 0, "end_seconds": 60}
    ]
    for path in (paths.audio("m4a"), paths.transcript, paths.summary):
        assert path.read_text(encoding="utf-8") == "existing output"
    assert sorted(path.name for path in paths.directory.iterdir()) == [
        "audio.m4a",
        "metadata.json",
        "summary.md",
        "transcript.md",
    ]


def test_store_can_be_used_from_a_worker_thread(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        saved = executor.submit(store.save_metadata, metadata()).result()
        assert executor.submit(store.lookup, VIDEO_ID).result() == saved
    # A closed operation connection leaves no transaction holding a write lock.
    with sqlite3.connect(store.database_path, timeout=0) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        connection.rollback()


def test_initialization_failure_is_contextual(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    with pytest.raises(StorageError, match="initialize cache") as error:
        CacheStore(blocked)
    assert isinstance(error.value.__cause__, OSError)


def test_corrupt_database_errors_include_operation(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    store.database_path.write_text("not SQLite")
    with pytest.raises(StorageError, match="read cache record"):
        store.lookup(VIDEO_ID)
    with pytest.raises(StorageError, match="save cache record"):
        store.save(CacheRecord(VIDEO_ID, "url", "title", "channel", 60))
    with pytest.raises(StorageError, match="save metadata"):
        store.save_metadata(metadata())
    assert list(store.paths(VIDEO_ID).directory.iterdir()) == []


def test_file_failure_leaves_existing_record_and_metadata(tmp_path: Path) -> None:
    store = CacheStore(tmp_path)
    first = store.save_metadata(metadata())
    paths = store.paths(VIDEO_ID)
    paths.metadata.unlink()
    paths.metadata.mkdir()
    with pytest.raises(StorageError, match="save metadata") as error:
        store.save_metadata(metadata("Changed"))
    assert isinstance(error.value.__cause__, OSError)
    assert store.lookup(VIDEO_ID) == first
    assert list(paths.directory.iterdir()) == [paths.metadata]


def test_temp_cleanup_failure_preserves_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CacheStore(tmp_path)
    primary = OSError("replace failed")

    def fail_replace(self: Path, target: Path) -> Path:
        raise primary

    def fail_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("cleanup failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(StorageError, match="save metadata") as error:
        store.save_metadata(metadata())
    assert error.value.__cause__ is primary
    assert store.lookup(VIDEO_ID) is None
