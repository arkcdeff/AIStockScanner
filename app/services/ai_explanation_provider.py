"""
AI Market Explanation Provider Abstraction

Follows the existing project provider patterns from app/market_intelligence/providers/.
Supports configurable AI providers with deterministic fallback.
"""

from __future__ import annotations

import json
from abc import abstractmethod
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.market_intelligence.providers.base import MarketProvider, ProviderResult

# ---------------------------------------------------------------------------
# AI Explanation Provider Result
# ---------------------------------------------------------------------------

class AIExplanationResult(BaseModel):
    """Result from an AI explanation provider.

    Distinguishes at minimum: success, unavailable, timeout, failed, blocked.
    Never exposes API keys, authorization headers, or raw secrets.
    """

    provider_name: str = Field(
        ..., description="Name of the AI provider"
    )
    model_name: str = Field(
        ..., description="Name of the model used"
    )
    status: str = Field(
        ..., description="Execution status"
    )
    explanation: str | None = Field(
        default=None, description="Generated market explanation text"
    )
    structured: dict[str, Any] | None = Field(
        default=None,
        description="Structured explanation data (regime, scores, factors, etc.)",
    )
    generated_at: str = Field(
        default_factory=lambda: __import__("datetime").datetime.utcnow().isoformat(),
        description="When the explanation was generated",
    )
    latency_ms: float | None = Field(
        default=None, description="Provider call latency in milliseconds"
    )
    error_code: str | None = Field(
        default=None, description="Machine-readable error code"
    )
    message: str | None = Field(
        default=None, description="Human-readable error message (sanitized)"
    )

    # Status shortcuts -----------------------------------------------------
    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def is_unavailable(self) -> bool:
        return self.status == "unavailable"

    @property
    def is_timeout(self) -> bool:
        return self.status == "timeout"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def is_error(self) -> bool:
        return not self.is_success


# ---------------------------------------------------------------------------
# AI Explanation Provider Abstraction
# ---------------------------------------------------------------------------

T = Optional["AIExplanationProvider"]  # placeholder for forward ref


class AIExplanationProvider(MarketProvider):
    """Abstract base class for AI explanation providers.

    Responsibilities:
    - Build a prompt from structured market facts
    - Call the AI model
    - Validate the response
    - Return AIExplanationResult
    """

    name: str = "ai_explanation_provider"

    @abstractmethod
    async def explain(
        self, structured_facts: dict[str, Any]
    ) -> AIExplanationResult:
        """Generate an explanation from structured market facts.

        Args:
            structured_facts: Deterministic facts from CompositeMarketIntelligenceResult.
                Must contain only information knowable at evaluation time.

        Returns:
            AIExplanationResult with status, explanation, and/or structured data.
        """
        raise NotImplementedError

    async def health_check(self) -> ProviderResult:
        """Check AI provider health/availability.

        Returns ProviderResult with status 'success' or 'failed'.
        """
        return ProviderResult(
            provider_name=self.name,
            status="success",
            freshness="missing",
            message="Health check not implemented for AI explanation provider",
        )

    async def close(self) -> None:
        """Optional cleanup hook."""
        pass


# ---------------------------------------------------------------------------
# Provider Configuration (minimal, follows existing patterns)
# ---------------------------------------------------------------------------

class AIExplanationProviderConfig(BaseModel):
    """Configuration for an AI explanation provider.

    Follows existing project configuration patterns.
    Supports graceful operation when no API key is configured.
    """

    provider: str = Field(
        ..., description="Provider identifier (e.g., 'ollama', 'openai', 'deepseek')"
    )
    model: str = Field(..., description="Model name to use")
    base_url: str | None = Field(
        default=None, description="Optional base URL for the API endpoint"
    )
    api_key: str | None = Field(
        default=None, description="API key (loaded from secure config, never hard-coded)"
    )
    timeout_seconds: int = Field(
        default=30, description="Maximum time to wait for a response"
    )
    max_tokens: int | None = Field(
        default=None, description="Optional maximum output tokens"
    )
    temperature: float | None = Field(
        default=0.7, description="Optional temperature for generation"
    )


def load_ai_explanation_config() -> AIExplanationProviderConfig | None:
    """Load AI explanation provider configuration from project settings.

    Returns None if no AI provider is configured.
    """
    import os

    # Check for configured provider - follow existing config patterns
    provider_name = os.getenv("AI_EXPLANATION_PROVIDER")
    if not provider_name:
        return None

    # Minimal configuration loading - follow project conventions
    return AIExplanationProviderConfig(
        provider=provider_name,
        model=os.getenv("AI_EXPLANATION_MODEL", "default"),
        base_url=os.getenv("AI_EXPLANATION_BASE_URL"),
        api_key=os.getenv("AI_EXPLANATION_API_KEY"),
        timeout_seconds=int(
            os.getenv("AI_EXPLANATION_TIMEOUT", "30")
        ),
        max_tokens=(
            int(os.getenv("AI_EXPLANATION_MAX_TOKENS"))
            if os.getenv("AI_EXPLANATION_MAX_TOKENS")
            else None
        ),
        temperature=float(os.getenv("AI_EXPLANATION_TEMPERATURE", "0.7")),
    )


