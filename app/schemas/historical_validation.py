"""Pydantic Schemas for Historical Validation & Walk-Forward Calibration Engine — Sprint 19.1I."""
import math
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ValidationSignalSnapshot(BaseModel):
    """Immutable representation of signal facts frozen at cutoff T (PIT)."""

    evaluation_date: date
    as_of_date: date
    symbol: str
    profile: str = "SWING"  # SWING, BREAKOUT, BTST, QUALITY_PULLBACK, INVESTMENT_QUALITY, OPTIONS
    methodology_version: str

    score: Decimal = Decimal("50.00")
    classification: str
    selected_side: str = "NO_TRADE"  # CE, PE, NO_TRADE
    selected_expiry: date | None = None
    selected_contract_id: int | None = None
    selected_trading_symbol: str | None = None

    supporting_evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    vetoes: list[str] = Field(default_factory=list)

    data_completeness: str = "HIGH"
    evidence_quality: str = "HIGH"


class ValidationOutcome(BaseModel):
    """Separated market price outcome evaluation after signal freeze."""

    entry_reference: str = "NEXT_SESSION_OPEN"  # NEXT_SESSION_OPEN, SAME_CLOSE_RESEARCH
    entry_date: date
    entry_price: Decimal

    horizon_days: int
    exit_date: date
    exit_price: Decimal
    highest_price: Decimal
    lowest_price: Decimal

    forward_return_pct: Decimal = Field(description="Period return %")
    mfe_pct: Decimal = Field(description="Maximum Favorable Excursion % (High vs Entry)")
    mae_pct: Decimal = Field(description="Maximum Adverse Excursion % (Low vs Entry)")

    benchmark_symbol: str = "NIFTY"
    benchmark_return_pct: Decimal = Decimal("0.00")
    excess_return_pct: Decimal = Decimal("0.00")


class OptionValidationOutcome(BaseModel):
    """Option-specific contract outcome evaluation."""

    entry_reference: str = "NEXT_AVAILABLE_OPTION_OBSERVATION"
    entry_date: date
    contract_id: int
    trading_symbol: str
    option_type: str  # CE or PE
    strike_price: Decimal
    expiry_date: date

    option_entry_price: Decimal
    option_exit_price: Decimal
    option_return_pct: Decimal

    underlying_entry_price: Decimal
    underlying_exit_price: Decimal
    underlying_return_pct: Decimal
    directional_accuracy: bool = Field(description="True if underlying moved in predicted direction")

    option_mfe_pct: Decimal = Decimal("0.00")
    option_mae_pct: Decimal = Decimal("0.00")
    days_held: int = 1

    atm_benchmark_return_pct: Decimal | None = Field(default=None, description="ATM contract return comparator")
    strike_selection_excess_return_pct: Decimal | None = Field(default=None, description="Preferred vs ATM return %")


class ValidationCase(BaseModel):
    """Paired signal snapshot and outcome evaluation for individual case auditing."""

    case_id: str
    signal: ValidationSignalSnapshot
    stock_outcome: ValidationOutcome | None = None
    option_outcome: OptionValidationOutcome | None = None
    is_positive_outcome: bool = False
    notes: list[str] = Field(default_factory=list)


class ScoreBucketMetrics(BaseModel):
    """Calibration statistics for a score range bucket."""

    score_range: str  # 90-100, 80-89, 70-79, 60-69, <60
    sample_count: int = 0
    mean_return_pct: Decimal = Decimal("0.00")
    median_return_pct: Decimal = Decimal("0.00")
    positive_outcome_rate: Decimal = Decimal("0.00")
    wilson_ci_lower: Decimal = Decimal("0.00")
    wilson_ci_upper: Decimal = Decimal("0.00")
    mean_mfe_pct: Decimal = Decimal("0.00")
    mean_mae_pct: Decimal = Decimal("0.00")
    mean_excess_return_pct: Decimal = Decimal("0.00")
    sample_quality_flag: str = "SUFFICIENT"  # SUFFICIENT, LIMITED, INSUFFICIENT


class ValidationSummary(BaseModel):
    """Aggregate out-of-sample historical validation & calibration summary."""

    methodology_version: str
    profile: str
    start_date: date
    end_date: date
    universe: str = "NIFTY_FNO"
    sample_count: int = 0

    overall_mean_return_pct: Decimal = Decimal("0.00")
    overall_median_return_pct: Decimal = Decimal("0.00")
    overall_positive_rate: Decimal = Decimal("0.00")
    overall_benchmark_excess_pct: Decimal = Decimal("0.00")
    overall_mean_mfe_pct: Decimal = Decimal("0.00")
    overall_mean_mae_pct: Decimal = Decimal("0.00")

    score_buckets: list[ScoreBucketMetrics] = Field(default_factory=list)
    evidence_quality_breakdown: dict[str, Any] = Field(default_factory=dict)
    regime_breakdown: dict[str, Any] = Field(default_factory=dict)

    directional_accuracy_rate: Decimal | None = None
    strike_selection_excess_return_pct: Decimal | None = None

    warnings: list[str] = Field(default_factory=list)
    calibration_recommendations: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


def compute_wilson_score_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Compute Wilson score interval for binary proportions."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denom
    spread = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denom
    lower = max(0.0, center - spread)
    upper = min(1.0, center + spread)
    return round(lower * 100.0, 2), round(upper * 100.0, 2)
