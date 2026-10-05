"""Command-line entrypoint. Processing commands will be added in later slices."""

import argparse
from collections.abc import Sequence

from yt_smzr import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Show help by default, or launch the placeholder terminal UI."""
    parser = argparse.ArgumentParser(
        prog="yt-smzr",
        description="YouTube summaries. Video processing is not implemented yet.",
    )
    parser.add_argument("--version", action="version", version=f"yt-smzr {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("tui", help="Open the placeholder terminal UI")
    args = parser.parse_args(argv)

    if args.command == "tui":
        from yt_smzr.tui import run

        run()
    else:
        parser.print_help()
    return 0
