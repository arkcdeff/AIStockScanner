"""Schemas for Deterministic Derivative OI Intelligence — Stage G.8.

Provides structured models for:
  - Call and Put Wall shifts / movements between sessions
  - Multi-strike open interest concentration
  - Strike-level & contract-level buildup classification (LONG_BUILDUP, SHORT_BUILDUP, etc.)
  - Active strikes and OI spurts
  - Comparative session 'What changed?' summary and full snapshot
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OIWallMovement(BaseModel):
    """Tracks static wall level and movement relative to previous session."""

    model_config = ConfigDict(frozen=True)

    wall_type: str = Field(description="CALL_WALL or PUT_WALL")
    current_strike: Decimal = Field(description="Strike price with maximum OI in current session")
    previous_strike: Decimal | None = Field(default=None, description="Strike price with maximum OI in previous session")
    shift_points: Decimal | None = Field(default=None, description="Signed shift in strike points (current - previous)")
    shift_direction: str = Field(default="UNCHANGED", description="UP, DOWN, UNCHANGED, or NEW")
    current_oi: int = Field(default=0, description="Open interest at current wall strike")
    previous_oi: int | None = Field(default=None, description="Open interest at wall strike in previous session")
    oi_change: int | None = Field(default=None, description="Change in OI at wall strike")
    oi_share_pct: Decimal | None = Field(default=None, description="Percentage share of total call/put open interest")


class StrikeBuildupClassification(BaseModel):
    """Buildup classification for an individual option strike."""

    model_config = ConfigDict(frozen=True)

    strike_price: Decimal
    option_type: str  # CE or PE
    ltp: Decimal | None = None
    price_change: Decimal | None = None
    price_change_pct: Decimal | None = None
    open_interest: int = 0
    change_in_oi: int = 0
    oi_change_pct: Decimal | None = None
    volume: int = 0
    buildup_state: str = Field(
        default="NEUTRAL",
        description="LONG_BUILDUP, SHORT_BUILDUP, SHORT_COVERING, LONG_UNWINDING, or neutral states",
    )


class MultiStrikeConcentration(BaseModel):
    """Concentration of open interest across top strikes."""

    model_config = ConfigDict(frozen=True)

    top1_strike: Decimal | None = None
    top1_oi: int | None = None
    top1_share_pct: Decimal | None = None
    top3_strikes: list[Decimal] = Field(default_factory=list)
    top3_oi: int | None = None
    top3_share_pct: Decimal | None = None
    top5_strikes: list[Decimal] = Field(default_factory=list)
    top5_oi: int | None = None
    top5_share_pct: Decimal | None = None


class ActiveStrikeItem(BaseModel):
    """An active option strike ranked by volume or open interest."""

    model_config = ConfigDict(frozen=True)

    strike_price: Decimal
    option_type: str
    volume: int = 0
    open_interest: int = 0
    change_in_oi: int = 0
    turnover: Decimal | None = None
    ltp: Decimal | None = None


class OISpurtItem(BaseModel):
    """A contract or strike exhibiting sudden high surge in open interest."""

    model_config = ConfigDict(frozen=True)

    strike_price: Decimal
    option_type: str
    current_oi: int
    previous_oi: int
    oi_change: int
    oi_growth_pct: Decimal
    volume: int


class ComparativeSessionSummary(BaseModel):
    """Comparative 'What changed?' summary between session T and T-1."""

    model_config = ConfigDict(frozen=True)

    underlying_symbol: str
    trade_date: date
    previous_trade_date: date | None = None
    current_spot: Decimal | None = None
    previous_spot: Decimal | None = None
    spot_change: Decimal | None = None
    spot_change_pct: Decimal | None = None
    total_call_oi: int = 0
    total_put_oi: int = 0
    net_call_oi_change: int = 0
    net_put_oi_change: int = 0
    current_pcr_oi: Decimal | None = None
    previous_pcr_oi: Decimal | None = None
    pcr_oi_change: Decimal | None = None
    delta_oi_pcr: Decimal | None = None
    volume_pcr: Decimal | None = None
    call_wall_movement: OIWallMovement | None = None
    put_wall_movement: OIWallMovement | None = None
    long_buildup_count: int = 0
    short_buildup_count: int = 0
    short_covering_count: int = 0
    long_unwinding_count: int = 0
    predominant_buildup: str = "NEUTRAL"
    narrative_summary: str = ""


class OIIntelligenceSnapshot(BaseModel):
    """Top-level aggregated OI Intelligence Snapshot for a trading session."""

    model_config = ConfigDict(frozen=True)

    underlying_symbol: str
    underlying_name: str
    trade_date: date
    expiry_date: date
    calendar_days_to_expiry: int
    as_of_date: date
    resolved_spot: Decimal | None = None
    summary: ComparativeSessionSummary
    call_wall: OIWallMovement
    put_wall: OIWallMovement
    concentration_call: MultiStrikeConcentration
    concentration_put: MultiStrikeConcentration
    active_strikes_by_volume: list[ActiveStrikeItem] = Field(default_factory=list)
    active_strikes_by_oi: list[ActiveStrikeItem] = Field(default_factory=list)
    oi_spurts: list[OISpurtItem] = Field(default_factory=list)
    strike_buildups: list[StrikeBuildupClassification] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
