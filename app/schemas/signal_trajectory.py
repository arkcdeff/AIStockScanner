"""Signal Trajectory Schemas — G.11-D7 Multi-Session Signal Lifecycle & Trajectory Engine.

Deterministic, frozen Pydantic contracts conforming to:
    SPEC-R2 + C-01A + C-03A + C-04A + C-02A.

All models are immutable (frozen) and enforce strict typing without speculative fields.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.what_changed_interpretation import (
    Limitation,
    NarrativeResult,
    WatchItem,
)
from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    LifecycleChangeDescriptor,
    OverallState,
)
from app.services.what_changed_synthesis_config import (
    EvidenceQuality,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)


# ===========================================================================
# 1. Enums
# ===========================================================================


class TrajectoryStatus(StrEnum):
    """Trajectory-level verdict on evidence adequacy (never a lifecycle state)."""

    OK = "OK"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SUSPENDED_DATA_GAP = "SUSPENDED_DATA_GAP"
    TERMINATED_UNOBSERVABLE = "TERMINATED_UNOBSERVABLE"
    EVALUATION_SESSION_NOT_OBSERVED = "EVALUATION_SESSION_NOT_OBSERVED"


class RecurrenceStatus(StrEnum):
    """Orthogonal recurrence status qualifier (never a lifecycle state)."""

    NONE = "NONE"
    RECURRING = "RECURRING"


class SessionObservability(StrEnum):
    """Per-session data observability state."""

    OBSERVED = "OBSERVED"
    DATA_GAP = "DATA_GAP"
    DOMAIN_DATA_GAP = "DOMAIN_DATA_GAP"
    FULL_DATA_GAP = "FULL_DATA_GAP"
    NOT_OBSERVED = "NOT_OBSERVED"


class TrajectoryQualifier(StrEnum):
    """Flags qualifying a session or trajectory point."""

    DATA_GAP = "DATA_GAP"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    RECURRING = "RECURRING"
    OPPOSITE_BIAS = "OPPOSITE_BIAS"
    CHANGED = "CHANGED"
    IDENTITY_BASE_DISPLACED = "IDENTITY_BASE_DISPLACED"
    CONTRADICTION_APPEARED = "CONTRADICTION_APPEARED"
    CONTRADICTION_PERSISTENT = "CONTRADICTION_PERSISTENT"
    CONTRADICTION_ESCALATED = "CONTRADICTION_ESCALATED"
    CONTRADICTION_RESOLVED = "CONTRADICTION_RESOLVED"
    OUT_OF_LOOKBACK = "OUT_OF_LOOKBACK"
    CORPORATE_ACTION_MASKED = "CORPORATE_ACTION_MASKED"


class ResolutionReason(StrEnum):
    """Approved resolution reason contract (SPEC-R2.1 §16.2)."""

    NONE = "NONE"
    EVIDENCE_DISSIPATION = "EVIDENCE_DISSIPATION"
    DATA_GAP_EXPIRY = "DATA_GAP_EXPIRY"
    DIRECTIONAL_FLIP = "DIRECTIONAL_FLIP"


# Backward-compatible alias during transition
TerminationReason = ResolutionReason


class PitMode(StrEnum):
    """Point-in-Time evaluation mode."""

    SESSION_GRAIN = "SESSION_GRAIN"
    STRICT_TIMESTAMP = "STRICT_TIMESTAMP"


class NonTradingDatePolicy(StrEnum):
    """Policy when evaluation_date is a non-trading calendar day."""

    REJECT = "REJECT"
    SNAP_TO_PREVIOUS_SESSION = "SNAP_TO_PREVIOUS_SESSION"


class TrajectoryLifecycleRuleSet(StrEnum):
    """Lifecycle evaluation rule set in force."""

    D3_PAIRWISE_PARITY = "D3_PAIRWISE_PARITY"
    D7_EXTENDED = "D7_EXTENDED"


# ===========================================================================
# 2. Component Models
# ===========================================================================


class SignalTrajectoryRequest(BaseModel):
    """Request specification echoed verbatim in trajectory response."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    evaluation_date: date | None = None
    lookback_sessions: int = Field(default=5, ge=2, le=20)
    recurrence_lookback_sessions: int = Field(default=5, ge=2, le=20)
    non_trading_policy: NonTradingDatePolicy = Field(
        default=NonTradingDatePolicy.SNAP_TO_PREVIOUS_SESSION
    )
    as_of: datetime | None = None
    include_resolved: bool = Field(default=True)
    include_ai_narrative: bool = Field(default=False)


