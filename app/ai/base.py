from abc import ABC, abstractmethod


class AIProviderError(RuntimeError):
    """Raised when an AI provider fails to produce a usable response."""


class BaseAIProvider(ABC):
    """Abstract provider that returns plain-text explanations for a prompt."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier used for caching and persistence (e.g. 'ollama')."""

    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier."""

    @abstractmethod
    async def generate(self, prompt: str, *, timeout_seconds: int) -> str:
        """Return the model output for the given prompt. Raises AIProviderError on failure."""

    @abstractmethod
    async def health(self) -> bool:
        """Return True when the provider endpoint is reachable and healthy."""
