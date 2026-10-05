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
    if settings.summarization_provider == "ollama":
        from yt_smzr.summarization.ollama_provider import OllamaSummarizer

        return OllamaSummarizer(settings)
    # Never echo arbitrary environment values, which can contain secrets.
    raise SummarizationError(
        "YT_SMZR_SUMMARIZATION_PROVIDER must be openai, openrouter, or ollama."
    )
