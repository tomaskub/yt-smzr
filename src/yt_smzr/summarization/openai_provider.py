"""Lazy OpenAI Responses structured-output adapter, with no preflight network."""

import importlib.util
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from openai import OpenAI

from yt_smzr.config import ConfigurationError, Settings
from yt_smzr.models import VideoMetadata
from yt_smzr.summarization.base import (
    SummarizationError,
    Summary,
    SummaryContent,
    validate_grounding,
)
from yt_smzr.summarization.prompts import check_input_limit, prepare_input
from yt_smzr.transcription.base import Transcript


class ClientFactory(Protocol):
    def __call__(self, api_key: str, /) -> "OpenAI": ...


def create_client(api_key: str) -> "OpenAI":
    from openai import OpenAI

    # One logical provider call; no automatic retries in this MVP adapter.
    return OpenAI(api_key=api_key, max_retries=0, timeout=120)


class OpenAISummarizer:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        factory: ClientFactory = create_client,
    ) -> None:
        try:
            self.settings = settings or Settings.from_env()
        except ConfigurationError as exc:
            raise SummarizationError(str(exc)) from None
        self.factory = factory

    def preflight(self) -> None:
        """Validate local configuration, without constructing a client."""
        if self.settings.summarization_provider != "openai":
            raise SummarizationError("YT_SMZR_SUMMARIZATION_PROVIDER must be openai.")
        if (
            not self.settings.summarization_model
            or not self.settings.summarization_model.strip()
        ):
            raise SummarizationError("YT_SMZR_SUMMARIZATION_MODEL must not be empty.")
        key = self.settings.openai_api_key
        if key is None or not key.strip():
            raise SummarizationError("Summarization requires OPENAI_API_KEY.")
        limit = self.settings.summarizer_max_input_bytes
        if isinstance(limit, bool) or limit <= 0:
            raise SummarizationError(
                "YT_SMZR_SUMMARIZER_MAX_INPUT_BYTES must be a positive integer."
            )
        if importlib.util.find_spec("openai") is None:
            raise SummarizationError("Summarization requires openai; run uv sync.")

    def summarize(self, metadata: VideoMetadata, transcript: Transcript) -> Summary:
        self.preflight()
        try:
            source = prepare_input(metadata, transcript)
            check_input_limit(source, self.settings.summarizer_max_input_bytes)
            key = self.settings.openai_api_key
            assert key is not None  # Validated by preflight.
            model = self.settings.summarization_model
            assert model is not None
            with self.factory(key) as client:
                response = client.responses.parse(
                    model=model,
                    instructions=source.instructions,
                    input=source.source,
                    text_format=SummaryContent,
                    store=False,
                    truncation="disabled",
                )
            if any(
                content.type == "refusal"
                for item in response.output
                if item.type == "message"
                for content in item.content
            ):
                raise SummarizationError("OpenAI refused to summarize this transcript.")
            if response.status != "completed":
                raise SummarizationError(
                    "OpenAI returned an incomplete summary response."
                )
            if response.output_parsed is None:
                raise SummarizationError(
                    "OpenAI returned no structured summary output."
                )
            content = SummaryContent.model_validate(response.output_parsed.model_dump())
            validate_grounding(content, metadata, transcript)
            return Summary(
                **content.model_dump(),
                provider="openai",
                model=model,
            )
        except SummarizationError:
            raise
        except Exception:
            # Provider exceptions can echo arbitrary remote content or credentials.
            raise SummarizationError(
                "OpenAI could not generate a valid structured summary. "
                "Check model support, credentials, connectivity, and provider limits."
            ) from None
