from __future__ import annotations

import re
from abc import abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, Field, field_validator

# Type variable for provider results
T = TypeVar("T", bound="ProviderResult")

STATUS_VALUES = ("success", "cached", "stale", "unavailable", "blocked", "failed")
FRESHNESS_VALUES = ("live", "recent", "stale", "missing")

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


class ProviderResult(BaseModel):
    """Typed result from any market data provider.

    ``status`` describes the provider execution and ``freshness`` the state
    of the returned data; the two dimensions are independent.
    """

    provider_name: str = Field(..., description="Name of the provider")
    status: str = Field(..., description="Execution status")
    observed_at: str | None = Field(
        default=None, description="When the data was observed"
    )
    fetched_at: str | None = Field(
        default=None, description="When the system fetched it"
    )
    market_date: str | None = Field(
        default=None, description="The market date the data applies to"
    )
    freshness: str = Field(
        default="missing",
        description="Freshness state: live, recent, stale, missing",
    )
    data: Any = Field(
        default=None, description="Normalized data payload"
    )
    error_code: str | None = Field(
        default=None, description="Machine-readable error code"
    )
    message: str | None = Field(
        default=None, description="Human-readable error message"
    )

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in STATUS_VALUES:
            raise ValueError(
                f"status must be one of {STATUS_VALUES}, got {value!r}"
            )
        return value

    @field_validator("freshness")
    @classmethod
    def _validate_freshness(cls, value: str) -> str:
        if value not in FRESHNESS_VALUES:
            raise ValueError(
                f"freshness must be one of {FRESHNESS_VALUES}, got {value!r}"
            )
        return value

    # Execution status ---------------------------------------------------
    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def is_cached(self) -> bool:
        return self.status == "cached"

    @property
    def is_stale_status(self) -> bool:
        return self.status == "stale"

    @property
    def is_unavailable(self) -> bool:
        return self.status == "unavailable"

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    @property
    def is_failure(self) -> bool:
        return self.is_failed

    # Data freshness -----------------------------------------------------
    @property
    def is_live(self) -> bool:
        return self.freshness == "live"

    @property
    def is_recent(self) -> bool:
        return self.freshness == "recent"

    @property
    def is_stale(self) -> bool:
        return self.freshness == "stale"

    @property
    def is_missing(self) -> bool:
        return self.freshness == "missing"


class MarketProviderError(Exception):
    """Base exception for all market provider errors."""

    def __init__(
        self,
        message: str = "",
        provider_name: str | None = None,
        error_code: str | None = None,
    ) -> None:
        self.message = message
        self.provider_name = provider_name
        self.error_code = error_code or self.__class__.__name__
        super().__init__(message)

    def public_message(self) -> str:
        """Return a sanitized message safe for end users and logs."""
        message = self.message or ""
        for pattern, replacement in _SECRET_PATTERNS:
            message = re.sub(pattern, replacement, message)
        return message


class MarketProviderConfigError(MarketProviderError):
    """Raised when provider configuration is invalid or missing."""


class MarketProviderTimeoutError(MarketProviderError):
    """Raised when provider request times out."""


class MarketProviderUnavailableError(MarketProviderError):
    """Raised when provider is unavailable."""


class MarketProviderBlockedError(MarketProviderError):
    """Raised when provider access is blocked (e.g., anti-bot)."""


class MarketProviderParseError(MarketProviderError):
    """Raised when provider response cannot be parsed."""


class MarketProvider:
    """Abstract base class for all market data providers."""

    name: str = "base_provider"

    @abstractmethod
    async def fetch(self, **kwargs: Any) -> ProviderResult:
        """Fetch data from the provider.

        Subclasses must implement this method and return a ProviderResult.
        """
        raise NotImplementedError

    async def health_check(self) -> ProviderResult:
        """Check provider health/availability.

        Returns a ProviderResult with status 'success' or 'failed'.
        """
        return ProviderResult(
            provider_name=self.name,
            status="success",
            freshness="missing",
            message="Health check not implemented",
        )

    async def close(self) -> None:
        """Optional cleanup hook called when provider is no longer needed."""
        pass

    @property
    def supports_markets(self) -> list[str]:
        """Return list of market identifiers this provider supports."""
        return []

    @property
    def supports_indices(self) -> list[str]:
        """Return list of index symbols this provider supports."""
        return []