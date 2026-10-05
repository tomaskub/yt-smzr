"""Bounded Ollama requests with schema validation and no implicit model pulls."""

from typing import Any, cast

import httpx

from yt_smzr.config import ConfigurationError, Settings, validate_ollama_settings
from yt_smzr.models import VideoMetadata
from yt_smzr.summarization.base import (
    SummarizationError,
    Summary,
    SummaryContent,
    validate_grounding,
)
from yt_smzr.summarization.prompts import check_input_limit, prepare_input
from yt_smzr.transcription.base import Transcript


def _request(
    settings: Settings,
    method: str,
    endpoint: str,
    *,
    payload: dict[str, Any] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Any:
    try:
        validate_ollama_settings(settings)
        with httpx.Client(
            timeout=settings.ollama_timeout_seconds,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.request(
                method,
                settings.ollama_base_url.rstrip("/") + endpoint,
                json=payload,
            )
        if response.status_code == 404:
            raise SummarizationError(
                "Ollama model or endpoint was not found. Check the server URL and "
                "installed model tag; install the model yourself with ollama pull."
            )
        if response.is_error:
            raise SummarizationError(
                "Ollama rejected the request. Check the model's structured-output "
                "support, context size, and server resources."
            )
        return response.json()
    except ConfigurationError as exc:
        raise SummarizationError(str(exc)) from None
    except SummarizationError:
        raise
    except httpx.TimeoutException:
        raise SummarizationError(
            "Ollama request timed out. Check server resources or increase the "
            "configured request timeout."
        ) from None
    except httpx.RequestError:
        raise SummarizationError(
            "Cannot reach Ollama. Start ollama serve and check the configured "
            "server URL and connectivity."
        ) from None
    except Exception:
        raise SummarizationError("Ollama returned an invalid response.") from None


def list_models(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> tuple[str, ...]:
    """List installed models only when explicitly requested by the caller."""
    data = _request(settings, "GET", "/api/tags", transport=transport)
    try:
        if not isinstance(data, dict):
            raise ValueError
        mapping = cast(dict[str, Any], data)
        if not isinstance(mapping.get("models"), list):
            raise ValueError
        names: list[str] = []
        for item in cast(list[Any], mapping["models"]):
            if not isinstance(item, dict):
                raise ValueError
            name: Any = cast(dict[str, Any], item).get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError
            names.append(name)
        return tuple(dict.fromkeys(names))
    except Exception:
        raise SummarizationError(
            "Ollama returned an invalid installed-model list."
        ) from None


class OllamaSummarizer:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    def preflight(self) -> None:
        """Validate configuration without contacting a server or loading a model."""
        try:
            validate_ollama_settings(self.settings)
        except ConfigurationError as exc:
            raise SummarizationError(str(exc)) from None
        if self.settings.summarization_provider != "ollama":
            raise SummarizationError("Summarization provider must be ollama.")
        if (
            not self.settings.summarization_model
            or not self.settings.summarization_model.strip()
        ):
            raise SummarizationError("Ollama requires an explicit installed model tag.")
        limit = self.settings.summarizer_max_input_bytes
        if isinstance(limit, bool) or limit <= 0:
            raise SummarizationError(
                "Summarizer input limit must be a positive integer."
            )

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary:
        self.preflight()
        source = prepare_input(metadata, transcript)
        check_input_limit(source, self.settings.summarizer_max_input_bytes)
        model = self.settings.summarization_model
        assert model is not None
        data = _request(
            self.settings,
            "POST",
            "/api/chat",
            payload={
                "model": self.settings.summarization_model,
                "stream": False,
                "format": SummaryContent.model_json_schema(),
                "messages": [
                    {"role": "system", "content": source.instructions},
                    {"role": "user", "content": source.source},
                ],
                "options": {"temperature": 0},
            },
            transport=self.transport,
        )
        try:
            if not isinstance(data, dict):
                raise ValueError
            result = cast(dict[str, Any], data)
            if result.get("done") is not True:
                raise SummarizationError(
                    "Ollama returned an incomplete summary. Check model context "
                    "size and server resources."
                )
            if result.get("done_reason") not in (None, "stop"):
                raise SummarizationError(
                    "Ollama stopped before completing the summary. Check model "
                    "context and output limits."
                )
            content = SummaryContent.model_validate_json(result["message"]["content"])
            validate_grounding(content, metadata, transcript)
            return Summary(
                **content.model_dump(),
                provider="ollama",
                model=model,
            )
        except SummarizationError:
            raise
        except Exception:
            raise SummarizationError(
                "Ollama returned invalid structured summary output. Use a model "
                "that supports JSON Schema and has enough context for this transcript."
            ) from None
