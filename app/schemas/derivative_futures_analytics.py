"""Pydantic and Data Architecture Schemas for Sprint 19.1D-5.

Defines explicit typed dataclasses and schemas for historical futures analytics:
  - FuturesPriceReference: Price field resolution & source provenance
  - FuturesContractObservation: EOD price, OI, volume, and lot size observation
  - FuturesContractAnalytics: Contract-level price/OI change, buildup classification, basis
  - FuturesCurveItem & FuturesCurveSnapshot: Term structure, slope, and calendar spreads
  - FuturesRolloverAnalytics: Position migration across expiries & historical context
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class FuturesPriceReference:
    """Futures price value and the exact source field from which it was resolved."""

    price_used: Decimal
    price_source_field: str  # "settlement_price", "close", "last_price"


@dataclass
class FuturesContractObservation:
    """Observed Facts for a single futures contract on a specific trade date."""

    contract_id: int
    trading_symbol: str
    expiry_date: date
    calendar_days_to_expiry: int
    trade_date: date
    price_reference: FuturesPriceReference
    open_interest: int
    volume_contracts: int
    lot_size: int
    source_change_in_oi: int | None = None
    source_underlying_value: Decimal | None = None
    available_at: date | None = None


@dataclass
class FuturesContractAnalytics:
    """Contract-level price/OI change, buildup pattern classification, and basis."""

    contract_id: int
    trading_symbol: str
    expiry_date: date
    calendar_days_to_expiry: int
    trade_date: date
    current_observation: FuturesContractObservation
    previous_observation: FuturesContractObservation | None = None
    price_change_absolute: Decimal | None = None
    price_change_pct: Decimal | None = None
    source_change_in_oi: int | None = None
    derived_change_in_oi: int | None = None
    oi_change_pct: Decimal | None = None
    buildup_state: str = "INSUFFICIENT_HISTORY"
    spot_value: Decimal | None = None
    spot_source: str | None = None
    # spot_source options: "SOURCE_UNDERLYING_VALUE", "MARKET_INDEX_PRICE", "DAILY_PRICE"
    basis_absolute: Decimal | None = None
    basis_pct: Decimal | None = None
    annualized_simple_basis_pct: Decimal | None = None
    quality_warnings: list[str] = field(default_factory=list)
    algorithm_version: str = "FUTURES_ANALYTICS_V1"


@dataclass
class FuturesCurveItem:
    """One contract element in a futures term structure curve."""

    expiry_rank: str  # "NEAR", "NEXT", "FAR_1", "FAR_2", etc.
    expiry_date: date
    calendar_days_to_expiry: int
    contract_id: int
    trading_symbol: str
    price: Decimal
    price_source_field: str
    open_interest: int
    volume_contracts: int
    oi_share_pct: Decimal
    basis_absolute: Decimal | None = None
    basis_pct: Decimal | None = None
    lot_size: int = 1


@dataclass
class FuturesCurveSnapshot:
    """Term structure snapshot across available expiries for an underlying."""

    underlying_type: str  # "INDEX" or "STOCK"
    underlying_id: int
    underlying_symbol: str
    trade_date: date
    spot_value: Decimal | None = None
    spot_source: str | None = None
    curve_slope: str = "INSUFFICIENT_EXPIRIES"  # "UPWARD_SLOPING", "DOWNWARD_SLOPING", "FLAT_CURVE"
    total_futures_oi: int = 0
    items: list[FuturesCurveItem] = field(default_factory=list)
    adjacent_spreads: list[dict[str, Any]] = field(default_factory=list)
    as_of_date: date = field(default_factory=date.today)


@dataclass
class FuturesRolloverAnalytics:
    """Rollover statistics measuring position migration across expiries."""

    underlying_symbol: str
    trade_date: date
    as_of_date: date
    near_expiry_date: date | None = None
    near_dte: int | None = None
    next_expiry_date: date | None = None
    near_expiry_oi: int = 0
    next_expiry_oi: int = 0
    far_expiries_oi: int = 0
    total_futures_oi: int = 0
    near_oi_share_pct: Decimal = Decimal("0")
    next_oi_share_pct: Decimal = Decimal("0")
    rollover_next_pct: Decimal | None = None  # next_oi / (near_oi + next_oi) * 100
    rollover_later_pct: Decimal | None = None # (total_oi - near_oi) / total_oi * 100
    is_expiry_window: bool = False
    historical_rollover_comparison: dict[str, Any] | None = None
    quality_warnings: list[str] = field(default_factory=list)
    algorithm_version: str = "FUTURES_ANALYTICS_V1"
