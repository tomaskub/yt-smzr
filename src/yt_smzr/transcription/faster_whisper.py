"""Lazy faster-whisper adapter. Preflight never downloads or loads a model."""

import importlib
import importlib.util
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

from yt_smzr.config import Settings
from yt_smzr.transcription.base import Transcript, TranscriptionError, TranscriptSegment

_progress_lock_initialization = threading.Lock()


class ProgressLockConfig(Protocol):
    def set_lock(self, lock: object) -> None: ...


def initialize_progress_lock() -> None:
    """Avoid multiprocessing startup under Textual's captured stderr."""
    module = importlib.import_module("tqdm")
    progress = cast(ProgressLockConfig, module.tqdm)
    with _progress_lock_initialization:
        # get_lock() creates a multiprocessing lock, even for disabled bars.
        # Check the existing lock without constructing it; preserve locks that
        # downloads or callers already use. set_lock() is tqdm's public API.
        if not hasattr(progress, "_lock"):
            progress.set_lock(threading.RLock())


class WhisperSegment(Protocol):
    start: float
    end: float
    text: str


class WhisperModel(Protocol):
    def transcribe(
        self, audio: str, *, language: str, log_progress: bool
    ) -> tuple[Iterable[WhisperSegment], object]: ...


class ModelFactory(Protocol):
    def __call__(
        self, model: str, *, device: str, compute_type: str
    ) -> WhisperModel: ...


def load_model(model: str, *, device: str, compute_type: str) -> WhisperModel:
    module = importlib.import_module("faster_whisper")
    factory = cast(ModelFactory, module.WhisperModel)
    return factory(model, device=device, compute_type=compute_type)


class FasterWhisperTranscriber:
    def __init__(
        self, settings: Settings | None = None, *, factory: ModelFactory = load_model
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.factory = factory

    def preflight(self) -> None:
        if not self.settings.transcription_model.strip():
            raise TranscriptionError("YT_SMZR_TRANSCRIPTION_MODEL must not be empty.")
        for module in ("faster_whisper", "ctranslate2", "av", "tqdm"):
            if importlib.util.find_spec(module) is None:
                raise TranscriptionError(
                    f"Transcription requires {module}; run uv sync."
                )

    def transcribe(self, audio_path: Path) -> Transcript:
        self.preflight()
        try:
            initialize_progress_lock()
            model = self.factory(
                self.settings.transcription_model, device="cpu", compute_type="int8"
            )
            segments, _ = model.transcribe(
                str(audio_path), language="en", log_progress=False
            )
            # faster-whisper performs inference while this iterator is consumed.
            return Transcript(
                provider="faster-whisper",
                model=self.settings.transcription_model,
                segments=tuple(
                    TranscriptSegment(
                        start_seconds=segment.start,
                        end_seconds=segment.end,
                        text=segment.text.strip(),
                    )
                    for segment in segments
                ),
            )
        except Exception as exc:
            raise TranscriptionError(
                f"faster-whisper could not transcribe audio with model "
                f"{self.settings.transcription_model}."
            ) from exc