class PersistenceMetrics(BaseModel):
    """Deterministic multi-session persistence tracking (§17 / §20)."""

    model_config = ConfigDict(frozen=True)

    streak_start_date: date | None = None
    initial_discovery_date: date | None = None
    last_observed_date: date | None = None
    active_streak_sessions: int = 0
    total_observed_sessions: int = 0
    eligible_sessions: int = 0
    persistence_ratio: float = 0.0
    max_streak_sessions: int = 0
    sessions_since_state_change: int = 0


class RecurrenceRecord(BaseModel):
    """Deterministic recurrence qualifier evaluation (§20)."""

    model_config = ConfigDict(frozen=True)

    status: RecurrenceStatus = RecurrenceStatus.NONE
    count_in_window: int = 0
    prior_termination_session: date | None = None
    intervening_sessions_observed: int = 0
    effective_lookback: int = 0


class DomainEvolution(BaseModel):
    """Directional domain participation migration tracking across sessions (§18 / §21)."""

    model_config = ConfigDict(frozen=True)

    ever_supporting_domains: list[str] = Field(default_factory=list)
    ever_conflicting_domains: list[str] = Field(default_factory=list)
    join_sequence: list[list[str]] = Field(default_factory=list)
    drop_sequence: list[list[str]] = Field(default_factory=list)
    current_supporting: list[str] = Field(default_factory=list)
    current_conflicting: list[str] = Field(default_factory=list)
    current_context: list[str] = Field(default_factory=list)


class ContradictionEvolution(BaseModel):
    """Deterministic tracking of conflicting evidence across sessions (§19 / §22)."""

    model_config = ConfigDict(frozen=True)

    appearance_count: int = 0
    persistence_count: int = 0
    escalation_count: int = 0
    resolution_count: int = 0
    appeared_domains: list[str] = Field(default_factory=list)
    resolved_domains: list[str] = Field(default_factory=list)
    max_conflict_count: int = 0
    current_conflict_count: int = 0


class CorporateActionEvidenceMask(BaseModel):
    """Audit metadata for Corporate Action Evidence Masking (§14, C-01A, C-02A)."""

    model_config = ConfigDict(frozen=True)

    action_applied: bool = False
    action_type: str | None = None
    ex_date: date | None = None
    available_at: datetime | None = None
    ratio_numerator: int | None = None
    ratio_denominator: int | None = None
    price_masked: bool = False
    effective_supporting_domains: list[str] = Field(default_factory=list)
    effective_conflicting_domains: list[str] = Field(default_factory=list)
    effective_convergence_ratio: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatternIdentity(BaseModel):
    """Canonical D3 pattern semantic identity components (§10, C-02A)."""

    model_config = ConfigDict(frozen=True)

    domains_involved: list[str] = Field(default_factory=list)
    metrics_involved: list[str] = Field(default_factory=list)
    relationship_type: RelationshipType
    direction: str | None = None


# ===========================================================================
# 3. Trajectory Points & Aggregates
# ===========================================================================


class PatternTrajectoryPoint(BaseModel):
    """Point-in-time state of a single pattern within a session trajectory."""

    model_config = ConfigDict(frozen=True)

    session_date: date
    lifecycle_state: PatternLifecycle
    d3_pairwise_state: PatternLifecycle | None = None
    recurrence_status: RecurrenceStatus = RecurrenceStatus.NONE
    strength: RelationshipStrength | None = None
    previous_strength: RelationshipStrength | None = None
    change_descriptor: LifecycleChangeDescriptor | None = None
    supporting_domains: list[str] = Field(default_factory=list)
    conflicting_domains: list[str] = Field(default_factory=list)
    effective_supporting_domains: list[str] = Field(default_factory=list)
    effective_conflicting_domains: list[str] = Field(default_factory=list)
    effective_convergence_ratio: float | None = None
    joined_domains: list[str] = Field(default_factory=list)
    dropped_domains: list[str] = Field(default_factory=list)
    persistent_domains: list[str] = Field(default_factory=list)
    qualifiers: list[TrajectoryQualifier] = Field(default_factory=list)
    data_status: SessionObservability = SessionObservability.OBSERVED
    data_gap: bool = False
    corporate_action_mask: CorporateActionEvidenceMask | None = None
    strengthening_reasons: list[str] = Field(default_factory=list)
    suppressed_strengthening_reasons: list[str] = Field(default_factory=list)


