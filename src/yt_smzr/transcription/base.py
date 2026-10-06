"""Provider-neutral validated transcript data and backend contract."""

from pathlib import Path
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# One nanosecond absorbs float arithmetic noise even at the two-hour video limit,
# while remaining far below audio timing precision. Keep it absolute so larger
# timestamps cannot admit larger overlaps. Validation never changes timestamps.
_SEGMENT_BOUNDARY_TOLERANCE_SECONDS = 1e-9


class TranscriptionError(RuntimeError):
    """Transcription or transcript publication failed; safe to display."""


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_segment(self) -> Self:
        if self.end_seconds < self.start_seconds:
            raise ValueError("Segment end must not precede its start.")
        if not self.text.strip():
            raise ValueError("Segment text must contain speech.")
        return self


class Transcript(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    language: str = "en"
    segments: tuple[TranscriptSegment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_transcript(self) -> Self:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("Transcript provider and model must be nonempty.")
        if self.language != "en":
            raise ValueError("Only English transcripts are supported.")
        for previous, current in zip(self.segments, self.segments[1:], strict=False):
            if (
                current.start_seconds < previous.start_seconds
                or previous.end_seconds - current.start_seconds
                > _SEGMENT_BOUNDARY_TOLERANCE_SECONDS
            ):
                raise ValueError("Transcript segments must be ordered without overlap.")
        return self


class Transcriber(Protocol):
    def preflight(self) -> None: ...

    def transcribe(self, audio_path: Path) -> Transcript: ...
