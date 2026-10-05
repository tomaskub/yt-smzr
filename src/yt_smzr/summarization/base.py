"""Provider-neutral summary models, grounding validation, and contract."""

import math
from typing import Annotated, Protocol, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from yt_smzr.models import VideoMetadata
from yt_smzr.transcription.base import Transcript


class SummarizationError(RuntimeError):
    """Summarization or summary publication failed; safe to display."""


def nonempty(value: str) -> str:
    if not value.strip():
        raise ValueError("Summary text must not be empty.")
    return value


SummaryText = Annotated[str, AfterValidator(nonempty)]


class SummaryModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Chapter(SummaryModel):
    title: SummaryText
    start_seconds: float = Field(ge=0)
    end_seconds: float | None
    summary: SummaryText

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.end_seconds is not None and self.end_seconds < self.start_seconds:
            raise ValueError("Chapter end must not precede its start.")
        return self


class NotableClaim(SummaryModel):
    """A statement attributed to the video, not an independently verified fact."""

    claim: SummaryText
    timestamp_seconds: int | None = Field(ge=0, strict=True)


class NotableQuote(SummaryModel):
    quote: SummaryText
    timestamp_seconds: int | None = Field(ge=0, strict=True)


class SummaryContent(SummaryModel):
    """Structured provider output. An empty quotes list means no grounded quotes."""

    short_summary: SummaryText
    detailed_summary: SummaryText
    key_points: tuple[SummaryText, ...]
    chapters: tuple[Chapter, ...]
    notable_claims: tuple[NotableClaim, ...]
    notable_quotes: tuple[NotableQuote, ...]


class Summary(SummaryContent):
    """Exported summary, including provider/model provenance."""

    provider: SummaryText
    model: SummaryText


def validate_grounding(
    summary: SummaryContent, metadata: VideoMetadata, transcript: Transcript
) -> None:
    """Reject altered/inferred outlines and quotes absent from source speech.

    Integer quote timestamps may round a segment's fractional start down. Quotes
    must occur within one segment or adjacent speech segments, with their time
    inside a segment containing the start of the quoted words. Whitespace is
    normalized, but words, capitalization, and punctuation must match exactly.
    """
    outline = tuple(
        (chapter.title, chapter.start_seconds, chapter.end_seconds)
        for chapter in summary.chapters
    )
    expected = tuple(
        (chapter.title, chapter.start_seconds, chapter.end_seconds)
        for chapter in metadata.chapters
    )
    if outline != expected:
        raise SummarizationError(
            "Summary chapters must match the YouTube-provided outline exactly."
        )
    if not math.isfinite(metadata.duration_seconds) or metadata.duration_seconds <= 0:
        raise SummarizationError("Cannot validate summary without a video duration.")
    for claim in summary.notable_claims:
        if (
            claim.timestamp_seconds is not None
            and claim.timestamp_seconds > metadata.duration_seconds
        ):
            raise SummarizationError("Summary claim timestamp is outside the video.")
    normalized = [" ".join(segment.text.split()) for segment in transcript.segments]
    speech = " ".join(normalized)
    # Start offsets let a quote span multiple segments without losing its time.
    offsets: list[int] = []
    offset = 0
    for text in normalized:
        offsets.append(offset)
        offset += len(text) + 1
    for quote in summary.notable_quotes:
        words = " ".join(quote.quote.split())
        matches: list[int] = []
        position = speech.find(words)
        while position >= 0:
            # Do not accept a substring of a larger word as a literal quote.
            end = position + len(words)
            if (position == 0 or not speech[position - 1].isalnum()) and (
                end == len(speech) or not speech[end].isalnum()
            ):
                matches.append(position)
            position = speech.find(words, position + 1)
        if not matches:
            raise SummarizationError(
                "Summary quote is not grounded in transcript text."
            )
        time = quote.timestamp_seconds
        if time is None:
            continue
        if time > metadata.duration_seconds or not any(
            start <= match < start + len(text)
            and math.floor(segment.start_seconds) <= time <= segment.end_seconds
            for match in matches
            for start, text, segment in zip(
                offsets, normalized, transcript.segments, strict=True
            )
        ):
            raise SummarizationError(
                "Summary quote timestamp does not match its transcript segment."
            )


class Summarizer(Protocol):
    def preflight(self) -> None: ...

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary: ...