class PatternTrajectory(BaseModel):
    """Tier 1: Multi-session trajectory for an individual canonical pattern fingerprint."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    pattern_fingerprint: str
    identity: PatternIdentity
    current_state: PatternLifecycle
    current_recurrence_status: RecurrenceStatus = RecurrenceStatus.NONE
    resolution_reason: ResolutionReason = ResolutionReason.NONE
    resolution_session: date | None = None
    termination_reason: ResolutionReason = ResolutionReason.NONE
    termination_session: date | None = None
    points: list[PatternTrajectoryPoint] = Field(default_factory=list)
    persistence: PersistenceMetrics
    recurrence: RecurrenceRecord
    domain_evolution: DomainEvolution
    contradiction_evolution: ContradictionEvolution
    context_only: bool = False


class AggregateTrajectory(BaseModel):
    """Tier 2: Multi-session aggregate trajectory for a symbol's directional episode."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    directional_bias: DirectionalBias | None = None
    episode_start_date: date | None = None
    current_state: PatternLifecycle
    current_recurrence_status: RecurrenceStatus = RecurrenceStatus.NONE
    resolution_reason: ResolutionReason = ResolutionReason.NONE
    resolution_session: date | None = None
    termination_reason: ResolutionReason = ResolutionReason.NONE
    termination_session: date | None = None
    points: list[PatternTrajectoryPoint] = Field(default_factory=list)
    persistence: PersistenceMetrics
    recurrence: RecurrenceRecord
    domain_evolution: DomainEvolution
    contradiction_evolution: ContradictionEvolution
    evidence_base_current: list[str] = Field(default_factory=list)


class TrajectorySessionPoint(BaseModel):
    """Per-session observation snapshot across all domains in the trajectory window."""

    model_config = ConfigDict(frozen=True)

    session_date: date
    session_index: int  # 0 for evaluation session (T0), 1 for T1, etc.
    data_status: SessionObservability
    data_status_code: str
    available_domains: list[str] = Field(default_factory=list)
    missing_domains: list[str] = Field(default_factory=list)
    context_domains: list[str] = Field(default_factory=list)
    evidence_quality: EvidenceQuality | None = None
    overall_state: OverallState | None = None
    directional_bias: DirectionalBias | None = None
    directional_value: int | None = None
    directional_max: int | None = None
    convergence_ratio: float | None = None
    effective_convergence_ratio: float | None = None
    supporting_domains: list[str] = Field(default_factory=list)
    conflicting_domains: list[str] = Field(default_factory=list)
    effective_supporting_domains: list[str] = Field(default_factory=list)
    effective_conflicting_domains: list[str] = Field(default_factory=list)
    observed_pattern_fingerprints: list[str] = Field(default_factory=list)
    data_gap_pattern_fingerprints: list[str] = Field(default_factory=list)
    relationship_count: int = 0
    contradiction_event: str | None = None
    conflict_count: int = 0
    corporate_action_applied: bool = False
    corporate_action_mask: CorporateActionEvidenceMask | None = None
    session_qualifiers: list[TrajectoryQualifier] = Field(default_factory=list)
    pit_safe: bool = True


class SectorContextBlock(BaseModel):
    """Contextual sector evidence block from G.11-D6 (§23 / §27). Strictly non-mutating."""

    model_config = ConfigDict(frozen=True)

    sector_code: str
    net_directional_breadth_pct: float
    data_coverage_pct: float
    mean_convergence_ratio: float | None = None
    contradiction_flags: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    advisory_flags: list[str] = Field(default_factory=list)


# ===========================================================================
# 4. Master Canonical Response Contract
# ===========================================================================


class StockSignalTrajectoryResponse(BaseModel):
    """Canonical G.11-D7 Multi-Session Signal Trajectory response (§28.3)."""

    model_config = ConfigDict(frozen=True)

    request_echo: SignalTrajectoryRequest
    symbol: str
    requested_evaluation_date: date | None = None
    evaluation_date: date
    non_trading_policy_applied: NonTradingDatePolicy
    lookback_sessions: int
    recurrence_lookback_sessions: int
    effective_recurrence_lookback: int
    as_of_requested: datetime | None = None
    as_of_effective_session: date
    pit_mode: PitMode = PitMode.SESSION_GRAIN
    pit_safe: bool = True
    lifecycle_rule_set: TrajectoryLifecycleRuleSet = TrajectoryLifecycleRuleSet.D7_EXTENDED
    sessions: list[TrajectorySessionPoint] = Field(default_factory=list)
    current_pattern_count: int = 0
    tier2_trajectory: AggregateTrajectory | None = None
    tier1_trajectories: list[PatternTrajectory] = Field(default_factory=list)
    status: TrajectoryStatus = TrajectoryStatus.OK
    status_reasons: list[str] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)
    watch_items: list[WatchItem] = Field(default_factory=list)
    sector_context: SectorContextBlock | None = None
    narrative: NarrativeResult | None = None
    provenance: list[str] = Field(default_factory=list)
    generated_at: datetime
