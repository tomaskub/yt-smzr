# yt-smzr

A local terminal app for turning YouTube videos into transcripts and summaries.
This bootstrap includes package installation, CLI entrypoints, and a Textual
welcome screen, reusable YouTube URL validation and metadata fetching, local
cache storage, confirmed audio downloads, timestamped transcription, and
structured summaries, a reusable end-to-end pipeline, and a CLI summarization
workflow. TUI workflow controls will be added in a later issue.

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

## CLI summarization

With the workflow dependencies and OpenAI configuration described below installed:

```sh
uv run yt-smzr summarize 'https://youtu.be/dQw4w9WgXcQ'
uv run yt-smzr summarize 'https://youtu.be/dQw4w9WgXcQ' --yes
uv run yt-smzr summarize 'https://youtu.be/dQw4w9WgXcQ' --force
```

The command shows title, channel, duration, and video ID before asking
`Process this video? [y/N]`. Enter `y` or `yes` to proceed. Empty input, other
answers, or end-of-input cancel before processing. `--yes` skips the prompt for
automation while still displaying metadata. `--force` refreshes cached results
and keeps all validation limits in place.

Stage messages appear during processing. A successful run prints the transcript
and summary Markdown and JSON paths. Processing errors print stage context to
stderr and exit with status 1; cancellation exits with status 0, and keyboard
interruption exits with status 130. The default output directory is `.yt-smzr/`
in the current working directory.

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
The CLI uses the full `Pipeline.prepare` boundary described below.

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

`yt_smzr.summarization.service.summarize_transcript(metadata, transcript, store)`
generates validated notes and writes separate `summary.md` and `summary.json`
exports in the video cache directory. It records the provider/model and reuses
existing valid summary exports unless `force=True`. Alternate providers implement
`Summarizer.preflight()` and `Summarizer.summarize(metadata, transcript)`. The stage
restores both summary files when export replacement or cache publication fails.
Whole-run refreshes must use a staging store until every stage succeeds.

Summarization configuration uses these environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_API_KEY` | Required | OpenAI credential, excluded from settings repr and artifacts. |
| `YT_SMZR_SUMMARIZATION_PROVIDER` | `openai` | First supported summarization adapter. |
| `YT_SMZR_SUMMARIZATION_MODEL` | `gpt-4.1-mini` | Model that supports structured outputs. |
| `YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES` | `100000` | Positive integer limit for one serialized input. |

The limit counts UTF-8 bytes of compact JSON containing the exact trusted
`instructions` string and serialized `input` string sent to the provider. It
includes metadata, chapter outlines, segment start/end timestamps, speech,
JSON escaping, and instructions. It excludes the schema and transport settings;
it is an application input limit, not a token count or a guarantee that any
configured model accepts the request. Oversized input fails before constructing
the client or calling an injected summarizer. No chunking or map-reduce is used.

OpenAI preflight checks configuration and the installed SDK without constructing
a client or contacting a provider. The adapter uses the Responses API with
Pydantic structured output, one call without automatic retries, disabled input
truncation, and `store=False`. Refusals, incomplete responses, absent parsed output,
and provider failures produce contextual messages without echoing remote errors.
The request follows the [official OpenAI structured outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
The default model supports structured outputs according to the
[GPT-4.1 mini documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

Summary chapters must retain the exact YouTube title, order, and start/end times;
no supplied chapters means an empty outline. Chapter seconds retain fractional
precision from `VideoMetadata`. Claims are attributed to the video, not
independently fact-checked. Quotes must match transcript words, capitalization,
and punctuation after whitespace normalization. A quote can span adjacent
segments. Its optional integer timestamp must fall within the segment containing
the quote's start, allowing its fractional start to round down. An empty
`notable_quotes` list means no grounded quotes were selected. Summary exports
contain notes and selected quotes, never the transcript segment collection.
Tests use fake providers and the real SDK with an in-memory HTTP transport.


## Reusable pipeline

The full workflow runs independently of Textual. Both frontends can use the same
confirmation boundary:

```python
from yt_smzr.pipeline import Pipeline, PipelineError

pipeline = Pipeline(on_event=lambda event: print(event.stage.value, event.message))
try:
    prepared = pipeline.prepare("https://youtu.be/dQw4w9WgXcQ", force=False)
    print(prepared.metadata.title, prepared.metadata.duration_seconds)
    confirmed = input("Process this video? [y/N] ").lower() == "y"
    result = pipeline.process(prepared, confirmed=confirmed)
    print(result.summary.short_summary)
    print(result.paths.transcript, result.paths.summary)
except PipelineError as error:
    print(error.stage.value, str(error))
```

`prepare` validates the URL and full local dependencies/configuration before
reading cached metadata or contacting YouTube. It returns `PreparedVideo` with
metadata, the force choice, and `metadata_cached`. It writes no artifacts.
A declined confirmation performs no download, transcription, or summarization.
`process` rechecks dependencies and metadata limits, then returns `PipelineResult`
with typed metadata, transcript, summary, artifact paths, retained audio path,
completed cache record, and `reused_stages`. Its `cache_hit` property reports
whether audio, transcript, and summary were all reused. `run` aliases `process`.
The older `prepare_video` helper remains metadata-only and performs no full
workflow preflight.

Repeated runs reuse valid metadata and audio, plus validated transcript/summary
JSON. Missing Markdown exports are regenerated from JSON without provider calls.
Missing or invalid JSON triggers only the affected work; force refresh bypasses
all cache reuse. Neither force nor cache hits bypass URL, duration, or input-size
limits. Metadata cache misses fetch fresh YouTube metadata for confirmation.

Every run stages metadata, audio, exports, and cache fields in a separate local
store. The pipeline publishes the video directory and completed SQLite record
after all stages succeed, restoring the previous directory if publication or the
record save fails. Failed runs remove their staged artifacts. If restoration
itself fails, the contextual error identifies the retained previous backup for
recovery. Cleanup after a committed success cannot turn it into a failed run.
This protects against handled failures, not process termination or power loss
between filesystem and SQLite operations. Use one active run per video and output
directory.

`PipelineStage` exposes preflight, metadata, confirmation, download, transcription,
summarization, cache/export, completion, and failure. Immutable `PipelineEvent`
objects contain a stage and concise message. Calls and callbacks run synchronously
on the calling thread. Frontends should run the pipeline in a worker and marshal
events to their UI thread. Observer exceptions are ignored so display failures
cannot interrupt core work or misreport a committed result. `PipelineError.stage`
identifies the failed stage, and its message is safe to display without a traceback.
Tests inject metadata extractors, downloaders, preflight checks, transcribers,
summarizers, and stores; they use no network, downloaded models, or live providers.
