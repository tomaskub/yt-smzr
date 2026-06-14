# Product Context: YouTube Video Summarizer TUI

I am building a personal-use terminal application for summarizing YouTube videos. The app should let me paste a YouTube URL, download or extract the audio, transcribe it with Whisper, and then use an LLM to produce useful summaries and notes.

The first version should be built in Python using Textual for the terminal UI. It should prioritize simplicity, speed of iteration, and clean separation between the UI and the processing pipeline.

## Core Workflow

1. User enters a YouTube URL.
2. App fetches video metadata such as title, channel, duration, and video ID.
3. App downloads/extracts the audio using yt-dlp and ffmpeg.
4. App transcribes the audio using Whisper, preferably with a local option such as faster-whisper or whisper.cpp.
5. App chunks the transcript if needed.
6. App sends transcript chunks to an LLM for summarization.
7. App produces:
   - short summary
   - detailed summary
   - key points
   - chapter-style outline with timestamps
   - notable quotes or claims
   - optional action items
8. App saves transcript and summary locally for reuse.

## Intended User Experience

The app should feel like a focused terminal productivity tool, not a complex media downloader. The user should be able to paste a URL, watch progress through download/transcription/summarization stages, and then browse the results in the terminal.

The UI can include:
- URL input field
- progress/status area
- video metadata panel
- summary panel
- transcript panel
- keybindings for switching views
- export options for Markdown, JSON, or plain text

## Technical Preferences

Use Python for the initial implementation.

Suggested libraries:
- textual for the terminal UI
- yt-dlp for downloading audio and metadata
- ffmpeg for audio conversion
- faster-whisper or whisper.cpp for local transcription
- OpenAI, Anthropic, Ollama, or another LLM provider for summarization
- sqlite for caching metadata, transcripts, and summaries
- pydantic for typed structured outputs and validation

## Architecture Goals

The app should be modular. The Textual UI should not contain the core business logic. Instead, the app should have a reusable pipeline layer with separate components for:

- video metadata fetching
- audio download/extraction
- transcription
- transcript chunking
- summarization
- local storage/cache
- export generation

This separation should make it possible to later replace the Textual UI with another frontend, such as a Go TUI, web UI, or native Swift/macOS app.

## Data Model

The app should cache results by YouTube video ID. A possible local record includes:

- video ID
- URL
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

## First-Version Scope

The first version should be small and functional:

- paste a YouTube URL
- download audio
- transcribe audio
- summarize transcript
- display summary in Textual
- save transcript and summary as Markdown files

Avoid over-engineering authentication, playlists, background queues, multiple users, cloud sync, or advanced configuration in the first version.

## Product Principle

The main goal is to create a reliable personal tool that turns long YouTube videos into readable notes quickly. The implementation should favor clear code, hackable structure, and easy local experimentation over production-grade complexity.
