"""Task 18.11: REST API schemas for Market Intelligence endpoints.

Typed Pydantic response models for all market intelligence API endpoints.
No business logic — purely serialization contracts for the API layer.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Factor-level schemas
# ---------------------------------------------------------------------------


class FactorOut(BaseModel):
    """Single factor family output (from CompositeMarketIntelligenceResult)."""

    name: str
    score: float | None = None
    configured_weight: float = 0.0
    effective_weight: float = 0.0
    confidence: float = 0.0
    completeness: float = 0.0
    freshness: str = "missing"
    direction: str | None = None
    provenance: str | None = None
    contribution: float = 0.0


class ContradictionOut(BaseModel):
    type: str
    factor_a: str
    factor_b: str
    severity: str
    message_code: str


class RiskFactorOut(BaseModel):
    type: str
    severity: str
    message: str


# ---------------------------------------------------------------------------
# Overall intelligence response
# ---------------------------------------------------------------------------


class OverallIntelligenceResponse(BaseModel):
    """GET /market-intelligence/latest — overall composite intelligence."""

    evaluation_at: datetime | None = None
    market_date: date | None = None
    regime: str | None = None
    composite_score: float | None = None
    confidence_score: float | None = None
    confidence_state: str = "unknown"
    data_completeness_percent: float | None = None
    agreement_percent: float | None = None
    factors: list[FactorOut] = []
    bullish_factors: list[FactorOut] = []
    bearish_factors: list[FactorOut] = []
    neutral_factors: list[FactorOut] = []
    missing_factors: list[FactorOut] = []
    stale_factors: list[FactorOut] = []
    contradictions: list[ContradictionOut] = []
    risk_factors: list[RiskFactorOut] = []
    factor_contributions: dict[str, float] = {}
    uncertain: bool = False
    high_volatility: bool = False
    provenance_summary: list[str] = []
    snapshot_id: int | None = None


# ---------------------------------------------------------------------------
# History response
# ---------------------------------------------------------------------------


class HistoryEntryResponse(BaseModel):
    """Single historical snapshot entry."""

    id: int
    market_date: date
    generated_at: date
    composite_score: float | None = None
    confidence_score: float | None = None
    regime: str | None = None
    price_action_score: float | None = None
    breadth_score: float | None = None
    sector_score: float | None = None
    institutional_score: float | None = None
    volatility_score: float | None = None
    global_score: float | None = None
    gift_nifty_score: float | None = None
    currency_score: float | None = None
    crude_score: float | None = None
    macro_score: float | None = None
    data_completeness_percent: float | None = None
    stale_factor_count: int = 0
    missing_factor_count: int = 0


class HistoryResponse(BaseModel):
    """GET /market-intelligence/history — bounded historical intelligence."""

    entries: list[HistoryEntryResponse] = []
    start_date: date | None = None
    end_date: date | None = None
    total: int = 0


# ---------------------------------------------------------------------------
# Domain-specific responses
# ---------------------------------------------------------------------------


class IndianIndexItem(BaseModel):
    """Single Indian index price-action summary."""

    index_code: str
    name: str | None = None
    latest_close: float | None = None
    daily_change_percent: float | None = None
    return_1d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    trend: str | None = None
    price_action_score: float | None = None
    freshness: str = "missing"


class IndianIndicesResponse(BaseModel):
    """GET /market-intelligence/indian-indices — Indian index summaries."""

    market_date: date | None = None
    indices: list[IndianIndexItem] = []
    evaluation_at: datetime | None = None


class GlobalMarketItem(BaseModel):
    """Single global/Asian market summary."""

    index_code: str
    market_date: date | None = None
    close: float | None = None
    returns: dict[str | int, float | None] = {}
    trend: str | None = None
    price_action_score: float | None = None
    score: float | None = None
    direction: str | None = None
    freshness: str = "missing"


class GlobalMarketsResponse(BaseModel):
    """GET /market-intelligence/global — global/Asian market intelligence."""

    evaluation_at: datetime | None = None
    global_score: float | None = None
    global_direction: str | None = None
    us_context: dict[str, Any] = {}
    asian_context: dict[str, Any] = {}
    markets: dict[str, GlobalMarketItem] = {}
    completeness: float = 0.0
    confidence: float = 0.0


class GiftNiftyResponse(BaseModel):
    """GET /market-intelligence/gift-nifty — GIFT Nifty intelligence."""

    evaluation_at: datetime | None = None
    gift_nifty_score: float | None = None
    confidence_score: float | None = None
    data_completeness_percent: float | None = None
    direction: str | None = None
    indicated_gap_points: float | None = None
    indicated_gap_percent: float | None = None
    gift_observation: dict[str, Any] | None = None
    nifty_reference: dict[str, Any] | None = None
    fallback_level: str = "none"
    freshness: str = "missing"


class InstitutionalInvestorItem(BaseModel):
    """Single FII/DII flow summary."""

    investor_type: str
    buy_value: float | None = None
    sell_value: float | None = None
    net_value: float | None = None
    score: float | None = None
    direction: str | None = None
    freshness: str = "missing"
    confidence: float | None = None


class InstitutionalResponse(BaseModel):
    """GET /market-intelligence/institutional — FII/DII intelligence."""

    evaluation_at: datetime | None = None
    institutional_score: float | None = None
    direction: str | None = None
    freshness: str = "missing"
    confidence: float | None = None
    completeness: float | None = None
    investors: dict[str, InstitutionalInvestorItem] = {}
    combined: dict[str, Any] = {}


class ExternalFactorItem(BaseModel):
    """Single external factor (VIX, USD/INR, Brent)."""

    index_code: str
    latest: float | None = None
    score: float | None = None
    direction: str | None = None
    freshness: str = "missing"
    confidence: float | None = None
    returns: dict[str | int, float | None] = {}
    regime: str | None = None
    trend: str | None = None


class ExternalFactorsResponse(BaseModel):
    """GET /market-intelligence/external-factors — VIX, USD/INR, Brent."""

    evaluation_at: datetime | None = None
    completeness: float = 0.0
    confidence: float = 0.0
    freshness: str = "missing"
    india_vix: ExternalFactorItem | None = None
    usd_inr: ExternalFactorItem | None = None
    brent_crude: ExternalFactorItem | None = None


class BreadthResponse(BaseModel):
    """GET /market-intelligence/breadth — market breadth intelligence."""

    market_date: str | None = None
    evaluation_time: str | None = None
    breadth_score: float | None = None
    breadth_state: str | None = None
    breadth_direction: str | None = None
    advancing_count: int | None = None
    declining_count: int | None = None
    unchanged_count: int | None = None
    advance_decline_ratio: float | None = None
    net_advances: int | None = None
    participation_score: float | None = None
    breadth_momentum_score: float | None = None
    breadth_momentum_direction: str | None = None
    sma_breadth: dict[str, Any] = {}
    high_low_breadth: dict[str, Any] = {}
    divergence: dict[str, Any] = {}
    completeness: float = 0.0
    confidence: float = 0.0
    freshness: str = "missing"


class SectorItem(BaseModel):
    """Single sector summary."""

    code: str
    name: str | None = None
    group: str | None = None
    score: float | None = None
    direction: str | None = None
    returns: dict[str | int, float | None] = {}
    momentum: float | None = None
    relative_strength: float | None = None
    freshness: str = "missing"


class SectorIntelligenceResponse(BaseModel):
    """GET /market-intelligence/sectors — sector intelligence."""

    market_date: str | None = None
    sector_intelligence_score: float | None = None
    direction: str | None = None
    sector_count: int = 0
    available_sector_count: int = 0
    leaders: list[SectorItem] = []
    laggards: list[SectorItem] = []
    cyclical_score: float | None = None
    defensive_score: float | None = None
    rotation: dict[str, Any] = {}
    completeness_percent: float = 0.0
    confidence_score: float = 0.0
    freshness: str = "missing"


class MacroEventItem(BaseModel):
    """Single macro event summary."""

    event_code: str
    name: str | None = None
    category: str | None = None
    importance: str | None = None
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    surprise: float | None = None
    lifecycle: str | None = None
    time_to_event: str | None = None
    freshness_status: str | None = None


class MacroIntelligenceResponse(BaseModel):
    """GET /market-intelligence/macro — macro/event intelligence."""

    evaluation_at: datetime | None = None
    macro_score: float | None = None
    direction: str | None = None
    freshness: str = "missing"
    confidence: float = 0.0
    completeness: float = 0.0
    domains: dict[str, Any] = {}
    upcoming_events: list[MacroEventItem] = []
    upcoming_risk: float = 0.0


class MarketExplanationResponse(BaseModel):
    """GET /market-intelligence/explanation — AI market explanation."""

    market_date: str | None = None
    evaluation_at: str | None = None
    regime: str | None = None
    composite_score: float | None = None
    confidence_score: float | None = None
    headline: str | None = None
    summary: str | None = None
    bullish_factors: list[str] = []
    bearish_factors: list[str] = []
    risk_factors: list[str] = []
    missing_data: list[str] = []
    stale_data: list[str] = []
    contradictions: list[str] = []
    watch_items: list[str] = []
    provider: str | None = None
    model: str | None = None
    generated_at: str | None = None
    is_ai_generated: bool = False
    fallback_used: bool = False


# ---------------------------------------------------------------------------
# Refresh response
# ---------------------------------------------------------------------------


class RefreshResultResponse(BaseModel):
    """POST /market-intelligence/refresh — manual refresh result."""

    status: str = "ok"
    message: str | None = None
    evaluation_at: datetime | None = None
    composite_score: float | None = None
    regime: str | None = None
    factors_refreshed: list[str] = []
    factors_failed: list[str] = []


# ---------------------------------------------------------------------------
# Error response
# ---------------------------------------------------------------------------


class MIErrorResponse(BaseModel):
    """Standard error response for market intelligence endpoints."""

    detail: str
    error_code: str | None = None
