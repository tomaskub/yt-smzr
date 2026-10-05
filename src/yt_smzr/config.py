"""Session, environment, dotfile, and default configuration."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import tomlkit


class ConfigurationError(ValueError):
    """Invalid configuration; safe to display without field values."""


def config_path() -> Path:
    return Path.home() / ".yt-smzr.toml"


def _error(path: Path, field_name: str) -> ConfigurationError:
    return ConfigurationError(f"Invalid settings in {path}: {field_name}.")


def validate_ollama_settings(settings: "Settings") -> None:
    try:
        url = urlsplit(settings.ollama_base_url)
        valid = (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
        )
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError(
            "ollama.base_url must be an HTTP/HTTPS URL without credentials, query, "
            "or fragment."
        )
    timeout = settings.ollama_timeout_seconds
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigurationError("ollama.timeout_seconds must be finite and positive.")


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot, including secrets excluded from repr."""

    output_dir: Path = Path(".yt-smzr")
    transcription_model: str = "small.en"
    summarization_provider: str = "openai"
    summarization_model: str | None = None
    summarizer_max_input_bytes: int = 100_000
    openai_api_key: str | None = field(default=None, repr=False)
    openrouter_api_key: str | None = field(default=None, repr=False)
    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_seconds: float = 120

    def __post_init__(self) -> None:
        if self.summarization_model is None:
            object.__setattr__(
                self,
                "summarization_model",
                "gpt-4.1-mini" if self.summarization_provider == "openai" else "",
            )

    openrouter_api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.summarization_model is None:
            object.__setattr__(
                self,
                "summarization_model",
                "gpt-4.1-mini" if self.summarization_provider == "openai" else "",
            )

    @classmethod
    def from_env(
        cls, *, path: Path | None = None, session: dict[str, object] | None = None
    ) -> "Settings":
        """Resolve explicit session > environment > dotfile > defaults."""
        location = path or config_path()
        document = read_document(location)
        values: dict[str, object] = {}
        mapping = {
            "summarization": {
                "provider": "summarization_provider",
                "model": "summarization_model",
            },
            "ollama": {
                "base_url": "ollama_base_url",
                "timeout_seconds": "ollama_timeout_seconds",
            },
        }
        for section, fields in mapping.items():
            table: Any = cast(dict[str, Any], document).get(section, {})
            if not isinstance(table, dict):
                raise _error(location, section)
            for key, name in fields.items():
                if key in table:
                    value: Any = cast(dict[str, Any], table)[key]
                    if key == "timeout_seconds":
                        if isinstance(value, bool) or not isinstance(
                            value, (int, float)
                        ):
                            raise _error(location, f"{section}.{key}")
                    elif not isinstance(value, str) or not value.strip():
                        raise _error(location, f"{section}.{key}")
                    values[name] = value
        for name, variable in ENV_FIELDS.items():
            if variable in os.environ:
                values[name] = os.environ[variable]
        values.update(session or {})
        provider = values.get("summarization_provider", "openai")
        if not isinstance(provider, str) or provider not in {
            "openai",
            "openrouter",
            "ollama",
        }:
            raise _error(location, "summarization.provider")
        if "summarization_model" not in values:
            values["summarization_model"] = (
                "gpt-4.1-mini" if provider == "openai" else ""
            )
        try:
            timeout = values.get("ollama_timeout_seconds", 120)
            if isinstance(timeout, bool):
                raise ValueError
            if not isinstance(timeout, (str, int, float)):
                raise ValueError
            resolved_timeout = float(timeout)
        except (TypeError, ValueError):
            raise _error(location, "ollama.timeout_seconds") from None
        try:
            input_limit = int(
                os.environ.get("YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES", "100000")
            )
        except ValueError:
            raise ConfigurationError(
                "YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES must be a positive integer."
            ) from None
        for name in ("summarization_model", "ollama_base_url"):
            value = values.get(name)
            if value is not None and not isinstance(value, str):
                raise _error(
                    location,
                    name.replace("summarization_", "summarization.").replace(
                        "ollama_", "ollama."
                    ),
                )
        settings = cls(
            output_dir=Path(os.environ.get("YT_SMZR_OUTPUT_DIR", ".yt-smzr")),
            transcription_model=os.environ.get(
                "YT_SMZR_TRANSCRIPTION_MODEL", "small.en"
            ),
            summarization_provider=str(values["summarization_provider"])
            if "summarization_provider" in values
            else "openai",
            summarization_model=str(values["summarization_model"]),
            summarizer_max_input_bytes=input_limit,
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
            ollama_base_url=str(
                values.get("ollama_base_url", "http://localhost:11434")
            ),
            ollama_timeout_seconds=resolved_timeout,
        )
        try:
            validate_ollama_settings(settings)
        except ConfigurationError as exc:
            raise ConfigurationError(f"Invalid settings in {location}: {exc}") from None
        return settings


ENV_FIELDS = {
    "summarization_provider": "YT_SMZR_SUMMARIZATION_PROVIDER",
    "summarization_model": "YT_SMZR_SUMMARIZATION_MODEL",
    "ollama_base_url": "YT_SMZR_OLLAMA_BASE_URL",
    "ollama_timeout_seconds": "YT_SMZR_OLLAMA_TIMEOUT_SECONDS",
}


def read_document(path: Path) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return tomlkit.document()
    except Exception:
        raise ConfigurationError(
            f"Cannot read valid TOML settings from {path}."
        ) from None


def save_settings(settings: Settings, *, path: Path | None = None) -> None:
    """Atomically update nonsecret selections, preserving unrelated TOML."""
    import tempfile

    location = path or config_path()
    validate_ollama_settings(settings)
    document = read_document(location)
    for section, entries in {
        "summarization": {
            "provider": settings.summarization_provider,
            "model": settings.summarization_model,
        },
        "ollama": {
            "base_url": settings.ollama_base_url,
            "timeout_seconds": settings.ollama_timeout_seconds,
        },
    }.items():
        table: Any = cast(dict[str, Any], document).get(section)
        if table is None:
            table = tomlkit.table()
            document[section] = table
        if not isinstance(table, dict):
            raise _error(location, section)
        for key, value in entries.items():
            table[key] = value
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{location.name}.",
            dir=location.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(tomlkit.dumps(document))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(location)
    except Exception:
        raise ConfigurationError(
            f"Cannot save settings to {location}; previous file retained."
        ) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
