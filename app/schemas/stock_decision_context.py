"""Stock Decision Context Schema — G.11-D-5 Decision Context Fusion Layer.

All models are immutable (frozen) and deterministic, consistent with G.11-D-3.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.stock360 import TechnicalSummaryRead, VolumeDeliverySummaryRead
from app.schemas.what_changed_interpretation import (
    EvidenceItem,
    Limitation,
    NarrativeResult,
    PatternLifecycleRecord,
    WatchItem,
)
from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    OverallState,
)


class ProfileAlignmentState(StrEnum):
    """Deterministic strategic alignment between D-3 dynamic evidence and Opportunity Profiles."""

    ALIGNED = "ALIGNED"
    PARTIALLY_ALIGNED = "PARTIALLY_ALIGNED"
    BLOCKED = "BLOCKED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProfileEvidenceAlignment(BaseModel):
    """Detailed deterministic alignment report for a single opportunity profile."""

    model_config = ConfigDict(frozen=True)

    profile: str = Field(description="Profile identifier, e.g. BREAKOUT, QUALITY_PULLBACK, SWING")
    alignment_state: ProfileAlignmentState
    canonical_classification: str = Field(description="Classification from OpportunityEngine")
    canonical_score: float = Field(description="Deterministic score from OpportunityEngine (0-100)")
    supporting_evidence: list[str] = Field(default_factory=list)
    conflicting_evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    veto_reasons: list[str] = Field(default_factory=list)
    alignment_summary: str = Field(description="Deterministic summary explanation of alignment")


class StockDecisionContextRead(BaseModel):
    """Complete deterministic Stock 360 & Decision Context Fusion research dossier."""

    model_config = ConfigDict(frozen=True)

    # Identity
    symbol: str
    company_name: str | None = None
    sector: str | None = None
    sector_name: str | None = None

    # Evaluation Context
    evaluation_date: date
    as_of_date: date
    comparison_date: date | None = None
    comparison_window: int = 1
    comparison_window_sessions: int = 1
    pit_safe: bool = True

    # What-Changed Dynamic Intelligence (from certified D-3)
    overall_state: OverallState
    directional_bias: DirectionalBias
    convergence_ratio: float | None = None
    supporting_domains: list[str] = Field(default_factory=list)
    conflicting_domains: list[str] = Field(default_factory=list)
    context_domains: list[str] = Field(default_factory=list)
    missing_domains: list[str] = Field(default_factory=list)
    lifecycle_summary: str | None = None
    pattern_lifecycle_records: list[PatternLifecycleRecord] = Field(default_factory=list)
    primary_evidence: list[EvidenceItem] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    conflicting_evidence: list[EvidenceItem] = Field(default_factory=list)
    context_evidence: list[EvidenceItem] = Field(default_factory=list)

    # Stock 360 Research Dimensions
    technicals: TechnicalSummaryRead | None = None
    volume_delivery: VolumeDeliverySummaryRead | None = None
    relative_strength: dict[str, Any] = Field(default_factory=dict)
    fundamentals: dict[str, Any] = Field(default_factory=dict)
    derivatives: dict[str, Any] = Field(default_factory=dict)
    news_policy: dict[str, Any] = Field(default_factory=dict)
    data_coverage: dict[str, str] = Field(default_factory=dict)
    data_completeness_pct: float = 0.0
    quality_warnings: list[str] = Field(default_factory=list)

    # Opportunity Profiles Evidence Alignment
    profile_alignments: list[ProfileEvidenceAlignment] = Field(default_factory=list)
    active_vetoes: list[str] = Field(default_factory=list)

    # Transparency: Watch Items & Limitations
    watch_items: list[WatchItem] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)

    # AI / Fallback Narrative
    narrative: NarrativeResult | None = None
    fallback_narrative: str = ""

    generated_at: datetime
