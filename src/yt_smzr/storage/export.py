"""Readable timestamped transcript exports."""

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
