"""Normalized Market Data Models — Sprint 19.1G-QA-MP.

Provider-neutral normalized domain objects for cash equities, market indices, futures,
and full option chains across historical and live market-data providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ProviderMode(StrEnum):
    HISTORICAL_ONLY = "HISTORICAL_ONLY"
    LIVE_ONLY = "LIVE_ONLY"
    BOTH = "BOTH"


class ProviderCapability(StrEnum):
    CASH_EQUITY = "CASH_EQUITY"
    INDEX = "INDEX"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"
    PARTICIPANT_DATA = "PARTICIPANT_DATA"
    FII_STATS = "FII_STATS"


class PriceType(StrEnum):
    NSE_SETTLEMENT = "NSE_SETTLEMENT"
    NSE_CLOSE = "NSE_CLOSE"
    UPSTOX_HISTORICAL_CLOSE = "UPSTOX_HISTORICAL_CLOSE"
    UPSTOX_LIVE_LTP = "UPSTOX_LIVE_LTP"
    GENERIC_LTP = "GENERIC_LTP"


class FreshnessStatus(StrEnum):
    LIVE = "LIVE"          # Ticks < 5s old during active market
    DELAYED = "DELAYED"    # Ticks 5s to 30s old during active market
    STALE = "STALE"        # Ticks > 30s old
    DISCONNECTED = "DISCONNECTED"
    MARKET_CLOSED = "MARKET_CLOSED"


@dataclass(frozen=True)
class NormalizedInstrument:
    """Canonical instrument mapping across providers."""

    provider_id: str
    provider_symbol: str
    canonical_symbol: str
    exchange: str
    instrument_type: str  # 'EQUITY', 'INDEX', 'FUTURE', 'OPTION'
    expiry_date: date | None = None
    strike_price: Decimal | None = None
    option_type: str | None = None  # 'CE', 'PE'
    lot_size: int = 1
    tick_size: Decimal = Decimal("0.05")
    isin: str | None = None
    instrument_key: str | None = None  # Provider-specific key (e.g. Upstox instrument_key)


@dataclass(frozen=True)
class NormalizedBar:
    """Normalized OHLCV price bar (daily or intraday)."""

    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    turnover: Decimal | None = None
    delivery_quantity: int | None = None
    delivery_percentage: Decimal | None = None
    price_type: PriceType = PriceType.NSE_CLOSE
    source_name: str = "UNKNOWN"
    provider_id: str = "UNKNOWN"
    timestamp: datetime | None = None


@dataclass
class NormalizedQuote:
    """Normalized real-time or end-of-day quote observation."""

    symbol: str
    instrument_type: str  # 'EQUITY', 'INDEX', 'FUTURE', 'OPTION'
    ltp: Decimal
    previous_close: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    settlement_price: Decimal | None = None
    volume: int | None = None
    open_interest: int | None = None
    change_in_open_interest: int | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    strike_price: Decimal | None = None
    option_type: str | None = None  # 'CE', 'PE'
    expiry_date: date | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    price_type: PriceType = PriceType.GENERIC_LTP
    source_name: str = "UNKNOWN"
    provider_id: str = "UNKNOWN"
    freshness_status: FreshnessStatus = FreshnessStatus.LIVE
    raw_provider_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedOptionChain:
    """Full option chain snapshot for an underlying symbol."""

    underlying_symbol: str
    as_of_timestamp: datetime
    spot_price: Decimal | None
    expiries: list[date]
    calls: list[NormalizedQuote]
    puts: list[NormalizedQuote]
    ce_pe_paired_pct: float = 0.0
    provider_id: str = "UNKNOWN"
    source_name: str = "UNKNOWN"
