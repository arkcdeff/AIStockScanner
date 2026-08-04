import httpx

from app.ai.base import AIProviderError, BaseAIProvider
from app.core.config import settings


class OllamaProvider(BaseAIProvider):
    """Ollama local inference via the /api/generate endpoint."""

    def __init__(self) -> None:
        self.base_url = settings.ai_ollama_url.rstrip("/")
        self.model = settings.ai_ollama_model

    @property
    def name(self) -> str:
        return "ollama"

    def model_name(self) -> str:
        return self.model

    async def generate(self, prompt: str, *, timeout_seconds: int) -> str:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "stream": False},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise AIProviderError(f"Ollama request failed: {exc}") from exc

        text = payload.get("response", "").strip()
        if not text:
            raise AIProviderError("Ollama returned an empty response")
        return text

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.is_success
        except Exception:  # noqa: BLE001
            return False
