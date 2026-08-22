"""Data Schemas for Advanced Options Foundation — Sprint 19.1G-A1.

Provides Pydantic schemas for deterministic quantitative options analytics:
  - Option Implied Volatility (IV) result & solver metadata
  - Option Greeks (Delta, Gamma, Theta, Vega, Rho)
  - Moneyness classification (DEEP_ITM, ITM, ATM, OTM, DEEP_OTM)
  - Breakeven metrics
  - Liquidity assessment
  - Per-contract option risk profile
  - Enriched Option Chain side and row schemas
  - Volatility structure schemas (IV Smile, IV Skew, IV Term Structure)
  - Expected Move analytics schema
  - Advanced Options Context hook schema for Stock 360 / Opportunity Engine
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class RiskFreeRateInfo(BaseModel):
    """Metadata and provenance for risk-free interest rate input."""

    rate: float = Field(default=0.065, description="Annualized risk-free interest rate (e.g. 0.065 = 6.5%)")
    rate_source: str = Field(default="CONFIGURED_FALLBACK", description="CONFIGURED_FALLBACK, EXPLICIT_PARAMETER, HISTORICAL_PIT_SERIES")
    rate_quality: str = Field(default="APPROXIMATION", description="APPROXIMATION, VALID, OFFICIAL")
    rate_date: date | None = Field(default=None, description="PIT date associated with interest rate observation")


class OptionIVResult(BaseModel):
    """Result of deterministic Black-Scholes implied volatility solver."""

    iv: float | None = Field(default=None, description="Annualized implied volatility (e.g. 0.22 = 22%)")
    solver_status: str = Field(
        default="NOT_COMPUTED",
        description="CONVERGED, SOLVER_FAILED, INTRINSIC_VIOLATION, ABOVE_THEORETICAL_BOUND, ZERO_DTE, INVALID_PRICE, EXPIRED",
    )
    iterations: int = Field(default=0, description="Number of solver iterations executed")
    input_quality: str = Field(default="VALID", description="Data quality status for IV calculation")
    methodology_version: str = Field(default="v1.0", description="Version of IV solver algorithm")


class OptionGreeksRead(BaseModel):
    """Standard Black-Scholes Option Greeks."""

    delta: float | None = Field(default=None, description="Option Delta (rate of price change per 1.0 underlying move)")
    gamma: float | None = Field(default=None, description="Option Gamma (rate of Delta change per 1.0 underlying move)")
    theta_per_day: float | None = Field(default=None, description="Option Theta per calendar day (annualized Theta / 365)")
    vega_per_pct: float | None = Field(default=None, description="Option Vega per 1.0 percentage point change in IV")
    rho_per_pct: float | None = Field(default=None, description="Option Rho per 1.0 percentage point change in interest rate")
    vanna_per_pct: float | None = Field(default=None, description="Option Vanna per 1.0 percentage point change in IV (rate of Vega per spot move)")
    charm_per_day: float | None = Field(default=None, description="Option Charm per calendar day (annualized rate of Delta change per passage of time)")


class OptionMoneynessRead(BaseModel):
    """Deterministic moneyness classification & distance facts."""

    classification: str = Field(description="DEEP_ITM, ITM, ATM, OTM, DEEP_OTM")
    distance_from_spot: Decimal = Field(description="Absolute distance from spot price")
    distance_pct: Decimal = Field(description="Percentage distance from spot price")
    intrinsic_value: Decimal = Field(description="Pure intrinsic value (max(0, S - K) for CE, max(0, K - S) for PE)")
    time_value: Decimal = Field(description="Extrinsic/time value (max(0, option_price - intrinsic_value))")


class OptionBreakevenRead(BaseModel):
    """Long option breakeven price and distance metrics."""

    breakeven_price: Decimal = Field(description="Breakeven price at expiry (Strike + Premium for CE, Strike - Premium for PE)")
    distance_to_breakeven: Decimal = Field(description="Absolute underlying move required to break even")
    distance_to_breakeven_pct: Decimal = Field(description="Percentage underlying move required to break even")


class OptionLiquidityAssessment(BaseModel):
    """Option contract liquidity classification & trading activity facts."""

    classification: str = Field(description="HIGH, MEDIUM, LOW, ILLIQUID, UNKNOWN")
    volume_contracts: int = Field(default=0, description="Contracts traded on trade date")
    open_interest: int | None = Field(default=None, description="Open interest in contracts")
    bid: Decimal | None = Field(default=None, description="Best bid price if available")
    ask: Decimal | None = Field(default=None, description="Best ask price if available")
    spread: Decimal | None = Field(default=None, description="Ask - Bid spread if available")
    spread_pct: Decimal | None = Field(default=None, description="Spread as percentage of midpoint")
    has_bid_ask_data: bool = Field(default=False, description="True if bid/ask prices are available")


class OptionRiskProfile(BaseModel):
    """Quantitative risk profile per contract."""

    premium_at_risk: Decimal = Field(description="Total capital at risk per contract (price * lot_size)")
    intrinsic_value: Decimal = Field(description="Intrinsic value component")
    time_value: Decimal = Field(description="Time value component")
    theta_per_day: float | None = Field(default=None, description="Daily decay rate per contract")
    gamma: float | None = Field(default=None, description="Curvature/Gamma risk")
    vega_per_pct: float | None = Field(default=None, description="Volatility sensitivity")
    iv: float | None = Field(default=None, description="Implied Volatility")
    dte: int = Field(description="Days To Expiry")
    liquidity_class: str = Field(description="Liquidity classification")
    breakeven_distance_pct: Decimal = Field(description="Distance % to breakeven")


class EnrichedOptionChainSide(BaseModel):
    """Enriched option side observation with IV, Greeks, Moneyness, Breakeven, and Liquidity."""

    contract_id: int
    trading_symbol: str
    option_type: str  # CE or PE
    strike_price: Decimal
    price_used: Decimal | None = None
    price_source: str = "SETTLEMENT"  # SETTLEMENT, CLOSE, LAST
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    last_price: Decimal | None = None
    settlement_price: Decimal | None = None
    volume_contracts: int = 0
    open_interest: int | None = None
    change_in_open_interest: int | None = None
    iv_result: OptionIVResult
    greeks: OptionGreeksRead
    moneyness: OptionMoneynessRead
    breakeven: OptionBreakevenRead
    liquidity: OptionLiquidityAssessment
    risk_profile: OptionRiskProfile
    rate_info: RiskFreeRateInfo = Field(default_factory=RiskFreeRateInfo)
    dividend_yield: float = 0.0
    dividend_assumption: str = "ZERO_DIVIDEND_ASSUMPTION"


class EnrichedOptionChainRow(BaseModel):
    """Enriched strike row containing paired CE and PE sides with analytics."""

    strike_price: Decimal
    is_atm: bool = False
    is_paired: bool = False
    call: EnrichedOptionChainSide | None = None
    put: EnrichedOptionChainSide | None = None


class ExpectedMoveAnalytics(BaseModel):
    """Expected move calculations across expiry windows."""

    expiry_date: date
    dte: int
    resolved_spot: Decimal
    atm_strike: Decimal
    atm_iv: float | None
    expected_move_points: Decimal
    expected_move_pct: Decimal
    upper_bound: Decimal
    lower_bound: Decimal
    straddle_price: Decimal | None = None
    straddle_expected_move_points: Decimal | None = None
    straddle_expected_move_pct: Decimal | None = None
    methodology: str = "PRIMARY_IV_SQRT_TIME"
    straddle_methodology: str | None = "ATM_STRADDLE_85PCT_HEURISTIC"
    rate_info: RiskFreeRateInfo = Field(default_factory=RiskFreeRateInfo)
    dividend_yield: float = 0.0
    dividend_assumption: str = "ZERO_DIVIDEND_ASSUMPTION"


class IVSmileItem(BaseModel):
    """IV Smile data point at a specific strike."""

    strike_price: Decimal
    moneyness: str
    distance_pct: Decimal
    ce_iv: float | None = None
    pe_iv: float | None = None
    representative_iv: float | None = None


class IVSkewAnalytics(BaseModel):
    """IV Skew metrics per expiry."""

    expiry_date: date
    dte: int
    atm_strike: Decimal
    atm_iv: float | None = None
    otm_put_iv: float | None = None
    otm_call_iv: float | None = None
    skew_spread: float | None = Field(default=None, description="OTM Put IV minus OTM Call IV")
    skew_ratio: float | None = Field(default=None, description="OTM Put IV / OTM Call IV")
    interpretation: str = "SKEW_NEUTRAL"  # SKEW_PUT_PREMIUM, SKEW_CALL_PREMIUM, SKEW_NEUTRAL


class IVTermStructureItem(BaseModel):
    """IV Term Structure entry per expiry."""

    expiry_date: date
    dte: int
    atm_strike: Decimal
    atm_iv: float | None = None
    is_front_expiry: bool = False
    total_oi: int = 0
    total_volume: int = 0


class AdvancedOptionsContext(BaseModel):
    """Advanced Options hook for Stock 360 & Opportunity Engine."""

    symbol: str
    trade_date: date
    resolved_spot: Decimal
    is_fno_security: bool = True
    front_expiry: date | None = None
    dte: int | None = None
    atm_iv: float | None = None
    iv_regime: str = "UNKNOWN"  # HIGH_IV, NORMAL_IV, LOW_IV, UNKNOWN
    expected_move_pct: Decimal | None = None
    skew_spread: float | None = None
    term_structure_slope: str = "FLAT"  # CONTANGO, BACKWARDATION, FLAT
    liquidity_quality: str = "MEDIUM"
    rate_info: RiskFreeRateInfo = Field(default_factory=RiskFreeRateInfo)
    dividend_yield: float = 0.0
    dividend_assumption: str = "ZERO_DIVIDEND_ASSUMPTION"
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
