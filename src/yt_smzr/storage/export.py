"""Separate readable transcript and summary exports."""

from yt_smzr.summarization.base import Summary
from yt_smzr.transcription.base import Transcript


def timestamp(seconds: float) -> str:
    """Format milliseconds, including hours, without minute rollover errors."""
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, fraction = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02}.{fraction:03}"


def transcript_markdown(transcript: Transcript) -> str:
    lines = [
        "# Transcript",
        "",
        f"Provider: {transcript.provider}",
        f"Model: {transcript.model}",
        f"Language: {transcript.language}",
        "",
    ]
    for segment in transcript.segments:
        text = " ".join(segment.text.split())
        lines.extend(
            [
                f"[{timestamp(segment.start_seconds)} - "
                f"{timestamp(segment.end_seconds)}] {text}",
                "",
            ]
        )
    return "\n".join(lines)


def transcript_json(transcript: Transcript) -> str:
    return transcript.model_dump_json(indent=2) + "\n"


def summary_markdown(summary: Summary) -> str:
    """Readable notes only; the transcript remains in separate exports."""
    lines = [
        "# Summary",
        "",
        f"Provider: {summary.provider}",
        f"Model: {summary.model}",
        "",
        "## Short summary",
        "",
        summary.short_summary,
        "",
        "## Detailed summary",
        "",
        summary.detailed_summary,
        "",
        "## Key points",
        "",
    ]
    lines.extend(f"- {point}" for point in summary.key_points)
    lines.extend(["", "## YouTube chapters", ""])
    for chapter in summary.chapters:
        end = (
            f" - {timestamp(chapter.end_seconds)}"
            if chapter.end_seconds is not None
            else ""
        )
        lines.extend(
            [
                f"### [{timestamp(chapter.start_seconds)}{end}] {chapter.title}",
                "",
                chapter.summary,
                "",
            ]
        )
    if not summary.chapters:
        lines.append("No YouTube-provided chapters.")
    lines.extend(
        [
            "",
            "## Notable claims",
            "",
            "Claims attributed to the video, not fact-checked.",
            "",
        ]
    )
    for claim in summary.notable_claims:
        time = (
            f"[{timestamp(claim.timestamp_seconds)}] "
            if claim.timestamp_seconds is not None
            else ""
        )
        lines.append(f"- {time}Video claims: {claim.claim}")
    if summary.notable_quotes:
        lines.extend(["", "## Notable quotes", ""])
        for quote in summary.notable_quotes:
            time = (
                f"[{timestamp(quote.timestamp_seconds)}] "
                if quote.timestamp_seconds is not None
                else ""
            )
            lines.append(f'- {time}"{quote.quote}"')
    return "\n".join(lines) + "\n"


def summary_json(summary: Summary) -> str:
    return summary.model_dump_json(indent=2) + "\n"
