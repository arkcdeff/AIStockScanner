"""Pydantic and Data Architecture Dataclass Schemas for Sprint 19.1D-6.

Defines explicit typed dataclasses and schemas for historical options analytics:
  - OIWall: Static/Fresh call and put open-interest wall levels and metadata
  - MaxPainCandidate & MaxPainAnalytics: Settlement payout minimization curve and strike
  - PCRAnalytics: Put-Call Ratios by Open Interest and Volume with coverage metadata
  - OIConcentrationItem: Ranked strike-level OI concentration shares
  - OptionChainAnalytics: Full single-expiry options analytics snapshot
  - ExpiryOptionAnalyticsItem & ExpiryOptionsAnalytics: Cross-expiry term structure analytics
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class OIWall:
    """OI Concentration Wall level (Static or Fresh ΔOI)."""

    strike_price: Decimal
    open_interest: int
    oi_share_pct: Decimal
    change_in_open_interest: int | None = None
    distance_from_spot: Decimal | None = None
    distance_pct: Decimal | None = None
    option_type: str = "CE"  # "CE" or "PE"
    expiry_date: date | None = None
    trade_date: date | None = None


@dataclass
class MaxPainCandidate:
    """Candidate settlement strike payout evaluation in Max Pain calculation."""

    candidate_strike: Decimal
    call_payout: Decimal
    put_payout: Decimal
    total_payout: Decimal


@dataclass
class MaxPainAnalytics:
    """Max Pain payout minimization analytics result."""

    max_pain_strike: Decimal | None = None
    spot_value: Decimal | None = None
    distance_from_spot: Decimal | None = None
    distance_pct: Decimal | None = None
    tie_count: int = 0
    tied_strikes: list[Decimal] = field(default_factory=list)
    methodology: str = "CONTRACT_LOT_WEIGHTED_PAYOUT_MINIMIZATION_V1"
    candidates: list[MaxPainCandidate] = field(default_factory=list)


@dataclass
class PCRAnalytics:
    """Put-Call Ratio analytics by Open Interest and Volume."""

    pcr_oi: Decimal | None = None
    pcr_volume: Decimal | None = None
    total_call_oi: int = 0
    total_put_oi: int = 0
    total_call_volume: int = 0
    total_put_volume: int = 0
    valid_ce_oi_count: int = 0
    valid_pe_oi_count: int = 0
    missing_ce_count: int = 0
    missing_pe_count: int = 0


@dataclass
class OIConcentrationItem:
    """Ranked strike-level OI concentration item."""

    strike_price: Decimal
    open_interest: int
    oi_share_pct: Decimal
    change_in_open_interest: int | None = None
    option_type: str = "CE"


@dataclass
class OptionChainAnalytics:
    """Full historical options analytics result for a single underlying, date, and expiry."""

    underlying_symbol: str
    trade_date: date
    expiry_date: date
    as_of_date: date
    resolved_spot: Decimal | None = None
    pcr: PCRAnalytics = field(default_factory=PCRAnalytics)
    max_pain: MaxPainAnalytics = field(default_factory=MaxPainAnalytics)
    static_call_wall: OIWall | None = None
    static_put_wall: OIWall | None = None
    fresh_call_wall: OIWall | None = None
    fresh_put_wall: OIWall | None = None
    largest_call_unwinding: OIWall | None = None
    largest_put_unwinding: OIWall | None = None
    call_oi_concentrations: list[OIConcentrationItem] = field(default_factory=list)
    put_oi_concentrations: list[OIConcentrationItem] = field(default_factory=list)
    quality_warnings: list[str] = field(default_factory=list)
    algorithm_version: str = "OPTIONS_ANALYTICS_V1"


@dataclass
class ExpiryOptionAnalyticsItem:
    """Per-expiry aggregated options metrics for an underlying on a trade date."""

    expiry_date: date
    calendar_days_to_expiry: int
    total_call_oi: int = 0
    total_put_oi: int = 0
    total_oi: int = 0
    total_call_volume: int = 0
    total_put_volume: int = 0
    total_volume: int = 0
    oi_share_pct: Decimal = Decimal("0")
    volume_share_pct: Decimal = Decimal("0")
    pcr_oi: Decimal | None = None
    pcr_volume: Decimal | None = None
    total_strikes: int = 0


@dataclass
class ExpiryOptionsAnalytics:
    """Cross-expiry term structure analytics for an underlying on a trade date."""

    underlying_symbol: str
    trade_date: date
    as_of_date: date
    total_oi_across_expiries: int = 0
    total_volume_across_expiries: int = 0
    expiries: list[ExpiryOptionAnalyticsItem] = field(default_factory=list)
    nearest_expiry_oi_share_pct: Decimal = Decimal("0")
    nearest_expiry_volume_share_pct: Decimal = Decimal("0")
    algorithm_version: str = "OPTIONS_ANALYTICS_V1"
