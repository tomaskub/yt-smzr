# Terminal review captures

Run `uv run --locked python tools/capture_tui.py` to regenerate the SVGs using
the same fake workflow as the TUI tests. No downloads, credentials, models, or
output files are created. Captures include deliberately long titles and paths.

| State | 80x24 | 120x40 | Monochrome 80x24 |
| --- | --- | --- | --- |
| Idle | [SVG](80x24-idle.svg) | [SVG](120x40-idle.svg) | [SVG](80x24-mono-idle.svg) |
| Confirmation | [SVG](80x24-confirmation.svg) | [SVG](120x40-confirmation.svg) | [SVG](80x24-mono-confirmation.svg) |
| Processing | [SVG](80x24-processing.svg) | [SVG](120x40-processing.svg) | [SVG](80x24-mono-processing.svg) |
| Completed | [SVG](80x24-completed.svg) | [SVG](120x40-completed.svg) | [SVG](80x24-mono-completed.svg) |
| Paths | [SVG](80x24-paths.svg) | [SVG](120x40-paths.svg) | [SVG](80x24-mono-paths.svg) |
| Failed | [SVG](80x24-failed.svg) | [SVG](120x40-failed.svg) | [SVG](80x24-mono-failed.svg) |
| Help | [SVG](80x24-help.svg) | [SVG](120x40-help.svg) | [SVG](80x24-mono-help.svg) |

Run `uv run --locked python tools/capture_settings.py` for settings SVG and PNG
review captures. It uses a fake installed-model loader and requires
`rsvg-convert` only for PNG rendering.

| Settings | 80x24 | 120x40 | Monochrome 80x24 |
| --- | --- | --- | --- |
| Dialog | [PNG](80x24-settings.png) | [PNG](120x40-settings.png) | [PNG](80x24-mono-settings.png) |

The resize behavior and keyboard reachability of long content are covered by
`tests/test_tui.py`. Metadata and paths remain scrollable rather than truncated
in application state. SVGs can be viewed in a browser or rendered with
`rsvg-convert capture.svg -o capture.png`.
