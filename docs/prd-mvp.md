# PRD: YouTube Video Summarizer MVP

## Problem Statement

Long YouTube videos often hide whether they are worth watching until after a large time investment. The user needs a fast personal terminal tool that can turn a pasted YouTube URL into readable notes, so they can judge content quality, extract useful ideas, and avoid sitting through repetitive or low-value videos.

## Goals

- Provide a working proof-of-concept terminal app for summarizing one YouTube video at a time.
- Keep the first version simple, local, and hackable.
- Separate the Textual UI from the processing pipeline so the core workflow can later support other frontends.
- Save generated transcripts and summaries locally for reuse.

## Non-Goals

- Playlists, channels, subscriptions, or batch queues.
- Multi-user accounts, authentication, or cloud sync.
- Advanced configuration UI.
- Production-grade job orchestration.
- Perfect transcript accuracy across all video types.
- Full knowledge-management integrations.

## Target User

The initial user is the developer building the tool: someone comfortable running a Python terminal app locally, installing command-line dependencies, and experimenting with local or hosted AI models.

## Core User Stories

1. As a user, I want to paste a YouTube URL, so that I can summarize a video without leaving the terminal.
2. As a user, I want the app to fetch video title, channel, duration, and ID, so that I can confirm I am processing the right video.
3. As a user, I want the app to extract audio automatically, so that I do not need to manage media files manually.
4. As a user, I want the app to transcribe the audio, so that the summary is based on the spoken content.
5. As a user, I want to see progress through metadata, download, transcription, and summarization stages, so that long-running work does not feel stuck.
6. As a user, I want a short summary, so that I can quickly decide whether the video is worth more attention.
7. As a user, I want a detailed summary and key points, so that I can capture the substance of the video.
8. As a user, I want a chapter-style outline with timestamps when available, so that I can jump back to useful parts of the video.
9. As a user, I want the transcript and summary saved as Markdown files, so that I can reuse them outside the app.
10. As a user, I want repeated runs for the same video to reuse local results where possible, so that I do not waste time or compute.

## MVP Scope

The MVP supports the happy path for a single YouTube URL:

1. Accept a URL in a Textual UI.
2. Fetch metadata with `yt-dlp`.
3. Show fetched metadata and require user confirmation before downloading audio.
4. Download or extract audio with `yt-dlp` and `ffmpeg`.
5. Transcribe audio with `faster-whisper`.
6. Reject transcripts that exceed the configured single-call summarization limit.
7. Summarize the transcript with OpenAI.
8. Display metadata, summary, and transcript in the terminal.
9. Save transcript and summary Markdown and JSON files locally.
10. Cache records by YouTube video ID.

## Product Requirements

- The UI must make the current processing stage visible.
- The user must be able to start from a pasted URL with minimal setup beyond required dependencies.
- The app must show enough metadata to identify the video before or during processing.
- The final summary view must include:
  - short summary
  - detailed summary
  - key points
  - YouTube-provided chapter outline with timestamps when available
  - notable claims
  - notable quotes when clearly grounded in the transcript
- The app must save generated output to local files.
- The app must avoid mixing Textual UI code with core pipeline logic.
- The app must reject videos over 2 hours before audio download.
- The app must reject explicit YouTube Shorts URLs.
- The app must preserve transcript segment timestamps.
- The app must expose a force-refresh option in the Textual UI.

## Technical Requirements

- Use Python for the first implementation.
- Use Textual for the terminal UI.
- Use `uv` and `pyproject.toml` for dependency management, packaging, and console scripts.
- Use `yt-dlp` through its Python API for metadata and audio download.
- Use `ffmpeg` for audio conversion when required.
- Use `faster-whisper` as the default local transcription backend.
- Use OpenAI as the first summarization provider behind a small interface.
- Use structured summary output validated with Pydantic or equivalent typed models.
- Use SQLite for local cache metadata, keyed by YouTube video ID.
- Store artifacts in a predictable project-local `.yt-smzr/` output directory.
- Use environment variables for configuration.
- Use `pytest`, `ruff`, and `pyright` for tests, linting/formatting, and type checking.

## Suggested Architecture

The MVP should be organized around a reusable pipeline layer with components for:

- video metadata fetching
- audio download and extraction
- transcription
- transcript chunking
- summarization
- local cache/storage
- Markdown export

The Textual app should call the pipeline and render state, but should not own the core workflow decisions.

Suggested package layout:

```text
src/yt_smzr/
  __init__.py
  cli.py
  config.py
  models.py
  pipeline.py
  tui.py

  youtube/
    metadata.py
    audio.py
    urls.py

  transcription/
    base.py
    faster_whisper.py

  summarization/
    base.py
    openai_provider.py
    prompts.py

  storage/
    paths.py
    sqlite.py
    export.py
```

The MVP also includes a thin CLI entrypoint:

```bash
yt-smzr summarize <youtube-url> [--force] [--yes]
```

The CLI fetches and prints metadata, then asks for confirmation before audio download/transcription. Passing `--yes` skips the prompt for automation.

## MVP Decisions

