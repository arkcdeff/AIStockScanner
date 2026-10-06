"""Normalized Market Data Models — Sprint 19.1G-QA-MP.

Provider-neutral normalized domain objects for cash equities, market indices, futures,
and full option chains across historical and live market-data providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    price_type: PriceType = PriceType.GENERIC_LTP
    source_name: str = "UNKNOWN"
    provider_id: str = "UNKNOWN"
    freshness_status: FreshnessStatus = FreshnessStatus.LIVE
    raw_provider_meta: dict[str, Any] = field(default_factory=dict)
    contract_id: int | None = None
    provider_instrument_id: str | None = None
    source_change_in_oi: int | None = None
    source_greeks: dict[str, Any] | None = None


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
    chain_scope: str = "SINGLE_EXPIRY"
    expiry_scope: date | None = None
    market_timestamp: datetime | None = None
    source_timestamp: datetime | None = None
    raw_payload_hash: str | None = None
    raw_payload: str | None = None
    source_reference: str | None = None
    batch_id: str | None = None
    is_truncated: bool = False
    expected_strike_count: int | None = None
    observed_strike_count: int | None = None
    quality_status: str = "VALID"
    quality_flags: dict[str, Any] = field(default_factory=dict)
    # G.11-OPT-3 provenance: the underlying spot and the expiry list must never be
    # presented as provider facts unless they actually came from the provider.
    spot_provenance: str = "UNKNOWN"
    spot_observed_at: datetime | None = None
    expiry_provenance: str = "UNKNOWN"
