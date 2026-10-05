"""Keep extractor diagnostics out of frontend output and display safe stage errors."""


class SilentLogger:
    """yt-dlp requires all three methods, including debug for ordinary output."""

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


SILENT_LOGGER = SilentLogger()
