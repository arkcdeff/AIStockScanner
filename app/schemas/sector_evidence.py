"""Sector Evidence Schemas — G.11-D-6 Sector Breadth & Market Evidence Consensus Models.

All models are immutable (frozen) and deterministic.
Exposes discrete state distributions, canonical denominators, domain participation rates,
and deterministic sector contradiction flags.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SectorContradictionFlag(StrEnum):
    """Deterministic sector contradiction classification."""

    PRICE_BREADTH_CONTRADICTION = "PRICE_BREADTH_CONTRADICTION"
    DELIVERY_CONFIRMATION_DEFICIT = "DELIVERY_CONFIRMATION_DEFICIT"
    HIGH_CONCENTRATION_RISK = "HIGH_CONCENTRATION_RISK"
    SECTOR_EVIDENCE_CONFLICTED = "SECTOR_EVIDENCE_CONFLICTED"


class CoverageStatus(StrEnum):
    """Data completeness and coverage evaluation status for a sector universe."""

    CONFIRMED = "CONFIRMED"
    PARTIAL_DATA = "PARTIAL_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNSUPPORTED_HISTORICAL_SECTOR_TAXONOMY = "UNSUPPORTED_HISTORICAL_SECTOR_TAXONOMY"


class SectorUniverseCode(StrEnum):
    """17 supported NSE sector/thematic index universes configured by AIStockScanner."""

    NIFTY_AUTO = "NIFTY_AUTO"
    NIFTY_BANK = "NIFTY_BANK"
    NIFTY_FIN_SERV = "NIFTY_FIN_SERV"
    NIFTY_FMCG = "NIFTY_FMCG"
    NIFTY_IT = "NIFTY_IT"
    NIFTY_MEDIA = "NIFTY_MEDIA"
    NIFTY_METAL = "NIFTY_METAL"
    NIFTY_PHARMA = "NIFTY_PHARMA"
    NIFTY_PSU_BANK = "NIFTY_PSU_BANK"
    NIFTY_PRIVATE_BANK = "NIFTY_PRIVATE_BANK"
    NIFTY_REALTY = "NIFTY_REALTY"
    NIFTY_HEALTHCARE = "NIFTY_HEALTHCARE"
    NIFTY_CONSUMER_DURABLES = "NIFTY_CONSUMER_DURABLES"
    NIFTY_OIL_GAS = "NIFTY_OIL_GAS"
    NIFTY_ENERGY = "NIFTY_ENERGY"
    NIFTY_INFRASTRUCTURE = "NIFTY_INFRASTRUCTURE"
    NIFTY_COMMODITIES = "NIFTY_COMMODITIES"
    ALL_SECTORS = "ALL_SECTORS"


class SectorContradictionDetail(BaseModel):
    """Detail of a single triggered contradiction flag with deterministic explanation."""

    model_config = ConfigDict(frozen=True)

    flag: SectorContradictionFlag
    description: str


class SectorEvidenceSummary(BaseModel):
    """Discrete breadth and evidence consensus summary for a single sector universe."""

    model_config = ConfigDict(frozen=True)

    sector_code: str
    sector_name: str
    category: str
    evaluation_date: date
    as_of_date: date | None
    comparison_window: int

    # Constituent Counts & Coverage
    total_eligible_constituents: int = Field(
        description="Total resolved constituents eligible for this sector at T"
    )
    evaluated_constituents: int = Field(
        description="Constituents evaluated without fatal DB/data errors (N_EVALUATED)"
    )
    analytical_valid_constituents: int = Field(
        description="Constituents with valid directional/neutral state (N_ANALYTICAL_VALID)"
    )
    insufficient_evidence_count: int = Field(
        description="Constituents classified as INSUFFICIENT_EVIDENCE (separate 5th state bucket)"
    )
    no_data_count: int = Field(
        description="Constituents with insufficient historical bars"
    )
    error_count: int = Field(
        description="Constituents that encountered unhandled evaluation errors"
    )
    data_coverage_pct: float = Field(
        description="N_ANALYTICAL_VALID / N_TOTAL_ELIGIBLE * 100.0"
    )
    insufficient_evidence_pct: float = Field(
        description="N_INSUFFICIENT_EVIDENCE / N_TOTAL_ELIGIBLE * 100.0"
    )
    coverage_status: str = Field(
        description="CONFIRMED, PARTIAL_DATA, INSUFFICIENT_DATA, or UNSUPPORTED_HISTORICAL_SECTOR_TAXONOMY"
    )

    # 5-State Distribution Counts & Directional Percentages (over N_ANALYTICAL_VALID)
    bullish_count: int = Field(description="Bullish evidence supported constituents")
    bearish_count: int = Field(description="Bearish evidence supported constituents")
    mixed_count: int = Field(description="Mixed / conflicted evidence constituents")
    neutral_count: int = Field(description="Neutral / context observation constituents")
    bullish_pct: float = Field(description="bullish_count / N_ANALYTICAL_VALID * 100.0")
    bearish_pct: float = Field(description="bearish_count / N_ANALYTICAL_VALID * 100.0")
    mixed_pct: float = Field(description="mixed_count / N_ANALYTICAL_VALID * 100.0")
    neutral_pct: float = Field(description="neutral_count / N_ANALYTICAL_VALID * 100.0")
    net_directional_breadth_pct: float = Field(
        description="bullish_pct - bearish_pct [-100.0..+100.0]"
    )

    # Convergence Metrics
    mean_convergence_ratio: float | None = Field(
        default=None,
        description="Arithmetic mean of D-3 convergence_ratio across constituents with ratios",
    )
    median_convergence_ratio: float | None = Field(
        default=None,
        description="Median of D-3 convergence_ratio across constituents with ratios",
    )

    # Domain Participation Rates & Counters
    volume_breakout_pct: float = Field(
        description="% of evaluated constituents with volume surge/breakout significance >= 70.0"
    )
    delivery_breakout_pct: float = Field(
        description="% of evaluated constituents with delivery breakout significance >= 70.0"
    )
    derivatives_eligible_count: int = Field(
        description="Constituents in sector with active F&O contract knowable at as_of"
    )
    derivatives_valid_count: int = Field(
        description="F&O constituents successfully evaluated with derivatives data"
    )
    derivatives_active_count: int = Field(
        description="F&O constituents exhibiting active D-1 open interest changes"
    )
    derivatives_active_pct: float = Field(
        description="derivatives_active_count / derivatives_valid_count * 100.0"
    )
    institutional_active_pct: float = Field(
        description="% of evaluated constituents with institutional deals or corporate filings"
    )

    # Contradictions & Warnings
    contradictions: list[SectorContradictionDetail] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)


class SectorEvidenceBreadthRequest(BaseModel):
    """Request model for sector evidence breadth aggregation."""

    model_config = ConfigDict(frozen=True)

    sectors: list[str] = Field(default_factory=lambda: ["ALL_SECTORS"])
    evaluation_date: date | None = None
    as_of_date: date | None = None
    comparison_window: int = Field(default=1, ge=1, le=30)
    min_data_coverage_pct: float = Field(default=50.0, ge=0.0, le=100.0)


class SectorEvidenceBreadthResponse(BaseModel):
    """Immutable response object containing sector breadth metrics and market aggregates."""

    model_config = ConfigDict(frozen=True)

    request_echo: SectorEvidenceBreadthRequest
    evaluation_date: date
    as_of_date: date | None
    comparison_window: int
    total_sectors_evaluated: int
    confirmed_sectors_count: int

    # Market-Wide Aggregate Metrics (Computed over Confirmed Sectors)
    sectors_bullish_pct: float = Field(
        description="% of confirmed sectors with net_directional_breadth_pct > 0.0"
    )
    sectors_bearish_pct: float = Field(
        description="% of confirmed sectors with net_directional_breadth_pct < 0.0"
    )
    sectors_neutral_pct: float = Field(
        description="% of confirmed sectors with net_directional_breadth_pct == 0.0"
    )
    market_mean_convergence_ratio: float | None = Field(
        default=None,
        description="Mean of sector mean_convergence_ratio across confirmed sectors",
    )

    sectors: list[SectorEvidenceSummary]
    generated_at: datetime
