# Manual TUI inspection

Issue [#31](https://github.com/tomaskub/yt-smzr/issues/31) requests a real visible
terminal run with `https://www.youtube.com/watch?v=_5p1_TNSWqQ`.

## Inspection status

The manual inspection is blocked before launch. None of the workflow checks
below has passed. Existing fake captures in `docs/captures/` do not count as
evidence for this inspection.

## Environment checked on 2026-10-06

- Checkout: `codex/issue-31-manual-tui-inspection` at `be367a0`.
- Python: 3.12.11, using the main checkout's installed `.venv`.
- Textual: 8.2.8.
- yt-dlp: 2026.8.19, yt-dlp-ejs: 0.8.0.
- faster-whisper: 1.2.1, configured transcription model `small.en`.
- OpenAI client: 2.54.0, HTTPX: 0.28.1, Pydantic: 2.13.5.
- ffmpeg: 9.0.1. Deno: 2.9.5. Node: 26.8.1.
- Noninteractive execution resolves `openai / gpt-4.1-mini`. Neither hosted
  provider credential is present in that process. This does not establish the
  configuration of the user's interactive terminal session.
- Terminal dimensions and effective visible TUI provider/model: not observed.

No credential values were read into the report or tool output.

## Exact access attempts

1. `cua.getState()` identified Ghostty as a running native app. No browser
   surfaces were available.
2. `cua.getApp('com.mitchellh.ghostty')` failed with
   `Computer Use is not allowed to use the app 'com.mitchellh.ghostty' for safety reasons.`
3. `cua.getApp('Terminal')` was rejected by automatic approval review. Its stated
   reason was that selecting an alternate terminal after the Ghostty rejection
   appeared to circumvent the access restriction, and the user had not
   specifically authorized that workaround.
4. No further UI attempt or indirect automation was made. Explicit alternate
   terminal approval is pending.

This is an inspection-environment blocker, not an observed application defect.

## Required checks

| Check | Status | Evidence |
| --- | --- | --- |
| Visible idle layout, focus and hints | Blocked | Terminal access rejected before launch |
| Help and invoking-focus return | Blocked | No visible TUI |
| Settings and OpenRouter Apply | Blocked | No visible TUI; repeat after #30 fix |
| Fetch supplied video metadata and verify identity | Blocked | No submission |
| Cancel before download | Blocked | No preparation |
| Explicit confirmation and real audio download | Blocked | No processing |
| Real faster-whisper transcription | Blocked | No processing |
| Real provider summarization | Blocked | No processing |
| Export Markdown and JSON verification | Blocked | No outputs generated |
| Stage/event responsiveness and duplicate prevention | Blocked | No processing |
| Keyboard summary/transcript/path browsing and scrolling | Blocked | No results |
| Resize to 80x24 and 120x40 | Blocked | Terminal not accessible |
| Cache reuse | Blocked | No successful result |
| Force refresh and recoverable-failure preservation | Blocked | No successful result |
| Real idle/confirmation/processing/completed/help/settings screenshots | Blocked | No accessible terminal |

No application defects or usability suggestions have been observed. The
previously reported OpenRouter preflight defect remains tracked by
[#30](https://github.com/tomaskub/yt-smzr/issues/30).
