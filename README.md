# yt-smzr

A local terminal app for turning YouTube videos into transcripts and summaries.
This bootstrap includes package installation, CLI entrypoints, and a Textual
welcome screen, reusable YouTube URL validation and metadata fetching, local
cache storage, confirmed audio downloads, and timestamped transcription.
Summarization and the connected workflow will be added in later issues.

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
the directory. `YT_SMZR_TRANSCRIPTION_MODEL` selects a faster-whisper model or
local model directory, defaulting to `small.en`. The full workflow will also require `ffmpeg`, a supported
JavaScript runtime such as Deno for YouTube extraction, and OpenAI configuration
described in [the MVP PRD](docs/prd-mvp.md). The `yt-dlp[default]` dependency
includes the YouTube JavaScript challenge solver.

## Development

The [Quality workflow](.github/workflows/quality.yml) runs on pull requests and
pushes to `main` using Ubuntu and Python from `.python-version`. It installs
uv 0.12.5 and the locked runtime and development dependencies, then checks
lint, formatting, types, and tests. To reproduce the CI checks locally with
the same uv version:

```sh
uv python install
uv sync --locked --dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest
```

`--locked` rejects an outdated `uv.lock` instead of updating it during checks.
The checks require no API keys or model downloads. To build a distribution
locally, also run:

```sh
uv build
```

The smoke tests cover package imports, the installed console command, the
module entrypoint, and a headless Textual startup and quit. They do not download
videos or call model providers.

The package lives in `src/yt_smzr/`. CLI and TUI code are separate from
configuration, models, and the pipeline. The `youtube`, `transcription`,
`summarization`, and `storage` packages reserve the integration boundaries for
later work. `yt_smzr.pipeline.prepare_video(url)` returns typed metadata for
confirmation without downloading audio. It accepts single-video watch and
`youtu.be` links, preserves the original URL and YouTube chapters, and rejects
explicit Shorts, live videos, unknown durations, and videos over two hours.
The CLI and TUI will call this boundary in later workflow slices.

`yt_smzr.youtube.audio.download_audio(metadata, store, confirmed=True)` downloads
compressed audio after the caller confirms metadata. It reuses nonempty cached
audio unless `force=True`, and rejects mismatched IDs or invalid durations even
on a forced run. New audio stays in a temporary directory until download succeeds;
publication or cache-write failures restore the previous audio and remove partial
files. The full pipeline must use a separate staging `CacheStore` for a refresh,
then publish the whole run after transcription and summarization succeed.

Audio preflight requires `ffmpeg` on PATH, the locked `yt-dlp[default]` packages,
and Deno 2.3.0 or newer or Node 22.0.0 or newer. It checks executable versions
without printing tool output. These runtime minimums follow the
[yt-dlp EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
The adapter enables Deno and Node explicitly and uses the installed EJS scripts.
It selects `bestaudio` when available and lets ffmpeg extract compressed audio
without transcoding when the input codec can be retained.

`yt_smzr.transcription.service.transcribe_audio(video_id, store)` transcribes the
record's retained audio without launching the UI. It writes `transcript.md` and
`transcript.json`, preserving start/end timestamps, and records the provider and
model in the cache. It reuses valid existing exports unless `force=True`. Alternate
backends implement `Transcriber.preflight()` and `Transcriber.transcribe(path)`.

The default backend uses faster-whisper on CPU with int8 computation and English
transcription. Preflight checks installed dependencies and a nonempty model setting
without loading models or contacting the network. The first transcription with a
model name can download model weights; a local model directory avoids that download.
Backend and export failures have contextual errors. Failed replacement restores
previous transcript files and leaves the cache record intact. A full refresh must
pass a separate staging store until summarization succeeds, as for audio downloads.
The adapter follows the [faster-whisper usage documentation](https://github.com/SYSTRAN/faster-whisper#usage),
including consuming its lazy segment iterator inside the error boundary.
