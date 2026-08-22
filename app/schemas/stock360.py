"""Pydantic and dataclass schemas for Stock 360° + Opportunity Intelligence — Sprint 19.1G."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.fundamental_corporate import CompanyFundamentalSnapshot
from app.schemas.news_policy import CompanyNewsPolicySnapshot


@dataclass
class RelativeStrengthMetrics:
    """Point-in-time relative performance against benchmark (Nifty 50) and sector."""

    stock_vs_nifty_5d_pct: float | None = None
    stock_vs_nifty_20d_pct: float | None = None
    stock_vs_nifty_60d_pct: float | None = None
    stock_vs_nifty_120d_pct: float | None = None

    stock_vs_sector_20d_pct: float | None = None
    stock_vs_sector_60d_pct: float | None = None

    rs_trend: str = "NEUTRAL"  # OUTPERFORMING, UNDERPERFORMING, NEUTRAL, INSUFFICIENT_DATA


class TechnicalSummaryRead(BaseModel):
    """Summary of technical indicator and price structure state."""

    close: float
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    adx14: float | None = None
    atr14: float | None = None
    supertrend: float | None = None
    supertrend_direction: str | None = None
    trend_state: str = "SIDEWAYS"  # UPTREND, DOWNTREND, SIDEWAYS
    pct_from_52w_high: float | None = None
    pct_from_52w_low: float | None = None


class VolumeDeliverySummaryRead(BaseModel):
    """Summary of volume and delivery confirmation state."""

    volume: int
    avg_volume_20d: float | None = None
    relative_volume_20d: float | None = None
    delivery_pct: float | None = None
    avg_delivery_pct_20d: float | None = None
    volume_breakout: bool = False
    delivery_breakout: bool = False
    accumulation_signal: bool = False


@dataclass
class Stock360Snapshot:
    """Unified 10-dimensional research snapshot for a single stock."""

    symbol: str
    stock_id: int
    company_name: str
    evaluation_date: date
    as_of_date: date
    sector_name: str | None = None
    is_fno: bool = False

    # 1. Technical & Trend
    technicals: TechnicalSummaryRead | None = None

    # 2. Volume & Delivery
    volume_delivery: VolumeDeliverySummaryRead | None = None

    # 3. Relative Strength
    relative_strength: RelativeStrengthMetrics | None = None

    # 4. Fundamentals & 5. Valuation (19.1E)
    fundamentals: CompanyFundamentalSnapshot | None = None

    # 6. Derivatives & Participant Context (19.1D)
    derivatives_summary: dict[str, Any] = Field(default_factory=dict)

    # 7. Corporate & News / Policy Disclosures (19.1F)
    news_policy: CompanyNewsPolicySnapshot | None = None

    # 8. Data Coverage & Confidence
    data_coverage: dict[str, str] = Field(default_factory=dict)  # AVAILABLE, PARTIAL, NO_DATA
    data_completeness_pct: float = 0.0
    quality_warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ComponentContributionRead(BaseModel):
    """Component contribution to an opportunity score."""

    component_name: str
    score: float  # 0 to 100
    weight: float  # 0.0 to 1.0
    weighted_contribution: float
    raw_metric_value: str | float | int | None = None
    status_reason: str


@dataclass
class StockOpportunityAssessment:
    """Opportunity evaluation result for a specific profile (e.g. BREAKOUT, BTST, QUALITY_PULLBACK)."""

    symbol: str
    stock_id: int
    profile: str  # INVESTMENT_QUALITY, QUALITY_PULLBACK, BREAKOUT, SWING, BTST_READINESS
    evaluation_date: date
    as_of_date: date

    score: float  # 0 to 100
    classification: str  # STRONG_RESEARCH_CANDIDATE, RESEARCH_CANDIDATE, WATCH, WAIT, REJECTED, NO_TRADE, INSUFFICIENT_DATA
    methodology_version: str = "v1.0"

    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    veto_reasons: list[str] = Field(default_factory=list)

    data_completeness_pct: float = 100.0
    evidence_quality: str = "HIGH"  # HIGH, MEDIUM, LOW, INSUFFICIENT

    component_contributions: list[ComponentContributionRead] = Field(default_factory=list)
    sector_name: str | None = None
    is_fno: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


class OpportunityBoardSummaryRead(BaseModel):
    """Summary coverage and candidate count for the Opportunity Board."""

    evaluation_date: date
    profile: str
    total_evaluated: int
    strong_candidates_count: int
    candidates_count: int
    watch_count: int
    wait_count: int
    rejected_count: int
    no_trade_count: int
    candidates: list[dict[str, Any]] = Field(default_factory=list)
