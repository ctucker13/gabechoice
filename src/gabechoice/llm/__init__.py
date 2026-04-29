from gabechoice.llm.base import LLMProvider
from gabechoice.config import settings


def get_llm() -> LLMProvider:
    if settings.llm_provider == "anthropic":
        from gabechoice.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    from gabechoice.llm.openai_provider import OpenAIProvider
    return OpenAIProvider()
