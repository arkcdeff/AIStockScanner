"""Provider Registry & Extensible Selection Engine — Sprint 19.1G-QA-MP.

Manages registration, capability auditing, and intelligent provider selection (AUTO,
NSE_HISTORICAL, UPSTOX_HISTORICAL, UPSTOX_LIVE). Supports future provider extension slots
(DHAN, ZERODHA, FLATTRADE, SHOONYA).
"""
from __future__ import annotations

import logging
from typing import Any

from app.providers.base import (
    HistoricalMarketDataProvider,
    LiveMarketDataProvider,
)

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Central registry for historical & live market-data providers."""

    _instance: ProviderRegistry | None = None

    def __init__(self) -> None:
        self._historical_providers: dict[str, HistoricalMarketDataProvider] = {}
        self._live_providers: dict[str, LiveMarketDataProvider] = {}

        # Default Provider Priorities
        self.default_historical_provider_id: str = "NSE_HISTORICAL"
        self.default_live_provider_id: str = "UPSTOX_LIVE"

        # Future Provider Slots Reserved
        self.reserved_provider_ids: list[str] = ["DHAN", "ZERODHA", "FLATTRADE", "SHOONYA"]

    @classmethod
    def get_instance(cls) -> ProviderRegistry:
        """Singleton pattern for provider registry."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_historical_provider(self, provider: HistoricalMarketDataProvider) -> None:
        """Register a historical market data provider."""
        self._historical_providers[provider.provider_id] = provider
        logger.info(f"Registered Historical Market Data Provider: {provider.provider_id} ({provider.provider_name})")

    def register_live_provider(self, provider: LiveMarketDataProvider) -> None:
        """Register a live market data provider."""
        self._live_providers[provider.provider_id] = provider
        logger.info(f"Registered Live Market Data Provider: {provider.provider_id} ({provider.provider_name})")

    def get_historical_provider(
        self,
        provider_id: str = "AUTO",
    ) -> HistoricalMarketDataProvider | None:
        """Get a historical provider by ID or select default (AUTO)."""
        pid = provider_id.strip().upper()
        if pid == "AUTO":
            # Priority 1: Default Historical Provider (NSE_HISTORICAL)
            if self.default_historical_provider_id in self._historical_providers:
                return self._historical_providers[self.default_historical_provider_id]
            # Priority 2: Any registered historical provider
            if self._historical_providers:
                return next(iter(self._historical_providers.values()))
            return None
        return self._historical_providers.get(pid)

    def get_live_provider(
        self,
        provider_id: str = "AUTO",
    ) -> LiveMarketDataProvider | None:
        """Get a live provider by ID or select default (AUTO)."""
        pid = provider_id.strip().upper()
        if pid == "AUTO":
            # Priority 1: Default Live Provider (UPSTOX_LIVE)
            if self.default_live_provider_id in self._live_providers:
                return self._live_providers[self.default_live_provider_id]
            # Priority 2: Any registered live provider
            if self._live_providers:
                return next(iter(self._live_providers.values()))
            return None
        return self._live_providers.get(pid)

    def list_registered_providers(self) -> dict[str, Any]:
        """List all active registered historical & live providers."""
        return {
            "historical": [
                {
                    "provider_id": p.provider_id,
                    "name": p.provider_name,
                    "mode": p.provider_mode.value,
                    "capabilities": [c.value for c in p.capabilities],
                }
                for p in self._historical_providers.values()
            ],
            "live": [
                {
                    "provider_id": p.provider_id,
                    "name": p.provider_name,
                    "mode": p.provider_mode.value,
                    "capabilities": [c.value for c in p.capabilities],
                }
                for p in self._live_providers.values()
            ],
            "reserved_slots": self.reserved_provider_ids,
        }
