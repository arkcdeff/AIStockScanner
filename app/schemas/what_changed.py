"""What Changed Schema — G.11-D-1 Deterministic Change Event & Result Models."""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChangeType(StrEnum):
    """Deterministic change event classification."""

    VALUE_INCREASE = "VALUE_INCREASE"
    VALUE_DECREASE = "VALUE_DECREASE"
    STATE_CHANGED = "STATE_CHANGED"
    APPEARED = "APPEARED"
    DISAPPEARED = "DISAPPEARED"
    THRESHOLD_CROSSED = "THRESHOLD_CROSSED"
    NO_LONGER_VALID = "NO_LONGER_VALID"
    DATA_BECAME_AVAILABLE = "DATA_BECAME_AVAILABLE"
    DATA_BECAME_UNAVAILABLE = "DATA_BECAME_UNAVAILABLE"


class ChangeSignificance(StrEnum):
    """Deterministic priority / importance ranking."""

    MAJOR = "MAJOR"
    SIGNIFICANT = "SIGNIFICANT"
    NORMAL = "NORMAL"
    MINOR = "MINOR"


class ChangeEvent(BaseModel):
    """Single atomic deterministic change observed between historical evaluation dates."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    evaluation_date: date
    comparison_date: date
    domain: str = Field(description="Domain: PRICE, VOLUME, TECHNICAL, TREND, SCANNER, CORPORATE, DEAL, DELIVERY, INSTITUTIONAL")
    metric: str = Field(description="Normalized key/identifier for the metric or attribute")
    previous_value: Any | None = None
    current_value: Any | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    previous_state: str | None = None
    current_state: str | None = None
    change_type: ChangeType
    significance: ChangeSignificance
    direction: str | None = Field(default=None, description="Directional bias if applicable: BULLISH, BEARISH, NEUTRAL, or None")
    source: str = Field(description="Underlying provenance source table or archive")
    confidence: str = Field(default="FACT_VERIFIED", description="Data quality / confidence rating")
    reason: str = Field(description="Deterministic explanation suitable for UI or API rendering")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Domain-specific contextual metadata")


class WhatChangedResult(BaseModel):
    """Aggregate deterministic result object for a stock across evaluation and comparison dates."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    evaluation_date: date
    comparison_date: date
    comparison_window_sessions: int
    generated_at: datetime
    pit_safe: bool = True
    total_changes: int
    major_changes: int
    significant_changes: int
    normal_changes: int
    minor_changes: int
    data_quality_summary: dict[str, Any]
    domain_summaries: dict[str, int]
    freshness: dict[str, Any]
    changes: list[ChangeEvent]
