"""Data Schemas for Advanced Options Exposure Analytics — Sprint 19.1G-A2.

Provides Pydantic schemas for deterministic options exposure analytics:
  - Contract & Side Exposure Facts (Gross GEX, Scenario GEX, DEX, Vanna, Charm)
  - Strike Exposure Row
  - Expiry Exposure Row
  - Spot Scenario Gamma Profile & Grid
  - Scenario Gamma Flip (Zero-Gamma level) result
  - Gamma Concentration metrics
  - Advanced Exposure Snapshot
  - Advanced Exposure Context hook for Stock 360°
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.advanced_options import OptionGreeksRead, RiskFreeRateInfo


class OptionExposureRow(BaseModel):
    """Contract / Side level exposure facts."""

    contract_id: int
    trading_symbol: str
    option_type: str  # CE or PE
    strike_price: Decimal
    expiry_date: date
    dte: int
    open_interest: int | None = None
    volume_contracts: int = 0
    lot_size: int | None = Field(default=1, description="Lot size used for contract, or None if unavailable")
    lot_size_source: str = "HISTORICAL_PIT"  # HISTORICAL_PIT, SOURCE_VERIFIED, UNAVAILABLE
    lot_size_quality: str = "VERIFIED_HISTORICAL"  # VERIFIED_HISTORICAL, SOURCE_VERIFIED, UNAVAILABLE
    price_used: Decimal | None = None

    iv: float | None = None
    greeks: OptionGreeksRead

    gross_gamma_exposure: Decimal | None = Field(default=None, description="Gross GEX (INR Notional per 1% spot move)")
    scenario_gex: Decimal | None = Field(default=None, description="Scenario-Signed GEX under assumption profile")

    gross_delta_exposure: Decimal | None = Field(default=None, description="Gross DEX (INR Notional)")
    scenario_dex: Decimal | None = Field(default=None, description="Scenario-Signed DEX under assumption profile")

    gross_vanna_exposure: Decimal | None = Field(default=None, description="Gross Vanna Exposure (INR Notional per 1% IV move)")
    scenario_vanna_exposure: Decimal | None = Field(default=None, description="Scenario-Signed Vanna Exposure")

    gross_charm_exposure: Decimal | None = Field(default=None, description="Gross Charm Exposure (INR Notional per day)")
    scenario_charm_exposure: Decimal | None = Field(default=None, description="Scenario-Signed Charm Exposure")


class StrikeExposureRow(BaseModel):
    """Paired strike row containing CE and PE aggregate exposure metrics."""

    strike_price: Decimal
    is_atm: bool = False

    ce_open_interest: int = 0
    pe_open_interest: int = 0
    total_open_interest: int = 0

    ce_gross_gex: Decimal = Decimal("0.00")
    pe_gross_gex: Decimal = Decimal("0.00")
    total_gross_gex: Decimal = Decimal("0.00")

    ce_scenario_gex: Decimal = Decimal("0.00")
    pe_scenario_gex: Decimal = Decimal("0.00")
    net_scenario_gex: Decimal = Decimal("0.00")

    gross_dex: Decimal = Decimal("0.00")
    net_scenario_dex: Decimal = Decimal("0.00")

    gross_vanna: Decimal = Decimal("0.00")
    gross_charm: Decimal = Decimal("0.00")


class ExpiryExposureRow(BaseModel):
    """Aggregate exposure metrics per expiry date."""

    expiry_date: date
    dte: int
    resolved_spot: Decimal

    gross_call_gex: Decimal = Decimal("0.00")
    gross_put_gex: Decimal = Decimal("0.00")
    gross_total_gex: Decimal = Decimal("0.00")

    scenario_net_gex: Decimal = Decimal("0.00")
    gross_total_dex: Decimal = Decimal("0.00")
    scenario_net_dex: Decimal = Decimal("0.00")

    largest_gamma_strikes: list[Decimal] = Field(default_factory=list)
    top1_strike_share_pct: Decimal = Decimal("0.00")


class GammaProfilePoint(BaseModel):
    """Single spot grid evaluation point for Scenario Gamma Profile."""

    spot_level: Decimal
    spot_change_pct: Decimal
    gross_gex: Decimal
    scenario_net_gex: Decimal


class GammaFlipResult(BaseModel):
    """Scenario Gamma Flip (Zero Gamma) level detection result."""

    gamma_flip_level: Decimal | None = Field(default=None, description="Interpolated zero-crossing spot level")
    status: str = Field(
        default="NO_FLIP_IN_RANGE",
        description="FLIP_FOUND, NO_FLIP_IN_RANGE, MULTIPLE_FLIPS_FOUND, SIGNED_ASSUMPTION_REQUIRED, INSUFFICIENT_DATA",
    )
    distance_from_spot: Decimal | None = None
    distance_pct: Decimal | None = None
    grid_lower_bound: Decimal
    grid_upper_bound: Decimal
    crossing_count: int = 0
    all_crossings: list[Decimal] = Field(default_factory=list)
    assumption_profile: str = "DEALER_SHORT_CALL_LONG_PUT_HEURISTIC"
    methodology: str = "SPOT_GRID_LINEAR_INTERPOLATION_STATIC_IV"
    disclaimer: str = (
        "Gamma Flip is an exposure-structure reference level under the configured scenario profile. "
        "It is NOT a guaranteed price reversal level, support, or resistance."
    )


class GammaConcentration(BaseModel):
    """Exposure concentration metrics across strikes."""

    top1_strike: Decimal | None = None
    top1_gex: Decimal = Decimal("0.00")
    top1_share_pct: Decimal = Decimal("0.00")

    top3_strikes: list[Decimal] = Field(default_factory=list)
    top3_share_pct: Decimal = Decimal("0.00")

    top5_strikes: list[Decimal] = Field(default_factory=list)
    top5_share_pct: Decimal = Decimal("0.00")


class AdvancedExposureSnapshot(BaseModel):
    """Comprehensive snapshot of options exposure analytics."""

    symbol: str
    trade_date: date
    as_of_date: date
    resolved_spot: Decimal
    expiry_scope: str = "ALL_AVAILABLE"  # NEAR_EXPIRY, FRONT_EXPIRY, ALL_AVAILABLE

    assumption_profile: str = "GROSS_UNSIGNED"  # GROSS_UNSIGNED, DEALER_SHORT_CALL_LONG_PUT_HEURISTIC
    assumption_warning: str = (
        "Aggregate Open Interest does NOT reveal actual holder position direction. "
        "Gross values represent factual magnitude. Signed values depend strictly on the selected scenario profile."
    )

    gross_call_gex: Decimal = Decimal("0.00")
    gross_put_gex: Decimal = Decimal("0.00")
    gross_total_gex: Decimal = Decimal("0.00")

    scenario_call_gex: Decimal = Decimal("0.00")
    scenario_put_gex: Decimal = Decimal("0.00")
    scenario_net_gex: Decimal = Decimal("0.00")

    gross_total_dex: Decimal = Decimal("0.00")
    scenario_net_dex: Decimal = Decimal("0.00")

    gross_total_vanna: Decimal = Decimal("0.00")
    gross_total_charm: Decimal = Decimal("0.00")

    gamma_flip: GammaFlipResult
    concentration: GammaConcentration

    exposure_quality: str = "MEDIUM"  # HIGH, MEDIUM, LOW, INSUFFICIENT
    rate_info: RiskFreeRateInfo = Field(default_factory=RiskFreeRateInfo)
    dividend_assumption: str = "ZERO_DIVIDEND_ASSUMPTION"
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class AdvancedExposureContext(BaseModel):
    """Advanced Exposure hook for Stock 360° & Opportunity Engine."""

    symbol: str
    trade_date: date
    resolved_spot: Decimal
    is_fno_security: bool = True

    gross_total_gex: Decimal | None = None
    scenario_net_gex: Decimal | None = None
    gamma_flip_level: Decimal | None = None
    distance_to_gamma_flip_pct: Decimal | None = None

    gross_total_dex: Decimal | None = None
    top1_gamma_strike: Decimal | None = None
    top1_gamma_share_pct: Decimal | None = None

    exposure_quality: str = "MEDIUM"
    warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
