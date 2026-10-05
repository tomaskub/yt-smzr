"""OpenRouter Chat Completions JSON Schema adapter with local preflight."""

import importlib.util
from typing import TYPE_CHECKING

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
from yt_smzr.summarization.openai_provider import ClientFactory
from yt_smzr.summarization.prompts import check_input_limit, prepare_input
from yt_smzr.transcription.base import Transcript


def create_client(api_key: str) -> "OpenAI":
    from openai import OpenAI

    return OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        timeout=120,
    )


class OpenRouterSummarizer:
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
        if self.settings.summarization_provider != "openrouter":
            raise SummarizationError(
                "YT_SMZR_SUMMARIZATION_PROVIDER must be openrouter."
            )
        model = self.settings.summarization_model
        if not model or not model.strip():
            raise SummarizationError(
                "OpenRouter requires an explicit YT_SMZR_SUMMARIZATION_MODEL."
            )
        key = self.settings.openrouter_api_key
        if key is None or not key.strip():
            raise SummarizationError("Summarization requires OPENROUTER_API_KEY.")
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
            key = self.settings.openrouter_api_key
            model = self.settings.summarization_model
            assert key is not None and model is not None
            with self.factory(key) as client:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": source.instructions},
                        {"role": "user", "content": source.source},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "SummaryContent",
                            "strict": True,
                            "schema": SummaryContent.model_json_schema(),
                        },
                    },
                    extra_body={"provider": {"require_parameters": True}},
                )
            if not response.choices:
                raise SummarizationError(
                    "OpenRouter returned no structured summary output."
                )
            choice = response.choices[0]
            if choice.message.refusal or choice.finish_reason == "content_filter":
                raise SummarizationError(
                    "OpenRouter refused to summarize this transcript."
                )
            if choice.finish_reason != "stop":
                raise SummarizationError(
                    "OpenRouter returned an incomplete summary response."
                )
            if not choice.message.content:
                raise SummarizationError(
                    "OpenRouter returned no structured summary output."
                )
            content = SummaryContent.model_validate_json(choice.message.content)
            validate_grounding(content, metadata, transcript)
            return Summary(**content.model_dump(), provider="openrouter", model=model)
        except SummarizationError:
            raise
        except Exception:
            raise SummarizationError(
                "OpenRouter could not generate a valid structured summary. "
                "Check model structured-output support, credentials, connectivity, "
                "and provider limits."
            ) from None
