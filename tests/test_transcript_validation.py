"""Transcript boundaries tolerate numeric noise without losing speech or timing."""

import json

import pytest
from pydantic import ValidationError

from yt_smzr.storage.export import transcript_json
from yt_smzr.transcription.base import Transcript, TranscriptSegment


def test_observed_whisper_boundary_preserves_every_timestamp_and_word() -> None:
    segments = (
        TranscriptSegment(
            start_seconds=325.64000000000004,
            end_seconds=328.76000000000005,
            text="Speech before the boundary.",
        ),
        TranscriptSegment(
            start_seconds=328.76,
            end_seconds=335.0,
            text="Speech after the boundary.",
        ),
    )

    transcript = Transcript(
        provider="faster-whisper", model="tiny.en", segments=segments
    )

    assert transcript.segments == segments
    exported = transcript_json(transcript)
    assert json.loads(exported)["segments"] == [
        segment.model_dump() for segment in segments
    ]
    assert Transcript.model_validate_json(exported) == transcript


@pytest.mark.parametrize("next_start", [2 - 5e-10, 2, 2.5])
def test_numeric_noise_touching_boundaries_and_gaps_are_valid(
    next_start: float,
) -> None:
    transcript = Transcript(
        provider="fake",
        model="test",
        segments=(
            TranscriptSegment(start_seconds=1, end_seconds=2, text="First."),
            TranscriptSegment(start_seconds=next_start, end_seconds=3, text="Second."),
        ),
    )

    assert transcript.segments[1].start_seconds == next_start


@pytest.mark.parametrize(
    ("previous_end", "next_start"),
    [
        (328.76000000000005, 328.75),
        (2, 2 - 2e-9),
        (7200, 7200 - 1e-6),
    ],
)
def test_overlap_above_absolute_tolerance_is_rejected(
    previous_end: float, next_start: float
) -> None:
    with pytest.raises(ValidationError, match="ordered without overlap"):
        Transcript(
            provider="fake",
            model="test",
            segments=(
                TranscriptSegment(
                    start_seconds=previous_end - 1,
                    end_seconds=previous_end,
                    text="First.",
                ),
                TranscriptSegment(
                    start_seconds=next_start,
                    end_seconds=previous_end + 1,
                    text="Second.",
                ),
            ),
        )


@pytest.mark.parametrize("next_start", [0, 1 - 5e-10])
def test_reversed_starts_are_rejected_even_with_zero_length_segment(
    next_start: float,
) -> None:
    with pytest.raises(ValidationError, match="ordered without overlap"):
        Transcript(
            provider="fake",
            model="test",
            segments=(
                TranscriptSegment(start_seconds=1, end_seconds=1, text="First."),
                TranscriptSegment(
                    start_seconds=next_start, end_seconds=2, text="Second."
                ),
            ),
        )
