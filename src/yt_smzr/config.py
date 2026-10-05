"""Environment-based configuration entrypoint."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Local output location. Provider settings will be added with processing."""

    output_dir: Path = Path(".yt-smzr")

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings without creating directories or loading providers."""
        return cls(output_dir=Path(os.environ.get("YT_SMZR_OUTPUT_DIR", ".yt-smzr")))
