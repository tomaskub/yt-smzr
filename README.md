# yt-smzr

A local terminal app for turning YouTube videos into transcripts and summaries.
This bootstrap includes package installation, CLI entrypoints, and a Textual
welcome screen. Video fetching, transcription, summarization, and caching will
be added in later issues.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run:

```sh
uv sync --locked
uv run yt-smzr --help
uv run yt-smzr --version
uv run yt-smzr tui
```

Python 3.12 is the development baseline, selected by `.python-version`. Python
3.13 is also supported. Python 3.14 is excluded for now because transcription
dependencies do not provide wheels for all intended platforms. `uv sync`
installs the package, runtime libraries, and development tools into `.venv`.
The installed command is also available as `.venv/bin/yt-smzr`.

Running `yt-smzr` without arguments prints help. `python -m yt_smzr` exposes
the same commands. Press `q` to close the welcome screen. These entrypoints
do not require an API key, download models, or contact YouTube.

`Settings.from_env()` in `src/yt_smzr/config.py` reads `YT_SMZR_OUTPUT_DIR`, which
defaults to `.yt-smzr/` relative to the current directory. It does not create
the directory. Provider settings and workflow validation will come with later
processing slices. The full workflow will also require `ffmpeg`, a supported
JavaScript runtime such as Deno for YouTube extraction, and OpenAI configuration
described in [the MVP PRD](docs/prd-mvp.md). The `yt-dlp[default]` dependency
includes the YouTube JavaScript challenge solver.

## Development

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv build
```

The smoke tests cover package imports, the installed console command, the
module entrypoint, and a headless Textual startup and quit. They do not download
videos or call model providers.

The package lives in `src/yt_smzr/`. CLI and TUI code are separate from
configuration, models, and the pipeline. The `youtube`, `transcription`,
`summarization`, and `storage` packages reserve the integration boundaries for
later work. The processing modules currently contain no implementation.
