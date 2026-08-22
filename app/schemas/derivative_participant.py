"""Pydantic and Data Dataclass Schemas for Sprint 19.1D-7: Participant Derivatives.

Defines schemas for participant positioning, volume activity, and FII derivatives statistics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class ParticipantPositionItem:
    """Normalized position/volume fact item."""

    participant_type: str  # "CLIENT", "DII", "FII", "PRO"
    instrument_bucket: str  # "INDEX_FUTURE", "STOCK_FUTURE", etc.
    long_contracts: int
    short_contracts: int
    net_contracts: int
    reported_total_long: int | None = None
    reported_total_short: int | None = None
    long_share_pct: Decimal = Decimal("0")
    short_share_pct: Decimal = Decimal("0")


@dataclass
class FiiIndexFuturesSummary:
    """Deterministic summary metrics for FII Index Futures positioning."""

    report_date: date
    long_contracts: int = 0
    short_contracts: int = 0
    net_contracts: int = 0
    long_short_ratio: Decimal | None = None
    day_over_day_net_change: int | None = None
    day_over_day_long_change: int | None = None
    day_over_day_short_change: int | None = None


@dataclass
class FiiDerivativeStatisticItem:
    """Item for FII Derivatives Statistics summary."""

    instrument_category: str
    buy_contracts: int | None = None
    buy_amount: Decimal | None = None
    sell_contracts: int | None = None
    sell_amount: Decimal | None = None
    open_interest_contracts: int | None = None
    open_interest_amount: Decimal | None = None
    net_buy_sell_contracts: int | None = None
    net_buy_sell_amount: Decimal | None = None


@dataclass
class ParticipantSnapshot:
    """Snapshot of participant positioning or volume for a report_date."""

    report_date: date
    dataset_type: str  # "OPEN_INTEREST" or "TRADING_VOLUME"
    as_of_date: date
    fii_index_futures: FiiIndexFuturesSummary
    items: list[ParticipantPositionItem] = field(default_factory=list)
    fii_statistics: list[FiiDerivativeStatisticItem] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    is_market_balanced: bool = True