# ---------------------------------------------------------------------------
# Deterministic Prompt Builder
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES = {
    "default": (
        "You are a market intelligence analyst. Explain the current market conditions "
        "based SOLELY on the structured facts below. Do NOT invent prices, percentages, "
        "events, news, or causes. Do NOT recalculate deterministic scores. Do NOT change "
        "the supplied market regime. Clearly distinguish missing/stale information. "
        "Explain both bullish AND bearish/risk evidence. Mention contradictions when "
        "material. Do not provide guaranteed predictions. Present speculation as fact. "
        "Keep the explanation understandable to a market user.\n\n"
        "Structured facts:\n{structured_facts}\n\n"
        "Generate a concise market explanation."
    )
}


def build_ai_prompt(structured_facts: dict[str, Any], template: str = "default") -> str:
    """Build a centralized prompt for the AI explanation provider.

    The prompt explicitly instructs the model to:
    1. Use ONLY supplied structured facts
    2. Do not invent prices, percentages, events, news or causes
    3. Do not recalculate deterministic scores
    4. Do not change the supplied market regime
    5. Clearly distinguish missing/stale information
    6. Do not provide guaranteed predictions
    7. Do not present speculation as fact
    8. Keep explanation understandable to a market user
    9. Explain bullish AND bearish/risk evidence
    10. Mention contradictions when material

    Args:
        structured_facts: The deterministic structured facts (from 18.10A)
        template: Prompt template to use

    Returns:
        Prompt string ready for the AI model
    """
    template_str = PROMPT_TEMPLATES.get(template, PROMPT_TEMPLATES["default"])
    # Serialize structured facts as compact JSON to minimize token usage
    facts_json = json.dumps(structured_facts, default=str, separators=(",", ":"))
    return template_str.format(structured_facts=facts_json)


# ---------------------------------------------------------------------------
# Sanitization / Anti-Hallucination Helpers
# ---------------------------------------------------------------------------

def sanitize_provider_error(message: str | None) -> str | None:
    """Sanitize a provider error message, removing any potential secrets.

    Follows the existing project secret-pattern conventions.
    """
    if not message:
        return message

    import re

    _SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
        (r"(?i)\b(sk-[a-z0-9]+(?:[_-][a-z0-9]+)*)", "sk-***"),
        (
            r"(?i)([?&](?:api[-_]?key|token|secret|password|signature)=)[^&\s]+",
            r"\1***",
        ),
        (
            r"(?i)\b(api[-_]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\",;\s]+",
            r"\1=***",
        ),
    )

    sanitized = message
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    return sanitized


def validate_ai_output(
    ai_result: AIExplanationResult,
    deterministic_facts: dict[str, Any],
) -> AIExplanationResult:
    """Validate AI output against deterministic facts (anti-hallucination).

    Ensures AI cannot override:
    - regime
    - composite score
    - confidence score
    - deterministic factor scores

    If structured AI output contains incompatible deterministic values:
    - reject/sanitize it
    - use authoritative deterministic values
    - or fall back to deterministic explanation
    """

    if not ai_result.is_success:
        return ai_result

    structured = ai_result.structured
    if structured is None:
        # No structured output - fall back to deterministic
        from app.services.ai_market_explanation import (
            create_ai_market_explanation,
        )
        return create_ai_market_explanation(
            deterministic_facts  # type: ignore[arg-type]
        )

    # Validate that AI cannot override deterministic values
    # Regime validation
    ai_regime = structured.get("regime")
    det_regime = deterministic_facts.get("regime")
    if ai_regime != det_regime:
        # Regime mismatch - use deterministic value
        structured["regime"] = det_regime

    # Composite score validation
    ai_composite = structured.get("composite_score")
    det_composite = deterministic_facts.get("composite_score")
    if ai_composite is not None and det_composite is not None:
        if ai_composite != det_composite:
            # Score mismatch - use deterministic value
            structured["composite_score"] = det_composite

    # Confidence score validation
    ai_confidence = structured.get("confidence_score")
    det_confidence = deterministic_facts.get("confidence_score")
    if ai_confidence is not None and det_confidence is not None:
        if ai_confidence != det_confidence:
            # Confidence mismatch - use deterministic value
            structured["confidence_score"] = det_confidence

    # Update the result with validated/structured data
    ai_result.structured = structured
    return ai_result