"""Environment-based configuration entrypoint."""

import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigurationError(ValueError):
    """Invalid environment configuration; safe to display."""


@dataclass(frozen=True)
class Settings:
    """Local output, transcription, and summarization settings."""

    output_dir: Path = Path(".yt-smzr")
    transcription_model: str = "small.en"
    summarization_provider: str = "openai"
    summarization_model: str | None = None
    summarizer_max_input_bytes: int = 100_000
    openai_api_key: str | None = field(default=None, repr=False)

    openrouter_api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.summarization_model is None:
            object.__setattr__(
                self,
                "summarization_model",
                "gpt-4.1-mini" if self.summarization_provider == "openai" else "",
            )

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings without creating directories or loading providers."""
        try:
            input_limit = int(
                os.environ.get("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", "100000")
            )
        except ValueError:
            raise ConfigurationError(
                "YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES must be a positive integer."
            ) from None
        return cls(
            output_dir=Path(os.environ.get("YT_SMZR_OUTPUT_DIR", ".yt-smzr")),
            transcription_model=os.environ.get(
                "YT_SMZR_TRANSCRIPTION_MODEL", "small.en"
            ),
            summarization_provider=os.environ.get(
                "YT_SMZR_SUMMARIZATION_PROVIDER", "openai"
            ),
            summarization_model=os.environ.get("YT_SMZR_SUMMARIZATION_MODEL"),
            summarizer_max_input_bytes=input_limit,
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        )
