"""Abstract Market Data Provider Interfaces — Sprint 19.1G-QA-MP.

Defines the abstract contracts for historical market data providers, live market data
providers, and instrument reference providers.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from datetime import date

from app.providers.models import (
    NormalizedBar,
    NormalizedInstrument,
    NormalizedOptionChain,
    NormalizedQuote,
    ProviderCapability,
    ProviderMode,
)


class BaseMarketDataProvider(ABC):
    """Base provider class defining identity and capabilities."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique provider identifier (e.g. 'NSE_HISTORICAL', 'UPSTOX_HISTORICAL', 'UPSTOX_LIVE')."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name."""
        pass

    @property
    @abstractmethod
    def provider_mode(self) -> ProviderMode:
        """Provider operational mode."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> set[ProviderCapability]:
        """Supported capabilities."""
        pass

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if provider is online and configured."""
        pass


class HistoricalMarketDataProvider(BaseMarketDataProvider, ABC):
    """Interface for historical market data access & bulk ingestion."""

    @abstractmethod
    async def fetch_historical_bars(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> list[NormalizedBar]:
        """Fetch historical price bars for a symbol."""
        pass

    @abstractmethod
    async def fetch_option_chain_history(
        self,
        underlying_symbol: str,
        trade_date: date,
        expiry_date: date | None = None,
    ) -> NormalizedOptionChain:
        """Fetch historical option chain snapshot for an underlying symbol."""
        pass


class LiveMarketDataProvider(BaseMarketDataProvider, ABC):
    """Interface for real-time live quotes, order books, and WebSocket streaming."""

    @abstractmethod
    async def get_live_quote(self, symbol: str) -> NormalizedQuote | None:
        """Fetch current snapshot quote for a symbol."""
        pass

    @abstractmethod
    async def get_live_option_chain(
        self,
        underlying_symbol: str,
        expiry_date: date | None = None,
    ) -> NormalizedOptionChain:
        """Fetch live real-time option chain for an underlying symbol."""
        pass

    @abstractmethod
    async def subscribe_ticks(
        self,
        symbols: list[str],
    ) -> AsyncGenerator[NormalizedQuote]:
        """Subscribe to real-time tick stream for given symbols."""
        pass


class InstrumentReferenceProvider(ABC):
    """Interface for resolving symbols and instrument specifications across providers."""

    @abstractmethod
    async def resolve_instrument(
        self,
        canonical_symbol: str,
        provider_id: str,
    ) -> NormalizedInstrument | None:
        """Resolve a canonical symbol into provider-specific instrument details."""
        pass

    @abstractmethod
    async def search_instruments(
        self,
        query: str,
        exchange: str = "NSE",
    ) -> list[NormalizedInstrument]:
        """Search instruments matching a query string."""
        pass
