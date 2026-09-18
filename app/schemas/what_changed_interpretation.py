"""What Changed Interpretation Schema — G.11-D-3 Stock Intelligence Interpretation Models.

All models are immutable (frozen) and deterministic, consistent with G.11-D-2.
Every field maps to a certified D-2 source or a deterministic transformation of it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    EvidenceClassification,
    LifecycleChangeDescriptor,
    OverallState,
)
from app.services.what_changed_synthesis_config import (
    EvidenceQuality,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)


class EvidenceItem(BaseModel):
    """Single deterministically-classified piece of D-2 evidence.

    ``reason`` and ``metadata`` are passed through verbatim from the certified
    D-2 relationship — D-3 never recomputes them.
    """

    model_config = ConfigDict(frozen=True)

    domain: str = Field(description="Primary domain of this evidence item")
    domains_involved: list[str] = Field(default_factory=list)
    metrics_involved: list[str] = Field(default_factory=list)
    relationship_type: RelationshipType
    strength: RelationshipStrength
    direction: str | None = None
    classification: EvidenceClassification
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: list[str] = Field(default_factory=list)
    pattern_fingerprint: str = Field(
        default="", description="Deterministic semantic identity of the pattern"
    )


class DirectionalConvergence(BaseModel):
    """D-3 corrected directional convergence.

    Unlike D-2's ``max_possible`` (which may include context-only domains),
    this representation counts only unique directional domains
    (supporting + conflicting), excluding context and missing domains.
    """

    model_config = ConfigDict(frozen=True)

    directional_value: int = Field(
        description="Count of unique directional domains providing supporting evidence"
    )
    directional_max_possible: int = Field(
        description="Count of unique directional domains evaluated (supporting + conflicting)"
    )
    convergence_ratio: float | None = Field(
        default=None,
        description="directional_value / directional_max_possible (None when no directional domain was evaluated)",
    )
    supporting_domains: list[str] = Field(default_factory=list)
    conflicting_domains: list[str] = Field(default_factory=list)
    context_domains: list[str] = Field(
        default_factory=list,
        description="Context-only domains (CORPORATE, INSTITUTIONAL, DEAL) — never counted directionally",
    )
    missing_domains: list[str] = Field(default_factory=list)
    interpretation: str


class PatternLifecycleRecord(BaseModel):
    """Per-pattern lifecycle tracking between current (T) and previous (T-1) D-2 snapshots."""

    model_config = ConfigDict(frozen=True)

    pattern_fingerprint: str
    domains_involved: list[str] = Field(default_factory=list)
    metrics_involved: list[str] = Field(default_factory=list)
    relationship_type: RelationshipType
    direction: str | None = None
    current_strength: RelationshipStrength | None = None
    previous_strength: RelationshipStrength | None = None
    lifecycle_state: PatternLifecycle
    change_descriptor: LifecycleChangeDescriptor | None = None
    data_gap: bool = Field(
        default=False,
        description="True when disappearance is caused by data unavailability, not evidence reversal",
    )
    description: str


class OverallLifecycleSummary(BaseModel):
    """Deterministic aggregate of pattern-level lifecycle records.

    Never replaces pattern-level records and never determined by an LLM.
    """

    model_config = ConfigDict(frozen=True)

    pattern_count: int
    new_count: int
    continuing_count: int
    strengthening_count: int
    weakening_count: int
    resolved_count: int
    description: str


class WatchItem(BaseModel):
    """Deterministic watch item describing what evidence would clarify the interpretation.

    NEVER a trading instruction.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    description: str


class Limitation(BaseModel):
    """Explicit, deterministic limitation of the current interpretation."""

    model_config = ConfigDict(frozen=True)

    code: str
    description: str


class NarrativeResult(BaseModel):
    """Optional AI-generated narrative plus its validation outcome.

    The narrative is NEVER authoritative over deterministic D-3 fields.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    source: str = Field(description="'ai' or 'fallback'")
    validated: bool
    validation_errors: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None


class StockIntelligenceInterpretation(BaseModel):
    """Aggregate deterministic G.11-D-3 result for a stock."""

    model_config = ConfigDict(frozen=True)

    # Identity
    symbol: str
    evaluation_date: date
    comparison_date: date
    generated_at: datetime

    # Interpretation
    overall_state: OverallState
    directional_bias: DirectionalBias

    # Evidence classification
    primary_evidence: list[EvidenceItem] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    conflicting_evidence: list[EvidenceItem] = Field(default_factory=list)
    context_evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_evidence: list[EvidenceItem] = Field(default_factory=list)

    # Corrected directional convergence
    convergence: DirectionalConvergence

    # Lifecycle
    overall_lifecycle: OverallLifecycleSummary | None = None
    pattern_lifecycle_records: list[PatternLifecycleRecord] = Field(default_factory=list)

    # Guidance / transparency
    watch_items: list[WatchItem] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)

    # Narrative
    narrative: NarrativeResult | None = None
    fallback_narrative: str

    # Metadata
    interpretation_method: str
    evidence_quality: EvidenceQuality
    pit_safe: bool
    provenance: list[str] = Field(default_factory=list)
