"""Select the configured provider for every workflow entrypoint."""

from yt_smzr.config import Settings
from yt_smzr.summarization.base import SummarizationError, Summarizer


def create_summarizer(settings: Settings) -> Summarizer:
    if settings.summarization_provider == "openai":
        from yt_smzr.summarization.openai_provider import OpenAISummarizer

        return OpenAISummarizer(settings)
    if settings.summarization_provider == "openrouter":
        from yt_smzr.summarization.openrouter_provider import OpenRouterSummarizer

        return OpenRouterSummarizer(settings)
    # Never echo arbitrary environment values, which can contain secrets.
    raise SummarizationError(
        "YT_SMZR_SUMMARIZATION_PROVIDER must be openai or openrouter."
    )