- **Transcription backend:** Use `faster-whisper` first, behind a backend-agnostic transcription interface.
- **Summarization provider:** Use OpenAI first, behind a provider-neutral summarizer interface. The OpenAI model is configurable and must support the structured summary output expected by the app.
- **Apple models:** Apple Foundation Models are not part of the MVP because they are exposed through native Apple-platform APIs rather than a simple Python interface. They may be added later through a Swift adapter.
- **Cache storage:** Use SQLite for cache metadata and store large artifacts as files.
- **Output directory:** Write outputs to `.yt-smzr/` in the current working directory by default.
- **Export formats:** Write both Markdown and JSON outputs.
- **Cache reuse:** Reuse any existing cached artifact for the same video ID. `--force` bypasses cache reuse but does not override validation limits.
- **Force refresh safety:** During a force refresh, existing cached outputs remain available until the replacement run completes successfully.
- **Failed runs:** Remove artifacts created during the failed run. Existing successful cached artifacts from previous runs are preserved.
- **Chapters:** Include chapter outlines only when YouTube provides chapters. Do not infer chapters in the MVP.
- **Transcript timestamps:** Preserve segment-level timestamps in transcript Markdown and JSON.
- **Notable claims and quotes:** Notable claims are required structured output. Notable quotes are optional and must be grounded in transcript text.
- **Language scope:** Target English-language videos only.
- **Duration cap:** Reject videos longer than 2 hours after metadata fetch and before audio download. `--force` does not override this cap.
- **Transcript source:** Always download/extract audio and transcribe with `faster-whisper`; do not use YouTube captions as transcript source.
- **Audio retention:** Keep the compressed audio artifact in the video cache directory.
- **Summarizer input:** Send timestamped transcript segments to the summarizer.
- **Long transcripts:** Reject transcripts that exceed the configured single-call summarizer input limit. Do not implement map-reduce summarization in the MVP.
- **Configuration:** Use environment variables only for provider/model/output limits. Use CLI/TUI controls for per-run inputs such as URL and force refresh.
- **Preflight checks:** Validate required dependencies and configuration at run start.
- **UI shape:** Use a single-screen Textual UI with URL input, force-refresh toggle, metadata confirmation, current stage, concise event log, summary, transcript, and output paths.
- **Progress:** Display coarse pipeline stages plus a concise event log. Fine-grained progress bars are out of scope.
- **URL scope:** Accept regular single-video YouTube watch URLs and `youtu.be/<id>` links. Reject explicit `/shorts/<id>` URLs and playlist/channel/search URLs unless a single video ID is present.

## Data Requirements

Each cached video record should include:

- video ID
- original URL
- title
- channel
- duration
- audio file path
- transcript file path
- summary file path
- transcription model used
- summarization model used
- created timestamp
- updated timestamp
- metadata file path
- transcript JSON file path
- summary JSON file path

SQLite stores cache metadata, run state, timestamps, provider/model names, and artifact file paths. Full transcript and summary contents are stored in JSON and Markdown files under the video cache directory.

Default local output shape:

```text
.yt-smzr/
  yt-smzr.sqlite
  videos/
    <video_id>/
      audio.<ext>
      metadata.json
      transcript.md
      transcript.json
      summary.md
      summary.json
```

Summary and transcript exports remain separate files. `summary.md` should not embed the full transcript.

The structured summary schema should include:

```text
short_summary: str
detailed_summary: str
key_points: list[str]
chapters: list[Chapter]
notable_claims: list[NotableClaim]
notable_quotes: list[NotableQuote]

Chapter:
  title: str
  start_seconds: int
  end_seconds: int | None
  summary: str

NotableClaim:
  claim: str
  timestamp_seconds: int | None

NotableQuote:
  quote: str
  timestamp_seconds: int | None
```

## Error Handling Requirements

The MVP should handle common failures with clear terminal messages:

- invalid or unsupported URL
- metadata fetch failure
- audio download failure
- missing `ffmpeg`
- transcription failure
- summarization provider failure
- failed file writes
- video exceeds duration cap
- explicit Shorts URL
- transcript exceeds single-call summarization limit
- missing required configuration such as `OPENAI_API_KEY`

The first version does not need sophisticated retry behavior, but failures should not crash without context.

Before a run begins, the app validates required dependencies and configuration for the full workflow, including `yt-dlp`, `ffmpeg`, `faster-whisper`, and OpenAI configuration.

## Testing Decisions

- Test the pipeline behavior at the highest practical seam, using fakes for external tools and model providers.
- Unit test URL/video ID handling, cache lookup behavior, transcript chunking, summary model validation, and Markdown export.
- Keep Textual UI tests minimal for the MVP; prioritize verifying that the UI can start, accept input, and render pipeline states.
- Avoid tests that depend on downloading real YouTube videos or calling real LLM providers.

## Acceptance Criteria

- A user can run the app locally and paste a YouTube URL.
- The app fetches and displays basic video metadata.
- The app downloads/extracts audio for that video.
- The app transcribes the audio.
- The app generates a structured summary.
- The app displays the summary in the Textual UI.
- The app writes transcript and summary Markdown files locally.
- Running the same video again can reuse cached outputs.
- The core pipeline can be invoked without launching the Textual UI.
- The CLI can invoke the same pipeline without launching the Textual UI.
- The TUI requires confirmation after metadata fetch before audio download starts.
- `--force` and the TUI force-refresh toggle bypass cache reuse without deleting previous successful outputs unless the replacement run succeeds.
- Videos over 2 hours are rejected before audio download.
- Explicit YouTube Shorts URLs are rejected.
- Transcript and summary JSON files are written alongside Markdown files.

## Open Questions

No open MVP product decisions remain from the initial PRD grilling session.
