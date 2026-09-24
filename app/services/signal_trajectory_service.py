"""Signal Trajectory Service — G.11-D7 Multi-Session Signal Lifecycle & Trajectory Engine.

Deterministic multi-session trajectory engine conforming to:
    SPEC-R2 + C-01A + C-03A + C-04A + C-02A.

Hard Architectural Invariants:
1. Authoritative Session Calendar: Uses ExchangeCalendarService as the sole calendar authority.
2. Chronological Forward-Chaining: Computes exactly K D2 syntheses and K D3 interpretations
   in chronological order (O(K)). Injects previous_synthesis forward.
3. Gap Non-Bridging (C-04A): A session immediately following a SESSION_DATA_GAP receives
   previous_synthesis = None. Never bridges across gaps.
4. Historical Data Insufficiency (C-03A): Insufficient historical data is never conflated with
   SESSION_DATA_GAP or market resolution (RESOLVED). Explicit limitation attached.
5. Canonical Identity Boundary (C-02A): D3 pattern_fingerprint remains strictly canonical.
   No post-mask fingerprint; no secondary corporate-action identity namespace.
6. Downstream Evidence Masking (C-01A): Corporate actions (split/bonus) are detected via PIT
   and masked downstream of D3. Price neutralization derives effective metrics without D2 re-computation.
7. Precedence Ladder: 5-level deterministic hierarchy (Gate 0 -> Level 1 -> Level 2 -> Level 3 -> Level 4 -> Level 5).
8. Orthogonal Recurrence: RECURRING is a qualifier (RecurrenceStatus), never a lifecycle state.
9. Stateless / Zero Migrations: Operates completely on existing DB entities; creates zero migrations.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.schemas.signal_trajectory import (
    AggregateTrajectory,
    ContradictionEvolution,
    CorporateActionEvidenceMask,
    DomainEvolution,
    NonTradingDatePolicy,
    PatternIdentity,
    PatternTrajectory,
    PatternTrajectoryPoint,
    PersistenceMetrics,
    PitMode,
    RecurrenceRecord,
    RecurrenceStatus,
    ResolutionReason,
    SectorContextBlock,
    SessionObservability,
    SignalTrajectoryRequest,
    StockSignalTrajectoryResponse,
    TerminationReason,
    TrajectoryLifecycleRuleSet,
    TrajectoryQualifier,
    TrajectorySessionPoint,
    TrajectoryStatus,
)
from app.schemas.what_changed_interpretation import (
    Limitation,
    StockIntelligenceInterpretation,
    WatchItem,
)
from app.schemas.what_changed_synthesis import CrossDomainSynthesisResult
from app.services.exchange_calendar_service import ExchangeCalendarService
from app.services.what_changed_interpretation_config import (
    CONTEXT_ONLY_DOMAINS,
    STRENGTH_RANK,
    DirectionalBias,
    LifecycleChangeDescriptor,
)
from app.services.what_changed_synthesis_config import (
    DOMAIN_PARTICIPATION,
    DOMAIN_PRICE,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)
from app.services.what_changed_synthesis_service import WhatChangedSynthesisService
from app.services.what_changed_interpretation_service import WhatChangedInterpretationService


# Core domains evaluated in Level 1 contradiction hierarchy
_CORE_CONTRADICTION_DOMAINS = {DOMAIN_PRICE, DOMAIN_PARTICIPATION}

# Tolerance epsilon for Level 3 convergence delta comparison
_CONVERGENCE_DELTA_TOLERANCE = 0.05


class SignalTrajectoryService:
    """Multi-session signal lifecycle & trajectory engine."""

    def __init__(
        self,
        session: AsyncSession,
        calendar: ExchangeCalendarService | None = None,
        synthesis_service: WhatChangedSynthesisService | None = None,
        interpretation_service: WhatChangedInterpretationService | None = None,
    ) -> None:
        self.session = session
        self.calendar = calendar or ExchangeCalendarService()
        self.synthesis_service = synthesis_service or WhatChangedSynthesisService(
            session, calendar=self.calendar
        )
        self.interpretation_service = (
            interpretation_service
            or WhatChangedInterpretationService(
                session, synthesis_service=self.synthesis_service
            )
        )

    # -----------------------------------------------------------------------
    # Public Entrypoint
    # -----------------------------------------------------------------------

    async def evaluate_trajectory(
        self,
        symbol: str,
        evaluation_date: date | None = None,
        lookback_sessions: int = 5,
        recurrence_lookback_sessions: int = 5,
        non_trading_policy: NonTradingDatePolicy = NonTradingDatePolicy.SNAP_TO_PREVIOUS_SESSION,
        as_of: datetime | None = None,
        include_resolved: bool = True,
        include_ai_narrative: bool = False,
    ) -> StockSignalTrajectoryResponse:
        """Evaluate deterministic multi-session signal trajectory for a single stock."""
        clean_symbol = symbol.upper().strip()
        k = max(2, min(20, lookback_sessions))
        m = max(2, min(20, recurrence_lookback_sessions))
        effective_m = min(m, k)

        request_echo = SignalTrajectoryRequest(
            symbol=clean_symbol,
            evaluation_date=evaluation_date,
            lookback_sessions=k,
            recurrence_lookback_sessions=m,
            non_trading_policy=non_trading_policy,
            as_of=as_of,
            include_resolved=include_resolved,
            include_ai_narrative=include_ai_narrative,
        )

        # 1. Resolve stock record
        stock = await self._get_stock(clean_symbol)
        if stock is None:
            raise ValueError(f"Stock symbol '{clean_symbol}' not found in active universe")

        # 2. Resolve evaluation session & check calendar policy
        eval_session, policy_applied, non_trading_err = await self._resolve_evaluation_session(
            stock_id=stock.id,
            requested_date=evaluation_date,
            policy=non_trading_policy,
            as_of=as_of,
        )

        now_utc = datetime.now(UTC)
        as_of_effective = as_of.date() if as_of else eval_session

        if non_trading_err:
            return StockSignalTrajectoryResponse(
                request_echo=request_echo,
                symbol=clean_symbol,
                requested_evaluation_date=evaluation_date,
                evaluation_date=eval_session,
                non_trading_policy_applied=policy_applied,
                lookback_sessions=k,
                recurrence_lookback_sessions=m,
                effective_recurrence_lookback=effective_m,
                as_of_requested=as_of,
                as_of_effective_session=as_of_effective,
                pit_mode=PitMode.SESSION_GRAIN,
                pit_safe=True,
                lifecycle_rule_set=TrajectoryLifecycleRuleSet.D7_EXTENDED,
                sessions=[],
                current_pattern_count=0,
                tier2_trajectory=None,
                tier1_trajectories=[],
                status=TrajectoryStatus.EVALUATION_SESSION_NOT_OBSERVED,
                status_reasons=[non_trading_err],
                limitations=[
                    Limitation(
                        code="EVALUATION_DATE_NON_TRADING",
                        description=f"Evaluation date {evaluation_date} is not a valid exchange session and policy is REJECT",
                    )
                ],
                watch_items=[],
                sector_context=None,
                narrative=None,
                provenance=["exchange_calendar"],
                generated_at=now_utc,
            )

        # 3. Determine candidate session window [T_{K-1} ... T_0] in chronological order
        target_sessions = self._build_candidate_sessions(eval_session, k)

        # 4. Determine historical availability boundary (C-03A Condition B)
        earliest_price_date = await self._get_earliest_price_date(stock.id)
        limitations: list[Limitation] = []
        status_reasons: list[str] = []
        trajectory_status = TrajectoryStatus.OK

        reconstructable_sessions = target_sessions
        if earliest_price_date is not None and target_sessions[0] < earliest_price_date:
            reconstructable_sessions = [s for s in target_sessions if s >= earliest_price_date]
            limitations.append(
                Limitation(
                    code="INSUFFICIENT_HISTORICAL_SESSION_DATA",
                    description=(
                        f"Requested lookback of {k} sessions cannot be fully reconstructed from available history "
                        f"(reconstructable sessions: {len(reconstructable_sessions)}, earliest available: {earliest_price_date})"
                    ),
                )
            )
            status_reasons.append("INSUFFICIENT_HISTORICAL_SESSION_DATA")
            trajectory_status = TrajectoryStatus.INSUFFICIENT_HISTORY

        if not reconstructable_sessions:
            return StockSignalTrajectoryResponse(
                request_echo=request_echo,
                symbol=clean_symbol,
                requested_evaluation_date=evaluation_date,
                evaluation_date=eval_session,
                non_trading_policy_applied=policy_applied,
                lookback_sessions=k,
                recurrence_lookback_sessions=m,
                effective_recurrence_lookback=effective_m,
                as_of_requested=as_of,
                as_of_effective_session=as_of_effective,
                pit_mode=PitMode.SESSION_GRAIN,
                pit_safe=True,
                lifecycle_rule_set=TrajectoryLifecycleRuleSet.D7_EXTENDED,
                sessions=[],
                current_pattern_count=0,
                tier2_trajectory=None,
                tier1_trajectories=[],
                status=TrajectoryStatus.INSUFFICIENT_HISTORY,
                status_reasons=["NO_RECONSTRUCTABLE_SESSIONS"],
                limitations=limitations,
                watch_items=[],
                sector_context=None,
                narrative=None,
                provenance=["daily_prices"],
                generated_at=now_utc,
            )

        # 5. Check session data presence (C-03A Condition A: SESSION_DATA_GAP)
        available_dates = await self._get_available_price_dates(stock.id, reconstructable_sessions)

        # 6. Chronological Forward-Chaining Pipeline (C-04A: Exactly K D2 & K D3 in O(K))
        syntheses: dict[date, CrossDomainSynthesisResult | None] = {}
        interpretations: dict[date, StockIntelligenceInterpretation | None] = {}
        data_gaps: dict[date, bool] = {}

        last_valid_synthesis: CrossDomainSynthesisResult | None = None
        immediately_preceding_was_gap = False

        for session_date in reconstructable_sessions:
            if session_date not in available_dates:
                # Session is an active exchange day but required market data is absent
                data_gaps[session_date] = True
                syntheses[session_date] = None
                interpretations[session_date] = None
                immediately_preceding_was_gap = True
                last_valid_synthesis = None
                continue

            try:
                syn = await self.synthesis_service.synthesize(
                    symbol=clean_symbol,
                    evaluation_date=session_date,
                    comparison_window=1,
                )
                syntheses[session_date] = syn
                data_gaps[session_date] = False

                # Forward Chaining Injection Rule (C-04A):
                # If first session, prev_syn = None.
                # If immediately preceding session was SESSION_DATA_GAP, prev_syn = None.
                # Otherwise, inject last valid synthesis.
                if immediately_preceding_was_gap or last_valid_synthesis is None:
                    prev_syn = None
                else:
                    prev_syn = last_valid_synthesis

                interp = await self.interpretation_service.interpret(
                    symbol=clean_symbol,
                    evaluation_date=session_date,
                    comparison_window=1,
                    current_synthesis=syn,
                    previous_synthesis=prev_syn,
                    include_ai_narrative=False,
                )
                interpretations[session_date] = interp
                last_valid_synthesis = syn
                immediately_preceding_was_gap = False

            except Exception as exc:
                logger.warning(
                    f"D7 synthesis/interpretation failed at {session_date} for {clean_symbol}: {exc}"
                )
                data_gaps[session_date] = True
                syntheses[session_date] = None
                interpretations[session_date] = None
                immediately_preceding_was_gap = True
                last_valid_synthesis = None

        # 7. Query Corporate Actions for Masking (C-01A, C-02A)
        corporate_actions = await self._get_corporate_actions(
            stock.id, reconstructable_sessions, as_of_date=as_of_effective
        )

        # 8. Build Effective Trajectory Evidence & Apply Corporate Action Mask
        masks: dict[date, CorporateActionEvidenceMask] = {}
        effective_supporting: dict[date, list[str]] = {}
        effective_conflicting: dict[date, list[str]] = {}
        effective_conv_ratios: dict[date, float | None] = {}

        for session_date in reconstructable_sessions:
            interp = interpretations.get(session_date)
            ca = corporate_actions.get(session_date)

            if interp is None:
                masks[session_date] = CorporateActionEvidenceMask(
                    action_applied=False, price_masked=False
                )
                effective_supporting[session_date] = []
                effective_conflicting[session_date] = []
                effective_conv_ratios[session_date] = None
                continue

            if ca is not None:
                # C-01A Numerical-Input Contract Derivation
                supp_no_price = [
                    d for d in interp.convergence.supporting_domains if d != DOMAIN_PRICE
                ]
                conf_no_price = [
                    d for d in interp.convergence.conflicting_domains if d != DOMAIN_PRICE
                ]
                val_no_price = len(supp_no_price)
                max_no_price = len(set(supp_no_price) | set(conf_no_price))
                eff_ratio = (
                    round(val_no_price / max_no_price, 4) if max_no_price > 0 else None
                )

                mask = CorporateActionEvidenceMask(
                    action_applied=True,
                    action_type=ca.action_type,
                    ex_date=ca.ex_date,
                    available_at=ca.available_at,
                    ratio_numerator=ca.ratio_numerator,
                    ratio_denominator=ca.ratio_denominator,
                    price_masked=True,
                    effective_supporting_domains=supp_no_price,
                    effective_conflicting_domains=conf_no_price,
                    effective_convergence_ratio=eff_ratio,
                    metadata={"source": "corporate_actions", "ca_id": ca.id},
                )
                masks[session_date] = mask
                effective_supporting[session_date] = supp_no_price
                effective_conflicting[session_date] = conf_no_price
                effective_conv_ratios[session_date] = eff_ratio
            else:
                eff_ratio = interp.convergence.convergence_ratio
                mask = CorporateActionEvidenceMask(
                    action_applied=False,
                    price_masked=False,
                    effective_supporting_domains=list(interp.convergence.supporting_domains),
                    effective_conflicting_domains=list(interp.convergence.conflicting_domains),
                    effective_convergence_ratio=eff_ratio,
                )
                masks[session_date] = mask
                effective_supporting[session_date] = list(interp.convergence.supporting_domains)
                effective_conflicting[session_date] = list(interp.convergence.conflicting_domains)
                effective_conv_ratios[session_date] = eff_ratio

        # 9. Build Session Trajectory Points (chronological order)
        session_points_chrono: list[TrajectorySessionPoint] = []
        for s_idx, session_date in enumerate(reconstructable_sessions):
            is_gap = data_gaps.get(session_date, False)
            interp = interpretations.get(session_date)
            mask = masks[session_date]

            if is_gap or interp is None:
                pt = TrajectorySessionPoint(
                    session_date=session_date,
                    session_index=len(reconstructable_sessions) - 1 - s_idx,
                    data_status=SessionObservability.DATA_GAP,
                    data_status_code="SESSION_DATA_GAP",
                    available_domains=[],
                    missing_domains=[],
                    context_domains=[],
                    evidence_quality=None,
                    overall_state=None,
                    directional_bias=None,
                    directional_value=None,
                    directional_max=None,
                    convergence_ratio=None,
                    effective_convergence_ratio=None,
                    supporting_domains=[],
                    conflicting_domains=[],
                    effective_supporting_domains=[],
                    effective_conflicting_domains=[],
                    observed_pattern_fingerprints=[],
                    data_gap_pattern_fingerprints=[],
                    relationship_count=0,
                    contradiction_event=None,
                    conflict_count=0,
                    corporate_action_applied=False,
                    corporate_action_mask=mask,
                    session_qualifiers=[TrajectoryQualifier.DATA_GAP],
                    pit_safe=True,
                )
            else:
                fingerprints = sorted(
                    {
                        rec.pattern_fingerprint
                        for rec in interp.pattern_lifecycle_records
                        if rec.pattern_fingerprint
                    }
                )
                session_qualifiers: list[TrajectoryQualifier] = []
                if mask.action_applied:
                    session_qualifiers.append(TrajectoryQualifier.CORPORATE_ACTION_MASKED)

                pt = TrajectorySessionPoint(
                    session_date=session_date,
                    session_index=len(reconstructable_sessions) - 1 - s_idx,
                    data_status=SessionObservability.OBSERVED,
                    data_status_code="CONFIRMED",
                    available_domains=sorted(
                        {ev.domain for ev in interp.primary_evidence + interp.supporting_evidence}
                    ),
                    missing_domains=sorted({ev.domain for ev in interp.missing_evidence}),
                    context_domains=sorted({ev.domain for ev in interp.context_evidence}),
                    evidence_quality=interp.evidence_quality,
                    overall_state=interp.overall_state,
                    directional_bias=interp.directional_bias,
                    directional_value=interp.convergence.directional_value,
                    directional_max=interp.convergence.directional_max_possible,
                    convergence_ratio=interp.convergence.convergence_ratio,
                    effective_convergence_ratio=effective_conv_ratios[session_date],
                    supporting_domains=list(interp.convergence.supporting_domains),
                    conflicting_domains=list(interp.convergence.conflicting_domains),
                    effective_supporting_domains=effective_supporting[session_date],
                    effective_conflicting_domains=effective_conflicting[session_date],
                    observed_pattern_fingerprints=fingerprints,
                    data_gap_pattern_fingerprints=[],
                    relationship_count=len(
                        interp.primary_evidence
                        + interp.supporting_evidence
                        + interp.conflicting_evidence
                    ),
                    contradiction_event=None,
                    conflict_count=len(effective_conflicting[session_date]),
                    corporate_action_applied=mask.action_applied,
                    corporate_action_mask=mask,
                    session_qualifiers=session_qualifiers,
                    pit_safe=interp.pit_safe,
                )
            session_points_chrono.append(pt)

        # 10. Multi-Session Pattern Lifecycle Tracking (Tier 1)
        tier1_trajectories = self._evaluate_tier1_trajectories(
            clean_symbol=clean_symbol,
            sessions=reconstructable_sessions,
            interpretations=interpretations,
            data_gaps=data_gaps,
            masks=masks,
            effective_supporting=effective_supporting,
            effective_conflicting=effective_conflicting,
            effective_conv_ratios=effective_conv_ratios,
            recurrence_lookback=effective_m,
        )

        # 11. Multi-Session Aggregate Trajectory Tracking (Tier 2 Episode)
        tier2_trajectory = self._evaluate_tier2_trajectory(
            clean_symbol=clean_symbol,
            sessions=reconstructable_sessions,
            interpretations=interpretations,
            data_gaps=data_gaps,
            masks=masks,
            effective_supporting=effective_supporting,
            effective_conflicting=effective_conflicting,
            effective_conv_ratios=effective_conv_ratios,
            recurrence_lookback=effective_m,
        )

        # 12. Evaluate Status & Watch Items
        if data_gaps.get(eval_session, False):
            trajectory_status = TrajectoryStatus.SUSPENDED_DATA_GAP
            status_reasons.append("EVALUATION_SESSION_DATA_GAP")
            limitations.append(
                Limitation(
                    code="EVALUATION_SESSION_DATA_GAP",
                    description=f"Evaluation session {eval_session} experienced a SESSION_DATA_GAP",
                )
            )

        watch_items: list[WatchItem] = []
        eval_interp = interpretations.get(eval_session)
        if eval_interp is not None:
            watch_items.extend(eval_interp.watch_items)
            limitations.extend(eval_interp.limitations)

        # Deduplicate limitations by code
        seen_lim_codes: set[str] = set()
        deduped_limitations: list[Limitation] = []
        for lim in limitations:
            if lim.code not in seen_lim_codes:
                seen_lim_codes.add(lim.code)
                deduped_limitations.append(lim)

        # 13. Presentation Filtering: include_resolved (Section 22)
        final_tier1 = tier1_trajectories
        if not include_resolved:
            final_tier1 = [
                traj
                for traj in tier1_trajectories
                if traj.current_state != PatternLifecycle.RESOLVED
            ]

        # Order sessions descending by date for response presentation
        response_sessions = sorted(
            session_points_chrono, key=lambda p: p.session_date, reverse=True
        )

        # Collect provenance
        provenance: list[str] = [
            "ExchangeCalendarService",
            "WhatChangedSynthesisService",
            "WhatChangedInterpretationService",
            "PriceAdjustmentService",
            "CorporateActionEvidenceMask",
        ]

        current_pattern_count = len(
            [t for t in tier1_trajectories if t.current_state != PatternLifecycle.RESOLVED]
        )

        return StockSignalTrajectoryResponse(
            request_echo=request_echo,
            symbol=clean_symbol,
            requested_evaluation_date=evaluation_date,
            evaluation_date=eval_session,
            non_trading_policy_applied=policy_applied,
            lookback_sessions=k,
            recurrence_lookback_sessions=m,
            effective_recurrence_lookback=effective_m,
            as_of_requested=as_of,
            as_of_effective_session=as_of_effective,
            pit_mode=PitMode.SESSION_GRAIN,
            pit_safe=all(p.pit_safe for p in session_points_chrono),
            lifecycle_rule_set=TrajectoryLifecycleRuleSet.D7_EXTENDED,
            sessions=response_sessions,
            current_pattern_count=current_pattern_count,
            tier2_trajectory=tier2_trajectory,
            tier1_trajectories=sorted(final_tier1, key=lambda t: t.pattern_fingerprint),
            status=trajectory_status,
            status_reasons=sorted(set(status_reasons)),
            limitations=sorted(deduped_limitations, key=lambda l: l.code),
            watch_items=sorted(watch_items, key=lambda w: w.code),
            sector_context=None,
            narrative=None,
            provenance=sorted(set(provenance)),
            generated_at=now_utc,
        )

    # -----------------------------------------------------------------------
    # Tier 1 Pattern Lifecycle Evaluation
    # -----------------------------------------------------------------------

    def _evaluate_tier1_trajectories(
        self,
        clean_symbol: str,
        sessions: list[date],
        interpretations: dict[date, StockIntelligenceInterpretation | None],
        data_gaps: dict[date, bool],
        masks: dict[date, CorporateActionEvidenceMask],
        effective_supporting: dict[date, list[str]],
        effective_conflicting: dict[date, list[str]],
        effective_conv_ratios: dict[date, float | None],
        recurrence_lookback: int,
    ) -> list[PatternTrajectory]:
        """Track multi-session trajectory for each canonical D3 pattern fingerprint."""
        # 1. Discover all unique canonical pattern fingerprints across sessions
        patterns_by_fp: dict[str, PatternIdentity] = {}
        patterns_context_only: dict[str, bool] = {}

        for s_date in sessions:
            interp = interpretations.get(s_date)
            if interp is None:
                continue
            for rec in interp.pattern_lifecycle_records:
                fp = rec.pattern_fingerprint
                if not fp or fp in patterns_by_fp:
                    continue
                patterns_by_fp[fp] = PatternIdentity(
                    domains_involved=list(rec.domains_involved),
                    metrics_involved=list(rec.metrics_involved),
                    relationship_type=rec.relationship_type,
                    direction=rec.direction,
                )
                is_context = all(d in CONTEXT_ONLY_DOMAINS for d in rec.domains_involved)
                patterns_context_only[fp] = is_context

        trajectories: list[PatternTrajectory] = []

        for fp, identity in patterns_by_fp.items():
            points: list[PatternTrajectoryPoint] = []
            active_streak = 0
            max_streak = 0
            total_observed = 0
            consecutive_gaps = 0
            state_streak = 0
            prev_state: PatternLifecycle | None = None
            streak_start_date: date | None = None
            initial_discovery_date: date | None = None
            last_observed_date: date | None = None

            # Contradiction tracking
            appearance_cnt = 0
            persistence_cnt = 0
            escalation_cnt = 0
            resolution_cnt = 0
            appeared_domains: list[str] = []
            resolved_domains: list[str] = []
            max_conflict = 0

            # Domain evolution
            ever_supp: set[str] = set()
            ever_conf: set[str] = set()
            join_seq: list[list[str]] = []
            drop_seq: list[list[str]] = []

            # Recurrence tracking
            recurrence_record = RecurrenceRecord(status=RecurrenceStatus.NONE)
            prior_resolved_date: date | None = None
            sessions_since_resolved = 0

            # Step through sessions chronologically
            prev_point: PatternTrajectoryPoint | None = None
            prev_eff_supp: list[str] = []
            prev_eff_conf: list[str] = []
            prev_strength: RelationshipStrength | None = None

            for session_date in sessions:
                is_gap = data_gaps.get(session_date, False)
                interp = interpretations.get(session_date)
                mask = masks[session_date]
                cur_eff_supp = effective_supporting[session_date]
                cur_eff_conf = effective_conflicting[session_date]
                cur_eff_ratio = effective_conv_ratios[session_date]

                # Find pattern record in current D3 interpretation
                rec_match = None
                if interp is not None:
                    for r in interp.pattern_lifecycle_records:
                        if r.pattern_fingerprint == fp:
                            rec_match = r
                            break

                is_active = rec_match is not None and rec_match.lifecycle_state != PatternLifecycle.RESOLVED

                # Gate 0: Continuity & Gap Check
                if is_gap or interp is None:
                    consecutive_gaps += 1
                    if consecutive_gaps <= 3:
                        # Carry-forward last confirmed state solely for reporting continuity
                        carried_state = (
                            prev_point.lifecycle_state
                            if prev_point is not None
                            else PatternLifecycle.NEW
                        )
                        pt = PatternTrajectoryPoint(
                            session_date=session_date,
                            lifecycle_state=carried_state,
                            d3_pairwise_state=None,
                            recurrence_status=RecurrenceStatus.NONE,
                            strength=prev_strength,
                            previous_strength=prev_strength,
                            change_descriptor=None,
                            supporting_domains=[],
                            conflicting_domains=[],
                            effective_supporting_domains=[],
                            effective_conflicting_domains=[],
                            effective_convergence_ratio=None,
                            joined_domains=[],
                            dropped_domains=[],
                            persistent_domains=[],
                            qualifiers=[TrajectoryQualifier.DATA_GAP],
                            data_status=SessionObservability.DATA_GAP,
                            data_gap=True,
                            corporate_action_mask=mask,
                        )
                    else:
                        # G > 3: Terminate episode as RESOLVED (DATA_GAP_EXPIRY)
                        pt = PatternTrajectoryPoint(
                            session_date=session_date,
                            lifecycle_state=PatternLifecycle.RESOLVED,
                            d3_pairwise_state=None,
                            recurrence_status=RecurrenceStatus.NONE,
                            strength=None,
                            previous_strength=prev_strength,
                            change_descriptor=None,
                            supporting_domains=[],
                            conflicting_domains=[],
                            effective_supporting_domains=[],
                            effective_conflicting_domains=[],
                            effective_convergence_ratio=None,
                            joined_domains=[],
                            dropped_domains=[],
                            persistent_domains=[],
                            qualifiers=[TrajectoryQualifier.DATA_GAP],
                            data_status=SessionObservability.DATA_GAP,
                            data_gap=True,
                            corporate_action_mask=mask,
                        )
                        active_streak = 0
                        prior_resolved_date = session_date
                        sessions_since_resolved = 0

                    points.append(pt)
                    prev_point = pt
                    continue

                # Session is valid (not a gap)
                consecutive_gaps = 0

                if not is_active:
                    # Pattern absent in confirmed session -> RESOLVED (EVIDENCE_ABSENT)
                    if prev_point is not None and prev_point.lifecycle_state != PatternLifecycle.RESOLVED:
                        pt_state = PatternLifecycle.RESOLVED
                        prior_resolved_date = session_date
                        sessions_since_resolved = 0
                    else:
                        pt_state = PatternLifecycle.RESOLVED

                    pt = PatternTrajectoryPoint(
                        session_date=session_date,
                        lifecycle_state=pt_state,
                        d3_pairwise_state=PatternLifecycle.RESOLVED,
                        recurrence_status=RecurrenceStatus.NONE,
                        strength=None,
                        previous_strength=prev_strength,
                        change_descriptor=None,
                        supporting_domains=[],
                        conflicting_domains=[],
                        effective_supporting_domains=[],
                        effective_conflicting_domains=[],
                        effective_convergence_ratio=None,
                        joined_domains=[],
                        dropped_domains=[],
                        persistent_domains=[],
                        qualifiers=[],
                        data_status=SessionObservability.OBSERVED,
                        data_gap=False,
                        corporate_action_mask=mask,
                    )
                    active_streak = 0
                    points.append(pt)
                    prev_point = pt
                    prev_eff_supp = []
                    prev_eff_conf = []
                    prev_strength = None
                    if prior_resolved_date is not None:
                        sessions_since_resolved += 1
                    continue

                # Pattern IS active in confirmed session
                total_observed += 1
                last_observed_date = session_date
                if initial_discovery_date is None:
                    initial_discovery_date = session_date

                cur_strength = rec_match.current_strength or RelationshipStrength.NORMAL
                joined = [d for d in cur_eff_supp if d not in prev_eff_supp]
                dropped = [d for d in prev_eff_supp if d not in cur_eff_supp]
                persistent = [d for d in cur_eff_supp if d in prev_eff_supp]

                ever_supp.update(cur_eff_supp)
                ever_conf.update(cur_eff_conf)
                if joined:
                    join_seq.append(joined)
                if dropped:
                    drop_seq.append(dropped)

                # Contradiction tracking
                new_conflicts = [d for d in cur_eff_conf if d not in prev_eff_conf]
                cleared_conflicts = [d for d in prev_eff_conf if d not in cur_eff_conf]
                if new_conflicts:
                    appearance_cnt += 1
                    appeared_domains.extend(new_conflicts)
                if cleared_conflicts:
                    resolution_cnt += 1
                    resolved_domains.extend(cleared_conflicts)
                if len(cur_eff_conf) > len(prev_eff_conf):
                    escalation_cnt += 1
                elif len(cur_eff_conf) == len(prev_eff_conf) and cur_eff_conf:
                    persistence_cnt += 1
                max_conflict = max(max_conflict, len(cur_eff_conf))

                # Determine Lifecycle State via 5-Level Precedence Ladder
                if prev_point is None or prev_point.lifecycle_state == PatternLifecycle.RESOLVED:
                    # Pattern is NEW
                    pt_state = PatternLifecycle.NEW
                    active_streak = 1
                    streak_start_date = session_date

                    # Check Recurrence (§14 / §20)
                    if (
                        prior_resolved_date is not None
                        and sessions_since_resolved <= recurrence_lookback
                    ):
                        rec_status = RecurrenceStatus.RECURRING
                        recurrence_record = RecurrenceRecord(
                            status=RecurrenceStatus.RECURRING,
                            count_in_window=recurrence_record.count_in_window + 1,
                            prior_termination_session=prior_resolved_date,
                            intervening_sessions_observed=sessions_since_resolved,
                            effective_lookback=recurrence_lookback,
                        )
                    else:
                        rec_status = RecurrenceStatus.NONE
                else:
                    # Pattern was active at T-1 and active at T -> run Precedence Ladder
                    active_streak += 1
                    rec_status = RecurrenceStatus.NONE

                    pt_state = self._evaluate_precedence_ladder(
                        prev_strength=prev_strength or RelationshipStrength.NORMAL,
                        cur_strength=cur_strength,
                        prev_conflicting=prev_eff_conf,
                        cur_conflicting=cur_eff_conf,
                        prev_supporting=prev_eff_supp,
                        cur_supporting=cur_eff_supp,
                        prev_conv_ratio=prev_point.effective_convergence_ratio,
                        cur_conv_ratio=cur_eff_ratio,
                    )

                max_streak = max(max_streak, active_streak)

                if prev_state == pt_state:
                    state_streak += 1
                else:
                    state_streak = 1
                    prev_state = pt_state

                qualifiers: list[TrajectoryQualifier] = []
                if rec_status == RecurrenceStatus.RECURRING:
                    qualifiers.append(TrajectoryQualifier.RECURRING)
                if mask.action_applied:
                    qualifiers.append(TrajectoryQualifier.CORPORATE_ACTION_MASKED)

                pt = PatternTrajectoryPoint(
                    session_date=session_date,
                    lifecycle_state=pt_state,
                    d3_pairwise_state=rec_match.lifecycle_state,
                    recurrence_status=rec_status,
                    strength=cur_strength,
                    previous_strength=prev_strength,
                    change_descriptor=rec_match.change_descriptor,
                    supporting_domains=list(interp.convergence.supporting_domains),
                    conflicting_domains=list(interp.convergence.conflicting_domains),
                    effective_supporting_domains=cur_eff_supp,
                    effective_conflicting_domains=cur_eff_conf,
                    effective_convergence_ratio=cur_eff_ratio,
                    joined_domains=joined,
                    dropped_domains=dropped,
                    persistent_domains=persistent,
                    qualifiers=qualifiers,
                    data_status=SessionObservability.OBSERVED,
                    data_gap=False,
                    corporate_action_mask=mask,
                )
                points.append(pt)
                prev_point = pt
                prev_eff_supp = cur_eff_supp
                prev_eff_conf = cur_eff_conf
                prev_strength = cur_strength

            # Calculate persistence metrics across window
            eligible_sessions = len(sessions) - sum(1 for d in sessions if data_gaps.get(d, False))
            pers_ratio = (
                round(total_observed / eligible_sessions, 4) if eligible_sessions > 0 else 0.0
            )

            current_pt = points[-1] if points else None
            curr_state = current_pt.lifecycle_state if current_pt else PatternLifecycle.RESOLVED
            curr_rec = current_pt.recurrence_status if current_pt else RecurrenceStatus.NONE

            res_reason = ResolutionReason.NONE
            res_session: date | None = None
            if curr_state == PatternLifecycle.RESOLVED:
                res_reason = (
                    ResolutionReason.DATA_GAP_EXPIRY
                    if current_pt and current_pt.data_gap
                    else ResolutionReason.EVIDENCE_DISSIPATION
                )
                res_session = current_pt.session_date if current_pt else None

            # Persistence object
            pers_metrics = PersistenceMetrics(
                streak_start_date=streak_start_date,
                initial_discovery_date=initial_discovery_date,
                last_observed_date=last_observed_date,
                active_streak_sessions=active_streak,
                total_observed_sessions=total_observed,
                eligible_sessions=eligible_sessions,
                persistence_ratio=pers_ratio,
                max_streak_sessions=max_streak,
                sessions_since_state_change=state_streak,
            )

            # Domain evolution object
            last_valid_interp = None
            for s in reversed(sessions):
                if interpretations.get(s) is not None:
                    last_valid_interp = interpretations[s]
                    break

            dom_evo = DomainEvolution(
                ever_supporting_domains=sorted(ever_supp),
                ever_conflicting_domains=sorted(ever_conf),
                join_sequence=join_seq,
                drop_sequence=drop_seq,
                current_supporting=effective_supporting.get(sessions[-1], []),
                current_conflicting=effective_conflicting.get(sessions[-1], []),
                current_context=sorted(
                    {ev.domain for ev in last_valid_interp.context_evidence}
                )
                if last_valid_interp
                else [],
            )

            # Contradiction evolution object
            contra_evo = ContradictionEvolution(
                appearance_count=appearance_cnt,
                persistence_count=persistence_cnt,
                escalation_count=escalation_cnt,
                resolution_count=resolution_cnt,
                appeared_domains=sorted(set(appeared_domains)),
                resolved_domains=sorted(set(resolved_domains)),
                max_conflict_count=max_conflict,
                current_conflict_count=len(effective_conflicting.get(sessions[-1], [])),
            )

            trajectories.append(
                PatternTrajectory(
                    symbol=clean_symbol,
                    pattern_fingerprint=fp,
                    identity=identity,
                    current_state=curr_state,
                    current_recurrence_status=curr_rec,
                    resolution_reason=res_reason,
                    resolution_session=res_session,
                    termination_reason=res_reason,
                    termination_session=res_session,
                    points=points,
                    persistence=pers_metrics,
                    recurrence=recurrence_record,
                    domain_evolution=dom_evo,
                    contradiction_evolution=contra_evo,
                    context_only=patterns_context_only.get(fp, False),
                )
            )

        return trajectories

    # -----------------------------------------------------------------------
    # Tier 2 Aggregate Trajectory Evaluation
    # -----------------------------------------------------------------------

    def _evaluate_tier2_trajectory(
        self,
        clean_symbol: str,
        sessions: list[date],
        interpretations: dict[date, StockIntelligenceInterpretation | None],
        data_gaps: dict[date, bool],
        masks: dict[date, CorporateActionEvidenceMask],
        effective_supporting: dict[date, list[str]],
        effective_conflicting: dict[date, list[str]],
        effective_conv_ratios: dict[date, float | None],
        recurrence_lookback: int,
    ) -> AggregateTrajectory | None:
        """Track multi-session aggregate trajectory for the stock's directional episode."""
        last_interp = interpretations.get(sessions[-1])
        if last_interp is None and all(interpretations.get(s) is None for s in sessions):
            return None

        # Determine directional bias from latest observed session
        curr_bias: DirectionalBias | None = None
        for s in reversed(sessions):
            inp = interpretations.get(s)
            if inp is not None:
                curr_bias = inp.directional_bias
                break

        points: list[PatternTrajectoryPoint] = []
        active_streak = 0
        max_streak = 0
        total_observed = 0
        consecutive_gaps = 0
        state_streak = 0
        prev_state: PatternLifecycle | None = None
        streak_start_date: date | None = None
        initial_discovery_date: date | None = None
        last_observed_date: date | None = None

        appearance_cnt = 0
        persistence_cnt = 0
        escalation_cnt = 0
        resolution_cnt = 0
        appeared_domains: list[str] = []
        resolved_domains: list[str] = []
        max_conflict = 0

        ever_supp: set[str] = set()
        ever_conf: set[str] = set()
        join_seq: list[list[str]] = []
        drop_seq: list[list[str]] = []

        recurrence_record = RecurrenceRecord(status=RecurrenceStatus.NONE)
        prior_resolved_date: date | None = None
        sessions_since_resolved = 0
        last_resolution_reason = ResolutionReason.NONE

        prev_point: PatternTrajectoryPoint | None = None
        prev_eff_supp: list[str] = []
        prev_eff_conf: list[str] = []
        prev_strength: RelationshipStrength | None = None
        prev_bias: DirectionalBias | None = None

        for session_date in sessions:
            is_gap = data_gaps.get(session_date, False)
            interp = interpretations.get(session_date)
            mask = masks[session_date]
            cur_eff_supp = effective_supporting[session_date]
            cur_eff_conf = effective_conflicting[session_date]
            cur_eff_ratio = effective_conv_ratios[session_date]

            if is_gap or interp is None:
                consecutive_gaps += 1
                if consecutive_gaps <= 3:
                    carried_state = (
                        prev_point.lifecycle_state
                        if prev_point is not None
                        else PatternLifecycle.NEW
                    )
                    pt = PatternTrajectoryPoint(
                        session_date=session_date,
                        lifecycle_state=carried_state,
                        d3_pairwise_state=None,
                        recurrence_status=RecurrenceStatus.NONE,
                        strength=prev_strength,
                        previous_strength=prev_strength,
                        change_descriptor=None,
                        supporting_domains=[],
                        conflicting_domains=[],
                        effective_supporting_domains=[],
                        effective_conflicting_domains=[],
                        effective_convergence_ratio=None,
                        joined_domains=[],
                        dropped_domains=[],
                        persistent_domains=[],
                        qualifiers=[TrajectoryQualifier.DATA_GAP],
                        data_status=SessionObservability.DATA_GAP,
                        data_gap=True,
                        corporate_action_mask=mask,
                    )
                else:
                    pt = PatternTrajectoryPoint(
                        session_date=session_date,
                        lifecycle_state=PatternLifecycle.RESOLVED,
                        d3_pairwise_state=None,
                        recurrence_status=RecurrenceStatus.NONE,
                        strength=None,
                        previous_strength=prev_strength,
                        change_descriptor=None,
                        supporting_domains=[],
                        conflicting_domains=[],
                        effective_supporting_domains=[],
                        effective_conflicting_domains=[],
                        effective_convergence_ratio=None,
                        joined_domains=[],
                        dropped_domains=[],
                        persistent_domains=[],
                        qualifiers=[TrajectoryQualifier.DATA_GAP],
                        data_status=SessionObservability.DATA_GAP,
                        data_gap=True,
                        corporate_action_mask=mask,
                    )
                    active_streak = 0
                    prior_resolved_date = session_date
                    sessions_since_resolved = 0
                    last_resolution_reason = ResolutionReason.DATA_GAP_EXPIRY

                points.append(pt)
                prev_point = pt
                continue

            consecutive_gaps = 0
            total_observed += 1
            last_observed_date = session_date
            if initial_discovery_date is None:
                initial_discovery_date = session_date

            cur_strength = RelationshipStrength.NORMAL
            if interp.primary_evidence:
                cur_strength = interp.primary_evidence[0].strength

            joined = [d for d in cur_eff_supp if d not in prev_eff_supp]
            dropped = [d for d in prev_eff_supp if d not in cur_eff_supp]
            persistent = [d for d in cur_eff_supp if d in prev_eff_supp]

            ever_supp.update(cur_eff_supp)
            ever_conf.update(cur_eff_conf)
            if joined:
                join_seq.append(joined)
            if dropped:
                drop_seq.append(dropped)

            new_conflicts = [d for d in cur_eff_conf if d not in prev_eff_conf]
            cleared_conflicts = [d for d in prev_eff_conf if d not in cur_eff_conf]
            if new_conflicts:
                appearance_cnt += 1
                appeared_domains.extend(new_conflicts)
            if cleared_conflicts:
                resolution_cnt += 1
                resolved_domains.extend(cleared_conflicts)
            if len(cur_eff_conf) > len(prev_eff_conf):
                escalation_cnt += 1
            elif len(cur_eff_conf) == len(prev_eff_conf) and cur_eff_conf:
                persistence_cnt += 1
            max_conflict = max(max_conflict, len(cur_eff_conf))

            # Directional flip check: if directional bias changes, old episode resolves
            if (
                prev_point is not None
                and prev_bias is not None
                and interp.directional_bias is not None
                and interp.directional_bias != prev_bias
                and {interp.directional_bias, prev_bias} <= {DirectionalBias.BULLISH_EVIDENCE, DirectionalBias.BEARISH_EVIDENCE}
            ):
                pt_state = PatternLifecycle.RESOLVED
                active_streak = 0
                prior_resolved_date = session_date
                sessions_since_resolved = 0
                last_resolution_reason = ResolutionReason.DIRECTIONAL_FLIP
            elif prev_point is None or prev_point.lifecycle_state == PatternLifecycle.RESOLVED:
                pt_state = PatternLifecycle.NEW
                last_resolution_reason = ResolutionReason.NONE
                active_streak = 1
                streak_start_date = session_date

                if (
                    prior_resolved_date is not None
                    and sessions_since_resolved <= recurrence_lookback
                ):
                    rec_status = RecurrenceStatus.RECURRING
                    recurrence_record = RecurrenceRecord(
                        status=RecurrenceStatus.RECURRING,
                        count_in_window=recurrence_record.count_in_window + 1,
                        prior_termination_session=prior_resolved_date,
                        intervening_sessions_observed=sessions_since_resolved,
                        effective_lookback=recurrence_lookback,
                    )
                else:
                    rec_status = RecurrenceStatus.NONE
            else:
                active_streak += 1
                rec_status = RecurrenceStatus.NONE
                pt_state = self._evaluate_precedence_ladder(
                    prev_strength=prev_strength or RelationshipStrength.NORMAL,
                    cur_strength=cur_strength,
                    prev_conflicting=prev_eff_conf,
                    cur_conflicting=cur_eff_conf,
                    prev_supporting=prev_eff_supp,
                    cur_supporting=cur_eff_supp,
                    prev_conv_ratio=prev_point.effective_convergence_ratio,
                    cur_conv_ratio=cur_eff_ratio,
                )

            max_streak = max(max_streak, active_streak)

            if prev_state == pt_state:
                state_streak += 1
            else:
                state_streak = 1
                prev_state = pt_state

            qualifiers = []
            if mask.action_applied:
                qualifiers.append(TrajectoryQualifier.CORPORATE_ACTION_MASKED)

            pt = PatternTrajectoryPoint(
                session_date=session_date,
                lifecycle_state=pt_state,
                d3_pairwise_state=None,
                recurrence_status=rec_status,
                strength=cur_strength,
                previous_strength=prev_strength,
                change_descriptor=None,
                supporting_domains=list(interp.convergence.supporting_domains),
                conflicting_domains=list(interp.convergence.conflicting_domains),
                effective_supporting_domains=cur_eff_supp,
                effective_conflicting_domains=cur_eff_conf,
                effective_convergence_ratio=cur_eff_ratio,
                joined_domains=joined,
                dropped_domains=dropped,
                persistent_domains=persistent,
                qualifiers=qualifiers,
                data_status=SessionObservability.OBSERVED,
                data_gap=False,
                corporate_action_mask=mask,
            )
            points.append(pt)
            prev_point = pt
            prev_eff_supp = cur_eff_supp
            prev_eff_conf = cur_eff_conf
            prev_strength = cur_strength
            prev_bias = interp.directional_bias

        eligible_sessions = len(sessions) - sum(1 for d in sessions if data_gaps.get(d, False))
        pers_ratio = (
            round(total_observed / eligible_sessions, 4) if eligible_sessions > 0 else 0.0
        )

        current_pt = points[-1] if points else None
        curr_state = current_pt.lifecycle_state if current_pt else PatternLifecycle.RESOLVED
        curr_rec = current_pt.recurrence_status if current_pt else RecurrenceStatus.NONE

        pers_metrics = PersistenceMetrics(
            streak_start_date=streak_start_date,
            initial_discovery_date=initial_discovery_date,
            last_observed_date=last_observed_date,
            active_streak_sessions=active_streak,
            total_observed_sessions=total_observed,
            eligible_sessions=eligible_sessions,
            persistence_ratio=pers_ratio,
            max_streak_sessions=max_streak,
            sessions_since_state_change=state_streak,
        )

        dom_evo = DomainEvolution(
            ever_supporting_domains=sorted(ever_supp),
            ever_conflicting_domains=sorted(ever_conf),
            join_sequence=join_seq,
            drop_sequence=drop_seq,
            current_supporting=effective_supporting.get(sessions[-1], []),
            current_conflicting=effective_conflicting.get(sessions[-1], []),
            current_context=[],
        )

        contra_evo = ContradictionEvolution(
            appearance_count=appearance_cnt,
            persistence_count=persistence_cnt,
            escalation_count=escalation_cnt,
            resolution_count=resolution_cnt,
            appeared_domains=sorted(set(appeared_domains)),
            resolved_domains=sorted(set(resolved_domains)),
            max_conflict_count=max_conflict,
            current_conflict_count=len(effective_conflicting.get(sessions[-1], [])),
        )

        res_reason = ResolutionReason.NONE
        res_session: date | None = None
        if curr_state == PatternLifecycle.RESOLVED:
            res_session = current_pt.session_date if current_pt else None
            if current_pt and current_pt.data_gap:
                res_reason = ResolutionReason.DATA_GAP_EXPIRY
            elif last_resolution_reason != ResolutionReason.NONE:
                res_reason = last_resolution_reason
            else:
                res_reason = ResolutionReason.EVIDENCE_DISSIPATION

        return AggregateTrajectory(
            symbol=clean_symbol,
            directional_bias=curr_bias,
            episode_start_date=streak_start_date,
            current_state=curr_state,
            current_recurrence_status=curr_rec,
            resolution_reason=res_reason,
            resolution_session=res_session,
            termination_reason=res_reason,
            termination_session=res_session,
            points=points,
            persistence=pers_metrics,
            recurrence=recurrence_record,
            domain_evolution=dom_evo,
            contradiction_evolution=contra_evo,
            evidence_base_current=effective_supporting.get(sessions[-1], []),
        )

    # -----------------------------------------------------------------------
    # 5-Level Deterministic Precedence Ladder (§13 / SPEC-R2)
    # -----------------------------------------------------------------------

    def _evaluate_precedence_ladder(
        self,
        prev_strength: RelationshipStrength,
        cur_strength: RelationshipStrength,
        prev_conflicting: list[str],
        cur_conflicting: list[str],
        prev_supporting: list[str],
        cur_supporting: list[str],
        prev_conv_ratio: float | None,
        cur_conv_ratio: float | None,
    ) -> PatternLifecycle:
        """Evaluate deterministic 5-level precedence ladder for active signals."""
        # Level 1: Core Contradiction Escalation
        new_core_conflicts = [
            d
            for d in cur_conflicting
            if d in _CORE_CONTRADICTION_DOMAINS and d not in prev_conflicting
        ]
        if new_core_conflicts:
            return PatternLifecycle.WEAKENING

        # Level 2: Relationship Strength Rank (MAJOR=3, SIGNIFICANT=2, NORMAL=1)
        prev_rank = STRENGTH_RANK.get(prev_strength, 1)
        cur_rank = STRENGTH_RANK.get(cur_strength, 1)
        if cur_rank > prev_rank:
            return PatternLifecycle.STRENGTHENING
        if cur_rank < prev_rank:
            return PatternLifecycle.WEAKENING

        # Level 3: Directional Convergence Ratio Delta (Tolerance epsilon = 0.05)
        # Note: Bypassed if either ratio is None (C-01A Section 5)
        if cur_conv_ratio is not None and prev_conv_ratio is not None:
            delta = cur_conv_ratio - prev_conv_ratio
            if delta > _CONVERGENCE_DELTA_TOLERANCE:
                return PatternLifecycle.STRENGTHENING
            if delta < -_CONVERGENCE_DELTA_TOLERANCE:
                return PatternLifecycle.WEAKENING

        # Level 4: Confirming Directional Domain Count
        cur_supp_cnt = len(cur_supporting)
        prev_supp_cnt = len(prev_supporting)
        if cur_supp_cnt > prev_supp_cnt:
            return PatternLifecycle.STRENGTHENING
        if cur_supp_cnt < prev_supp_cnt:
            return PatternLifecycle.WEAKENING

        # Level 5: Parity
        return PatternLifecycle.CONTINUING

    # -----------------------------------------------------------------------
    # Helper Queries & Calendar Utilities
    # -----------------------------------------------------------------------

    async def _get_stock(self, symbol: str) -> Stock | None:
        stmt = select(Stock).where(Stock.symbol == symbol, Stock.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _resolve_evaluation_session(
        self,
        stock_id: int,
        requested_date: date | None,
        policy: NonTradingDatePolicy,
        as_of: datetime | None,
    ) -> tuple[date, NonTradingDatePolicy, str | None]:
        """Resolve effective evaluation session date obeying calendar and policy."""
        if requested_date is None:
            stmt = (
                select(DailyPrice.trade_date)
                .where(DailyPrice.stock_id == stock_id)
                .order_by(DailyPrice.trade_date.desc())
                .limit(1)
            )
            if as_of is not None:
                stmt = stmt.where(DailyPrice.trade_date <= as_of.date())
            res = await self.session.execute(stmt)
            latest = res.scalar_one_or_none()
            if latest is None:
                raise ValueError(f"No price records found for stock_id {stock_id}")
            return latest, policy, None

        if self.calendar.is_exchange_trading_day(requested_date):
            return requested_date, policy, None

        # Requested date is non-trading
        if policy == NonTradingDatePolicy.REJECT:
            return requested_date, policy, "NON_TRADING_DATE_REJECTED"

        # SNAP_TO_PREVIOUS_SESSION
        snapped = self.calendar.previous_exchange_session(requested_date)
        return snapped, policy, None

    def _build_candidate_sessions(self, eval_session: date, k: int) -> list[date]:
        """Build exactly k exchange trading sessions ending at eval_session in chronological order."""
        sessions = [eval_session]
        cur = eval_session
        for _ in range(k - 1):
            cur = self.calendar.previous_exchange_session(cur)
            sessions.append(cur)
        return sorted(sessions)

    async def _get_earliest_price_date(self, stock_id: int) -> date | None:
        stmt = (
            select(DailyPrice.trade_date)
            .where(DailyPrice.stock_id == stock_id)
            .order_by(DailyPrice.trade_date.asc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def _get_available_price_dates(
        self, stock_id: int, candidate_dates: list[date]
    ) -> set[date]:
        stmt = select(DailyPrice.trade_date).where(
            DailyPrice.stock_id == stock_id,
            DailyPrice.trade_date.in_(candidate_dates),
        )
        res = await self.session.execute(stmt)
        return set(res.scalars().all())

    async def _get_corporate_actions(
        self, stock_id: int, candidate_dates: list[date], as_of_date: date | None = None
    ) -> dict[date, CorporateAction]:
        """Query qualifying split/bonus corporate actions for the candidate sessions."""
        stmt = select(CorporateAction).where(
            CorporateAction.stock_id == stock_id,
            CorporateAction.ex_date.in_(candidate_dates),
            CorporateAction.action_type.in_(["split", "bonus"]),
        )
        res = await self.session.execute(stmt)
        actions = res.scalars().all()

        action_by_date: dict[date, CorporateAction] = {}
        for ca in actions:
            # PIT filter: available_at must be knowable on or before session ex_date (and <= as_of_date)
            if ca.available_at is not None:
                ca_knowable_date = ca.available_at.date()
                if ca_knowable_date > ca.ex_date:
                    continue
                if as_of_date is not None and ca_knowable_date > as_of_date:
                    continue
            action_by_date[ca.ex_date] = ca

        return action_by_date
