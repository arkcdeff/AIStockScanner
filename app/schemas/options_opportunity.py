"""Pydantic Schemas for Options Opportunity, Expiry, Moneyness & Exact Strike Selection Engine — Sprint 19.1G-A3."""
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.advanced_options import OptionGreeksRead, RiskFreeRateInfo


class OptionDirectionalAssessment(BaseModel):
    """Underlying directional setup assessment."""

    direction: str = Field(description="BULLISH, BEARISH, NEUTRAL, CONFLICTED, INSUFFICIENT_DATA")
    side_recommendation: str = Field(description="CE, PE, NO_TRADE")
    direction_score: Decimal = Field(default=Decimal("50.00"), description="Directional score (0 to 100)")
    evidence_quality: str = Field(default="MEDIUM", description="HIGH, MEDIUM, LOW, INSUFFICIENT")

    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    methodology_version: str = "option_direction_v1"


class OptionExpiryAssessment(BaseModel):
    """Expiry suitability & ranking assessment."""

    preferred_expiry: date | None = None
    dte: int | None = None
    category: str = "NEAREST"  # NEAREST, NEXT, MONTHLY, OTHER
    expiry_score: Decimal = Decimal("50.00")
    evaluated_expiries: list[dict[str, Any]] = Field(default_factory=list)
    has_event_before_expiry: bool = False
    event_risk_notes: list[str] = Field(default_factory=list)
    methodology_version: str = "option_expiry_selection_v1"


class OptionMoneynessAssessment(BaseModel):
    """Moneyness & Delta quality classification."""

    classification: str = Field(description="DEEP_ITM, ITM, ATM, OTM, DEEP_OTM")
    delta_suitability: str = Field(description="EXCELLENT, GOOD, MARGINAL, UNACCEPTABLE")
    breakeven_within_expected_move: bool = True
    penalty_reasons: list[str] = Field(default_factory=list)
    methodology_version: str = "option_moneyness_v1"


class OptionStrikeAssessment(BaseModel):
    """Contract-level research score and strike assessment."""

    contract_id: int
    trading_symbol: str
    underlying: str
    option_type: str  # CE or PE
    expiry_date: date
    strike_price: Decimal
    moneyness: str  # DEEP_ITM, ITM, ATM, OTM, DEEP_OTM

    price: Decimal | None = None
    lot_size: int | None = None
    estimated_lot_premium_cost: Decimal | None = Field(default=None, description="Price * Lot Size")
    premium_at_risk_per_lot: Decimal | None = Field(default=None, description="Total capital at risk per contract")

    open_interest: int | None = None
    volume_contracts: int = 0
    iv: float | None = None
    greeks: OptionGreeksRead

    intrinsic_value: Decimal = Decimal("0.00")
    time_value: Decimal = Decimal("0.00")
    breakeven_price: Decimal = Decimal("0.00")
    breakeven_distance_pct: Decimal = Decimal("0.00")
    theta_burden_pct: float | None = Field(default=None, description="Daily decay burden (|Theta| / Price)")

    liquidity_classification: str = "UNKNOWN"
    strike_score: Decimal = Field(default=Decimal("0.00"), description="Weighted research score (0 to 100)")
    classification: str = Field(default="CANDIDATE", description="PREFERRED, ALTERNATIVE, REJECTED, WATCH")

    factor_scores: dict[str, Decimal] = Field(default_factory=dict)
    why_selected: list[str] = Field(default_factory=list)
    why_rejected: list[str] = Field(default_factory=list)
    veto_reasons: list[str] = Field(default_factory=list)

    data_quality: str = "HIGH"
    methodology_version: str = "option_strike_ranking_v1"


class OptionOpportunityAssessment(BaseModel):
    """Aggregate Options Research & Opportunity Assessment."""

    symbol: str
    trade_date: date
    as_of_date: date

    overall_classification: str = Field(
        default="NO_TRADE",
        description="STRONG_OPTION_RESEARCH_CANDIDATE, OPTION_RESEARCH_CANDIDATE, WATCH, WAIT, REJECTED, NO_TRADE, INSUFFICIENT_DATA",
    )
    selected_side: str = Field(default="NO_TRADE", description="CE, PE, NO_TRADE")

    directional_assessment: OptionDirectionalAssessment
    expiry_assessment: OptionExpiryAssessment
    moneyness_assessment: OptionMoneynessAssessment | None = None

    preferred_contract: OptionStrikeAssessment | None = None
    alternative_contracts: list[OptionStrikeAssessment] = Field(default_factory=list)
    rejected_contracts: list[OptionStrikeAssessment] = Field(default_factory=list)

    no_trade_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    rate_info: RiskFreeRateInfo = Field(default_factory=RiskFreeRateInfo)
    methodology_versions: dict[str, str] = Field(
        default_factory=lambda: {
            "direction": "option_direction_v1",
            "expiry": "option_expiry_selection_v1",
            "moneyness": "option_moneyness_v1",
            "ranking": "option_strike_ranking_v1",
        }
    )
    provenance: dict[str, Any] = Field(default_factory=dict)
