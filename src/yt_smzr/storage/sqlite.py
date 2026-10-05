"""Small SQLite cache with operation-scoped connections and file metadata."""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from yt_smzr.models import VideoMetadata
from yt_smzr.storage.paths import ArtifactPaths, artifact_paths, validate_video_id

RunState = Literal["pending", "running", "completed", "failed"]


class StorageError(RuntimeError):
    """A cache database or artifact could not be read or written."""


@dataclass(frozen=True)
class CacheRecord:
    video_id: str
    original_url: str
    title: str
    channel: str
    duration_seconds: float
    audio_path: Path | None = None
    transcript_path: Path | None = None
    transcript_json_path: Path | None = None
    summary_path: Path | None = None
    summary_json_path: Path | None = None
    metadata_path: Path | None = None
    transcription_provider: str | None = None
    transcription_model: str | None = None
    summarization_provider: str | None = None
    summarization_model: str | None = None
    run_state: RunState = "pending"
    created_at: datetime | None = None
    updated_at: datetime | None = None


_COLUMNS = tuple(field.name for field in fields(CacheRecord))
_PATH_COLUMNS = tuple(name for name in _COLUMNS if name.endswith("_path"))
_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    original_url TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    audio_path TEXT, transcript_path TEXT, transcript_json_path TEXT,
    summary_path TEXT, summary_json_path TEXT, metadata_path TEXT,
    transcription_provider TEXT, transcription_model TEXT,
    summarization_provider TEXT, summarization_model TEXT,
    run_state TEXT NOT NULL
        CHECK(run_state IN ('pending','running','completed','failed')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)
"""


class CacheStore:
    """Store record snapshots; use save_metadata for a preserving metadata update.

    Connections live only for an operation, so the store can be constructed on one
    thread and used on another. Refresh staging and artifact publishing belong to
    the pipeline; this store never removes audio, transcripts, or summaries.
    """

    def __init__(self, output_dir: Path = Path(".yt-smzr")) -> None:
        self.output_dir = output_dir.resolve()
        self.database_path = self.output_dir / "yt-smzr.sqlite"
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.database_path)) as connection:
                with connection:
                    connection.execute(_SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(
                f"Could not initialize cache at {self.output_dir}."
            ) from exc

    def paths(self, video_id: str) -> ArtifactPaths:
        return artifact_paths(self.output_dir, video_id)

    def lookup(self, video_id: str) -> CacheRecord | None:
        validate_video_id(video_id)
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                return self._lookup(connection, video_id)
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise StorageError(f"Could not read cache record for {video_id}.") from exc

    @staticmethod
    def _lookup(connection: sqlite3.Connection, video_id: str) -> CacheRecord | None:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if row is None:
            return None
        values = dict(row)
        for name in _PATH_COLUMNS:
            values[name] = Path(values[name]) if values[name] is not None else None
        for name in ("created_at", "updated_at"):
            values[name] = datetime.fromisoformat(values[name])
        return CacheRecord(**values)

    @staticmethod
    def _save(connection: sqlite3.Connection, record: CacheRecord) -> CacheRecord:
        previous = CacheStore._lookup(connection, record.video_id)
        now = datetime.now(UTC)
        record = replace(
            record,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        values = [getattr(record, name) for name in _COLUMNS]
        parameters = [
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, Path)
            else value
            for value in values
        ]
        assignments = ", ".join(
            f"{name}=excluded.{name}" for name in _COLUMNS if name != "video_id"
        )
        connection.execute(
            f"INSERT INTO videos ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)}) "
            f"ON CONFLICT(video_id) DO UPDATE SET {assignments}",
            parameters,
        )
        return record

    def save(self, record: CacheRecord) -> CacheRecord:
        """Save a complete snapshot, preserving the original creation timestamp.

        To change selected fields, replace fields on a lookup result, then save it.
        None intentionally clears optional fields in this full snapshot API.
        """
        validate_video_id(record.video_id)
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    return self._save(connection, record)
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise StorageError(
                f"Could not save cache record for {record.video_id}."
            ) from exc

    def save_metadata(self, metadata: VideoMetadata) -> CacheRecord:
        """Replace metadata JSON atomically and retain previous artifact/run fields.

        For force refresh, the pipeline should use a separate staging store until
        the whole replacement run succeeds. This writes directly to this store.
        File replacement and SQLite commit are separate operations, not a shared
        transaction. A commit failure after replacement can leave new JSON with
        the old record; staging is required for whole-run publication safety.
        """
        paths = self.paths(metadata.video_id)
        temporary: Path | None = None
        try:
            paths.directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=paths.directory, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(metadata), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            with closing(sqlite3.connect(self.database_path)) as connection:
                with connection:
                    # Acquire the write lock before reading to preserve other updates.
                    connection.execute("BEGIN IMMEDIATE")
                    previous = self._lookup(connection, metadata.video_id)
                    base = previous or CacheRecord(
                        metadata.video_id,
                        metadata.original_url,
                        metadata.title,
                        metadata.channel,
                        metadata.duration_seconds,
                    )
                    record = self._save(
                        connection,
                        replace(
                            base,
                            original_url=metadata.original_url,
                            title=metadata.title,
                            channel=metadata.channel,
                            duration_seconds=metadata.duration_seconds,
                            metadata_path=paths.metadata,
                        ),
                    )
                    temporary.replace(paths.metadata)
                    return record
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            raise StorageError(
                f"Could not save metadata for {metadata.video_id} at {paths.metadata}."
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # Cleanup must not hide the original file/database failure.
                    pass
