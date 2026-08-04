import httpx

from app.ai.base import AIProviderError, BaseAIProvider
from app.core.config import settings


class OpenAIProvider(BaseAIProvider):
    """OpenAI via the /chat/completions endpoint."""

    def __init__(self) -> None:
        self.api_key = settings.ai_openai_api_key
        self.base_url = settings.ai_openai_base_url.rstrip("/")
        self.model = settings.ai_openai_model

    @property
    def name(self) -> str:
        return "openai"

    def model_name(self) -> str:
        return self.model

    async def generate(self, prompt: str, *, timeout_seconds: int) -> str:
        if not self.api_key:
            raise AIProviderError("OpenAI API key is not configured")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=body
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise AIProviderError(f"OpenAI request failed: {exc}") from exc

        try:
            text = payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("OpenAI returned an unexpected payload") from exc
        if not text:
            raise AIProviderError("OpenAI returned an empty response")
        return text

    async def health(self) -> bool:
        if not self.api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return response.is_success
        except Exception:  # noqa: BLE001
            return False
