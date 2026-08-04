from functools import lru_cache

from app.ai.base import AIProviderError, BaseAIProvider
from app.ai.providers.deepseek_provider import DeepSeekProvider
from app.ai.providers.mock_provider import MockAIProvider
from app.ai.providers.ollama_provider import OllamaProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.core.config import settings

_PROVIDERS: dict[str, type[BaseAIProvider]] = {
    "mock": MockAIProvider,
    "ollama": OllamaProvider,
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
}


@lru_cache
def get_provider() -> BaseAIProvider:
    name = settings.ai_provider.strip().lower()
    provider_cls = _PROVIDERS.get(name)
    if provider_cls is None:
        raise AIProviderError(
            f"Unsupported AI provider: {name!r}. "
            f"Supported providers: {', '.join(sorted(_PROVIDERS))}"
        )
    return provider_cls()


def reset_provider_cache() -> None:
    get_provider.cache_clear()
