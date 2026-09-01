"""Stock Intelligence / Decision Context Schemas — Stage 2.6.5 Phase F.

Provides typed models for point-in-time safe decision context aggregation,
evidence classification, factor attribution, and provenance tracking.

Strict Rules:
  - MISSING != ZERO (Missing data is recorded as UNAVAILABLE in missing_inputs/watch_items, never 0 or neutral).
  - COMPLETENESS != CONFIDENCE (Completeness measures data availability; confidence measures evidence reliability/concordance).
  - DETERMINISTIC (No randomness, no LLM required for deterministic state).
  - NO BUY/SELL CLAIMS (Decision context only; not investment advice).
"""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class OverallState(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class EvidenceDirection(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"


class EvidenceDomain(StrEnum):
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    VALUATION = "valuation"
    NEWS = "news"
    CORPORATE_EVENTS = "corporate_events"
    MARKET_CONTEXT = "market_context"
    SECTOR = "sector"
    DELIVERY = "delivery"


class FactorItem(BaseModel):
    """A human-readable factor attributed to a specific domain."""

    domain: str
    factor: str
    description: str
    direction: EvidenceDirection
    value: Any | None = None
    source: str | None = None


class EvidenceItem(BaseModel):
    """A granular evidence point evaluated for the stock decision context."""

    domain: str
    factor: str
    direction: EvidenceDirection
    description: str
    value: Any | None = None
    evaluation_date: date | None = None
    source: str
    status: str = "AVAILABLE"  # AVAILABLE, PARTIAL, UNAVAILABLE


class StockIntelligence(BaseModel):
    """Point-in-time deterministic decision context snapshot for a stock."""

    symbol: str
    stock_id: int | None = None
    company_name: str | None = None
    evaluation_date: date
    as_of_date: date

    # Overall Classification
    overall_state: OverallState = OverallState.INSUFFICIENT_DATA
    state_reason: str = ""

    # Confidence & Completeness
    confidence: float = 0.0  # 0.0 to 1.0 (reliability and signal concordance)
    confidence_label: str = "INSUFFICIENT"  # HIGH, MODERATE, LOW, INSUFFICIENT
    completeness_pct: float = 0.0  # 0.0 to 100.0 (% of evaluated domains available)
    completeness_label: str = "CRITICAL_GAPS"  # ROBUST, PARTIAL, LOW, CRITICAL_GAPS

    # Factor Breakdowns
    supporting_factors: list[FactorItem] = Field(default_factory=list)
    risk_factors: list[FactorItem] = Field(default_factory=list)
    watch_items: list[FactorItem] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)

    # Detailed Evidence & Provenance
    evidence: list[EvidenceItem] = Field(default_factory=list)
    domain_coverage: dict[str, str] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    quality_warnings: list[str] = Field(default_factory=list)

    disclaimer: str = (
        "Deterministic decision context based on available stored data. "
        "Not an investment recommendation."
    )
