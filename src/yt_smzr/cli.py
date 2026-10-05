"""Command-line frontends for the reusable processing pipeline."""

import argparse
import sys
from collections.abc import Sequence

from yt_smzr import __version__
from yt_smzr.pipeline import Pipeline, PipelineError, PipelineEvent, PipelineStage


def _show_event(event: PipelineEvent) -> None:
    if event.stage != PipelineStage.FAILURE:
        print(f"[{event.stage.value}] {event.message}", flush=True)


def _summarize(url: str, *, force: bool, yes: bool) -> int:
    pipeline = Pipeline(on_event=_show_event)
    try:
        prepared = pipeline.prepare(url, force=force)
        metadata = prepared.metadata
        print(
            f"Title: {metadata.title}\n"
            f"Channel: {metadata.channel}\n"
            f"Duration: {metadata.duration_seconds:g} seconds\n"
            f"Video ID: {metadata.video_id}",
            flush=True,
        )
        if not yes:
            try:
                confirmed = input("Process this video? [y/N] ").strip().lower() in {
                    "y",
                    "yes",
                }
            except EOFError:
                confirmed = False
            if not confirmed:
                print("Cancelled. No processing started.", flush=True)
                return 0
        result = pipeline.process(prepared, confirmed=True)
        print(
            f"Transcript: {result.paths.transcript}\n"
            f"Transcript JSON: {result.paths.transcript_json}\n"
            f"Summary: {result.paths.summary}\n"
            f"Summary JSON: {result.paths.summary_json}",
            flush=True,
        )
        return 0
    except PipelineError as error:
        print(f"Error [{error.stage.value}]: {error}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr, flush=True)
        return 130


def main(argv: Sequence[str] | None = None) -> int:
    """Show help, launch the terminal UI, or summarize a single video."""
    parser = argparse.ArgumentParser(
        prog="yt-smzr",
        description="Turn YouTube videos into local transcripts and summaries.",
    )
    parser.add_argument("--version", action="version", version=f"yt-smzr {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("tui", help="Open the terminal UI")
    summarize = subparsers.add_parser("summarize", help="Summarize one YouTube video")
    summarize.add_argument("url", help="Single-video YouTube watch or youtu.be URL")
    summarize.add_argument(
        "--force",
        action="store_true",
        help="Refresh cached results; validation still applies",
    )
    summarize.add_argument(
        "--yes", action="store_true", help="Skip confirmation after displaying metadata"
    )
    args = parser.parse_args(argv)

    if args.command == "summarize":
        return _summarize(args.url, force=args.force, yes=args.yes)
    if args.command == "tui":
        from yt_smzr.tui import run

        run()
    else:
        parser.print_help()
    return 0
