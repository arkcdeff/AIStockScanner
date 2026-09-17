"""What Changed Synthesis Schema — G.11-D-2 Cross-Domain Deterministic Result Models."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.what_changed_synthesis_config import (
    EvidenceQuality,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)


class CrossDomainRelationship(BaseModel):
    """Single deterministic relationship between two or more independent facts/domains."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    evaluation_date: date
    comparison_date: date
    domains_involved: list[str]
    metrics_involved: list[str]
    relationship_type: RelationshipType
    strength: RelationshipStrength
    direction: str | None = Field(default=None, description="BULLISH, BEARISH, NEUTRAL, DIVERGENT, or None")
    evidence: list[str] = Field(default_factory=list, description="Specific factual D-1 events supporting this relationship")
    conflicting_evidence: list[str] = Field(default_factory=list, description="Specific factual D-1 events conflicting with this relationship")
    missing_evidence: list[str] = Field(default_factory=list, description="Domains or metrics unpopulated or unavailable")
    provenance: list[str] = Field(default_factory=list, description="Underlying database tables or archives")
    confidence: str = Field(default="FACT_VERIFIED", description="Data quality / confidence rating")
    reason: str = Field(description="Deterministic plain-language explanation of how the domains relate")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Contextual values and raw metrics")


class ConvergenceScore(BaseModel):
    """Deterministic count of independent confirming domains pointing to the same observed state."""

    model_config = ConfigDict(frozen=True)

    value: int = Field(description="Count of independent domains providing confirming evidence")
    scale: str = Field(default="domain_count", description="Scale type: strictly independent domain count")
    max_possible: int = Field(description="Total count of available evaluated independent domains")
    contributing_domains: list[str] = Field(default_factory=list, description="Independent domains that confirmed the move")
    conflicting_domains: list[str] = Field(default_factory=list, description="Independent domains that contradicted or diverged")
    excluded_domains: list[str] = Field(default_factory=list, description="Context-only domains (corporate, institutional) not counted as direct directional evidence")
    missing_domains: list[str] = Field(default_factory=list, description="Domains with unavailable data")
    interpretation: str = Field(description="Deterministic explanation of the evidence convergence")


class LifecycleState(BaseModel):
    """Pattern lifecycle state tracking progression across comparison windows."""

    model_config = ConfigDict(frozen=True)

    state: PatternLifecycle
    previous_window: int | None = None
    current_window: int
    description: str


class CrossDomainSynthesisResult(BaseModel):
    """Aggregate deterministic cross-domain synthesis result for a stock."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    evaluation_date: date
    comparison_date: date
    comparison_window_sessions: int
    pit_safe: bool = True
    evidence_quality: EvidenceQuality
    convergence_score: ConvergenceScore
    supporting_domains: list[str] = Field(default_factory=list)
    conflicting_domains: list[str] = Field(default_factory=list)
    missing_domains: list[str] = Field(default_factory=list)
    context_domains: list[str] = Field(default_factory=list)
    available_domains: list[str] = Field(default_factory=list)
    relationships: list[CrossDomainRelationship] = Field(default_factory=list)
    lifecycle: LifecycleState | None = None
    freshness: dict[str, Any] = Field(default_factory=dict)
    data_quality_summary: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
