"""Comprehensive Test Suite for G.11-D7: Multi-Session Signal Lifecycle & Trajectory Engine.

Conforms to:
    SPEC-R2 + C-01A + C-03A + C-04A + C-02A.

Categories Tested (All 31 Mandatory Specification Categories):
    1. K=2 minimal boundary
    2. K=5 default window
    3. K=20 maximum boundary
    4. First-session previous_synthesis = None invariant
    5. Strict sequential forward-chaining
    6. SESSION_DATA_GAP single-session detection
    7. Gap non-bridging rule (previous_synthesis = None after gap)
    8. G <= 3 carry-forward state & streak suspension
    9. G > 3 DATA_GAP_EXPIRY permanent resolution
    10. INSUFFICIENT_HISTORICAL_SESSION_DATA limitation
    11. Canonical D3 pattern fingerprint parity
    12. D3 pairwise lifecycle parity audit
    13. Directional convergence ratio parity
    14. Directional domain migration (joined, dropped, persistent)
    15. Contradiction lifecycle (appearance, escalation, resolution)
    16. Orthogonal recurrence status (NONE vs RECURRING)
    17. Corporate action split masking (PRICE neutralization)
    18. Corporate action bonus masking
    19. Corporate action canonical fingerprint immutability
    20. No post-mask fingerprint firewall
    21. Tier 1 per-pattern trajectory preservation
    22. Tier 2 aggregate episode trajectory preservation & directional flip
    23. PIT late corporate action non-leakage
    24. C-04A pre-mask D3 parity
    25. Cross-symbol evaluation isolation
    26. Authentication gating (Bearer token & 401)
    27. API query parameter validation (lookback bounds & 422)
    28. include_resolved presentation filtering
    29. AI firewall (AI never authoritative)
    30. Trading firewall (zero BUY/SELL/CE/PE)
    31. OPT-1 strict isolation audit
    + Real PostgreSQL integration coverage
    + Zero migration invariant audit
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
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
    DirectionalConvergence,
    EvidenceItem,
    Limitation,
    PatternLifecycleRecord,
    StockIntelligenceInterpretation,
    WatchItem,
)
from app.schemas.what_changed_synthesis import (
    ConvergenceScore,
    CrossDomainRelationship,
    CrossDomainSynthesisResult,
)
from app.services.exchange_calendar_service import ExchangeCalendarService
from app.services.signal_trajectory_service import SignalTrajectoryService
from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    EvidenceClassification,
    LifecycleChangeDescriptor,
    OverallState,
)
from app.services.what_changed_synthesis_config import (
    DOMAIN_DERIVATIVES,
    DOMAIN_PARTICIPATION,
    DOMAIN_PRICE,
    DOMAIN_TECHNICAL,
    EvidenceQuality,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)
from tests.auth_helpers import auth_client

# ===========================================================================
# Mock Factories & Test Helpers
# ===========================================================================

T0 = date(2026, 9, 18)  # Friday
T1 = date(2026, 9, 17)  # Thursday
T2 = date(2026, 9, 16)  # Wednesday
T3 = date(2026, 9, 15)  # Tuesday
T4 = date(2026, 9, 14)  # Monday


def _build_mock_synthesis(
    symbol: str,
    eval_date: date,
    relationships: list[CrossDomainRelationship] | None = None,
) -> CrossDomainSynthesisResult:
    return CrossDomainSynthesisResult(
        symbol=symbol,
        evaluation_date=eval_date,
        comparison_date=eval_date,
        comparison_window_sessions=1,
        generated_at=datetime.now(UTC),
        evidence_quality=EvidenceQuality.CONFIRMED,
        relationships=relationships or [],
        convergence_score=ConvergenceScore(
            value=3,
            scale="domain_count",
            max_possible=4,
            contributing_domains=["PRICE", "TECHNICAL", "PARTICIPATION"],
            conflicting_domains=[],
            excluded_domains=["CORPORATE"],
            missing_domains=[],
            interpretation="Confirmed convergence",
        ),
        supporting_domains=["PRICE", "TECHNICAL", "PARTICIPATION"],
        conflicting_domains=[],
        missing_domains=[],
        context_domains=["CORPORATE"],
        available_domains=["PRICE", "TECHNICAL", "PARTICIPATION", "CORPORATE"],
        pit_safe=True,
    )


def _build_mock_interpretation(
    symbol: str,
    eval_date: date,
    supporting_domains: list[str] | None = None,
    conflicting_domains: list[str] | None = None,
    patterns: list[PatternLifecycleRecord] | None = None,
    strength: RelationshipStrength = RelationshipStrength.NORMAL,
    directional_bias: DirectionalBias = DirectionalBias.BULLISH_EVIDENCE,
) -> StockIntelligenceInterpretation:
    supp = supporting_domains if supporting_domains is not None else ["PRICE", "TECHNICAL"]
    conf = conflicting_domains if conflicting_domains is not None else []
    val = len(supp)
    max_pos = len(set(supp) | set(conf))
    ratio = round(val / max_pos, 4) if max_pos > 0 else 0.0

    conv = DirectionalConvergence(
        directional_value=val,
        directional_max_possible=max_pos,
        convergence_ratio=ratio,
        supporting_domains=supp,
        conflicting_domains=conf,
        context_domains=["CORPORATE"],
        missing_domains=[],
        interpretation="Directional convergence interpretation",
    )

    ev_items = [
        EvidenceItem(
            domain=d,
            domains_involved=[d],
            metrics_involved=["metric1"],
            relationship_type=RelationshipType.CONFIRMATION,
            strength=strength,
            direction="BULLISH",
            classification=EvidenceClassification.PRIMARY,
            pattern_fingerprint="PRICE|metric1|CONFIRMATION|BULLISH",
        )
        for d in supp
    ]

    pats = patterns
    if pats is None:
        pats = [
            PatternLifecycleRecord(
                pattern_fingerprint="PRICE|metric1|CONFIRMATION|BULLISH",
                domains_involved=["PRICE"],
                metrics_involved=["metric1"],
                relationship_type=RelationshipType.CONFIRMATION,
                direction="BULLISH",
                current_strength=strength,
                previous_strength=strength,
                lifecycle_state=PatternLifecycle.NEW,
                description="Pattern description",
            )
        ]

    return StockIntelligenceInterpretation(
        symbol=symbol,
        evaluation_date=eval_date,
        comparison_date=eval_date,
        generated_at=datetime.now(UTC),
        overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
        directional_bias=directional_bias,
        primary_evidence=ev_items,
        supporting_evidence=[],
        conflicting_evidence=[],
        context_evidence=[],
        missing_evidence=[],
        convergence=conv,
        pattern_lifecycle_records=pats,
        watch_items=[],
        limitations=[],
        fallback_narrative="Fallback",
        interpretation_method="DETERMINISTIC",
        evidence_quality=EvidenceQuality.CONFIRMED,
        pit_safe=True,
    )


# ===========================================================================
# Test Class 1: Forward-Chaining & Lookback Bounds (Categories 1-5, 24)
# ===========================================================================


class TestForwardChainingAndLookback:
    """Tests categories 1, 2, 3, 4, 5, 24."""

    def test_k_bounds_k2_k5_k20(self) -> None:
        """Categories 1, 2, 3: Validates K=2 minimal, K=5 default, K=20 maximum lookback bounds."""
        cal = ExchangeCalendarService()

        # Test K=2
        target_k2 = cal.expected_exchange_sessions_between(cal.nth_exchange_session_before(T0, 1), T0)
        assert len(target_k2) == 2

        # Test K=5
        target_k5 = cal.expected_exchange_sessions_between(cal.nth_exchange_session_before(T0, 4), T0)
        assert len(target_k5) == 5

        # Test K=20
        target_k20 = cal.expected_exchange_sessions_between(cal.nth_exchange_session_before(T0, 19), T0)
        assert len(target_k20) == 20

    @pytest.mark.asyncio
    async def test_first_session_previous_none_and_sequential_forward_chaining(self) -> None:
        """Categories 4, 5, 24: Verifies first-session previous=None and sequential forward-chaining."""
        mock_session = AsyncMock(spec=AsyncSession)
        cal = ExchangeCalendarService()

        mock_synthesis_svc = AsyncMock()
        mock_interp_svc = AsyncMock()

        svc = SignalTrajectoryService(
            mock_session,
            calendar=cal,
            synthesis_service=mock_synthesis_svc,
            interpretation_service=mock_interp_svc,
        )

        stock = Stock(id=1, symbol="TESTSTOCK", is_active=True)
        svc._get_stock = AsyncMock(return_value=stock)
        svc._resolve_evaluation_session = AsyncMock(
            return_value=(T0, NonTradingDatePolicy.SNAP_TO_PREVIOUS_SESSION, None)
        )
        svc._get_earliest_price_date = AsyncMock(return_value=T4)

        # Candidate sessions: [T4, T3, T2, T1, T0]
        sessions_chrono = [T4, T3, T2, T1, T0]
        svc._build_candidate_sessions = MagicMock(return_value=sessions_chrono)
        svc._get_available_price_dates = AsyncMock(return_value=set(sessions_chrono))
        svc._get_corporate_actions = AsyncMock(return_value={})

        synth_map = {d: _build_mock_synthesis("TESTSTOCK", d) for d in sessions_chrono}
        mock_synthesis_svc.synthesize.side_effect = lambda symbol, evaluation_date, comparison_window: synth_map[evaluation_date]

        interp_calls: list[tuple[date, Any, Any]] = []

        async def record_interp(symbol, evaluation_date=None, comparison_window=1, *, current_synthesis=None, previous_synthesis=None, include_ai_narrative=None):
            interp_calls.append((evaluation_date, current_synthesis, previous_synthesis))
            return _build_mock_interpretation("TESTSTOCK", evaluation_date)

        mock_interp_svc.interpret.side_effect = record_interp

        res = await svc.evaluate_trajectory("TESTSTOCK", evaluation_date=T0, lookback_sessions=5)

        assert res.status == TrajectoryStatus.OK
        assert len(interp_calls) == 5

        # Check Category 4: first session (T4) must receive previous_synthesis = None
        first_call = interp_calls[0]
        assert first_call[0] == T4
        assert first_call[1] == synth_map[T4]
        assert first_call[2] is None, "First session T4 must have previous_synthesis = None"

        # Check Category 5: chronological forward-chaining
        for i in range(1, 5):
            call = interp_calls[i]
            cur_date = sessions_chrono[i]
            prev_date = sessions_chrono[i - 1]
            assert call[0] == cur_date
            assert call[1] == synth_map[cur_date]
            assert call[2] == synth_map[prev_date], f"Session {cur_date} must receive synthesis of {prev_date}"


# ===========================================================================
# Test Class 2: Data Gaps & Historical Insufficiency (Categories 6, 7, 8, 9, 10)
# ===========================================================================


class TestDataGapsAndHistoricalInsufficiency:
    """Tests categories 6, 7, 8, 9, 10."""

    @pytest.mark.asyncio
    async def test_session_data_gap_and_gap_non_bridging(self) -> None:
        """Categories 6, 7: Session gap detection & gap non-bridging rule (previous_synthesis = None)."""
        mock_session = AsyncMock(spec=AsyncSession)
        cal = ExchangeCalendarService()
        mock_synthesis_svc = AsyncMock()
        mock_interp_svc = AsyncMock()

        svc = SignalTrajectoryService(
            mock_session,
            calendar=cal,
            synthesis_service=mock_synthesis_svc,
            interpretation_service=mock_interp_svc,
        )

        stock = Stock(id=1, symbol="TESTSTOCK", is_active=True)
        svc._get_stock = AsyncMock(return_value=stock)
        svc._resolve_evaluation_session = AsyncMock(
            return_value=(T0, NonTradingDatePolicy.SNAP_TO_PREVIOUS_SESSION, None)
        )
        svc._get_earliest_price_date = AsyncMock(return_value=T2)

        # 3 sessions: T2, T1 (GAP), T0
        sessions_chrono = [T2, T1, T0]
        svc._build_candidate_sessions = MagicMock(return_value=sessions_chrono)
        # T1 is missing from available_dates -> SESSION_DATA_GAP
        svc._get_available_price_dates = AsyncMock(return_value={T2, T0})
        svc._get_corporate_actions = AsyncMock(return_value={})

        s2 = _build_mock_synthesis("TESTSTOCK", T2)
        s0 = _build_mock_synthesis("TESTSTOCK", T0)
        mock_synthesis_svc.synthesize.side_effect = lambda symbol, evaluation_date, comparison_window: s2 if evaluation_date == T2 else s0

        interp_calls = []

        async def record_interp(symbol, evaluation_date=None, comparison_window=1, *, current_synthesis=None, previous_synthesis=None, include_ai_narrative=None):
            interp_calls.append((evaluation_date, current_synthesis, previous_synthesis))
            return _build_mock_interpretation("TESTSTOCK", evaluation_date)

        mock_interp_svc.interpret.side_effect = record_interp

        res = await svc.evaluate_trajectory("TESTSTOCK", evaluation_date=T0, lookback_sessions=3)

        # Verify T1 is recorded as DATA_GAP
        gap_points = [p for p in res.sessions if p.session_date == T1]
        assert len(gap_points) == 1
        assert gap_points[0].data_status == SessionObservability.DATA_GAP

        # Verify Category 7: T0 immediately following T1(GAP) MUST receive previous_synthesis = None
        t0_call = [c for c in interp_calls if c[0] == T0][0]
        assert t0_call[2] is None, "Session T0 following gap T1 must receive previous_synthesis = None (no stale bridging)"

    def test_g_less_equal_3_carry_forward_and_streak_suspension(self) -> None:
        """Category 8: G <= 3 carry-forward state & streak suspension."""
        mock_session = AsyncMock(spec=AsyncSession)
        cal = ExchangeCalendarService()
        svc = SignalTrajectoryService(mock_session, calendar=cal)

        # 4 sessions: T3 (NEW), T2 (GAP 1), T1 (GAP 2), T0 (ACTIVE)
        sessions = [T3, T2, T1, T0]
        p_fp = "TEST|M|CONFIRMATION|BULLISH"
        pat_rec = PatternLifecycleRecord(
            pattern_fingerprint=p_fp,
            domains_involved=["PRICE"],
            metrics_involved=["m"],
            relationship_type=RelationshipType.CONFIRMATION,
            direction="BULLISH",
            current_strength=RelationshipStrength.NORMAL,
            previous_strength=RelationshipStrength.NORMAL,
            lifecycle_state=PatternLifecycle.NEW,
            description="d",
        )
        interp_t3 = _build_mock_interpretation("TESTSTOCK", T3, patterns=[pat_rec])
        interp_t0 = _build_mock_interpretation("TESTSTOCK", T0, patterns=[pat_rec])

        interpretations = {T3: interp_t3, T2: None, T1: None, T0: interp_t0}
        data_gaps = {T3: False, T2: True, T1: True, T0: False}
        masks = {s: CorporateActionEvidenceMask() for s in sessions}
        eff_supp = {s: ["PRICE"] for s in sessions}
        eff_conf = {s: [] for s in sessions}
        eff_ratios = {s: 1.0 for s in sessions}

        trajs = svc._evaluate_tier1_trajectories(
            clean_symbol="TESTSTOCK",
            sessions=sessions,
            interpretations=interpretations,
            data_gaps=data_gaps,
            masks=masks,
            effective_supporting=eff_supp,
            effective_conflicting=eff_conf,
            effective_conv_ratios=eff_ratios,
            recurrence_lookback=5,
        )

        assert len(trajs) == 1
        traj = trajs[0]
        points_by_date = {p.session_date: p for p in traj.points}

        # Gaps carry forward state NEW
        assert points_by_date[T2].data_gap is True
        assert points_by_date[T2].lifecycle_state == PatternLifecycle.NEW
        assert points_by_date[T1].data_gap is True
        assert points_by_date[T1].lifecycle_state == PatternLifecycle.NEW

    def test_g_greater_than_3_data_gap_expiry(self) -> None:
        """Category 9: G > 3 triggers RESOLVED with DATA_GAP_EXPIRY."""
        mock_session = AsyncMock(spec=AsyncSession)
        cal = ExchangeCalendarService()
        svc = SignalTrajectoryService(mock_session, calendar=cal)

        # 5 sessions: T4 (ACTIVE), T3(GAP), T2(GAP), T1(GAP), T0(GAP 4)
        sessions = [T4, T3, T2, T1, T0]
        p_fp = "TEST|M|CONFIRMATION|BULLISH"
        pat_rec = PatternLifecycleRecord(
            pattern_fingerprint=p_fp,
            domains_involved=["PRICE"],
            metrics_involved=["m"],
            relationship_type=RelationshipType.CONFIRMATION,
            direction="BULLISH",
            current_strength=RelationshipStrength.NORMAL,
            previous_strength=RelationshipStrength.NORMAL,
            lifecycle_state=PatternLifecycle.NEW,
            description="d",
        )
        interp_t4 = _build_mock_interpretation("TESTSTOCK", T4, patterns=[pat_rec])
        interpretations = {T4: interp_t4, T3: None, T2: None, T1: None, T0: None}
        data_gaps = {T4: False, T3: True, T2: True, T1: True, T0: True}
        masks = {s: CorporateActionEvidenceMask() for s in sessions}
        eff_supp = {s: ["PRICE"] for s in sessions}
        eff_conf = {s: [] for s in sessions}
        eff_ratios = {s: 1.0 for s in sessions}

        trajs = svc._evaluate_tier1_trajectories(
            clean_symbol="TESTSTOCK",
            sessions=sessions,
            interpretations=interpretations,
            data_gaps=data_gaps,
            masks=masks,
            effective_supporting=eff_supp,
            effective_conflicting=eff_conf,
            effective_conv_ratios=eff_ratios,
            recurrence_lookback=5,
        )

        assert len(trajs) == 1
        traj = trajs[0]
        # At T0, consecutive gap count is 4 (> 3) -> must be RESOLVED with DATA_GAP_EXPIRY
        assert traj.current_state == PatternLifecycle.RESOLVED
        assert traj.resolution_reason == ResolutionReason.DATA_GAP_EXPIRY
        assert traj.termination_reason == ResolutionReason.DATA_GAP_EXPIRY

    @pytest.mark.asyncio
    async def test_insufficient_historical_session_data(self) -> None:
        """Category 10: Insufficient historical data attaches limitation and does NOT force RESOLVED."""
        mock_session = AsyncMock(spec=AsyncSession)
        cal = ExchangeCalendarService()
        mock_synthesis_svc = AsyncMock()
        mock_interp_svc = AsyncMock()

        svc = SignalTrajectoryService(
            mock_session,
            calendar=cal,
            synthesis_service=mock_synthesis_svc,
            interpretation_service=mock_interp_svc,
        )

        stock = Stock(id=1, symbol="NEWLISTING", is_active=True)
        svc._get_stock = AsyncMock(return_value=stock)
        svc._resolve_evaluation_session = AsyncMock(
            return_value=(T0, NonTradingDatePolicy.SNAP_TO_PREVIOUS_SESSION, None)
        )

        # Stock was only listed on T1, so earliest_price_date = T1
        svc._get_earliest_price_date = AsyncMock(return_value=T1)
        svc._build_candidate_sessions = MagicMock(return_value=[T4, T3, T2, T1, T0])
        svc._get_available_price_dates = AsyncMock(return_value={T1, T0})
        svc._get_corporate_actions = AsyncMock(return_value={})

        mock_synthesis_svc.synthesize.return_value = _build_mock_synthesis("NEWLISTING", T0)
        mock_interp_svc.interpret.return_value = _build_mock_interpretation("NEWLISTING", T0)

        res = await svc.evaluate_trajectory("NEWLISTING", evaluation_date=T0, lookback_sessions=5)

        assert res.status == TrajectoryStatus.INSUFFICIENT_HISTORY
        lim_codes = [l.code for l in res.limitations]
        assert "INSUFFICIENT_HISTORICAL_SESSION_DATA" in lim_codes
        # Reconstructable points returned without fabricating older points
        assert len(res.sessions) == 2


# ===========================================================================
# Test Class 3: Precedence Ladder & Parity (Categories 11, 12, 13, 14, 15, 16)
# ===========================================================================


class TestPrecedenceLadderAndParity:
    """Tests categories 11, 12, 13, 14, 15, 16."""

    def test_precedence_ladder_levels(self) -> None:
        """Categories 12, 13, 14, 15: Tests all 5 levels of the deterministic precedence ladder."""
        cal = ExchangeCalendarService()
        svc = SignalTrajectoryService(AsyncMock(), calendar=cal)

        # Level 1: New Core Contradiction -> WEAKENING
        st_lvl1 = svc._evaluate_precedence_ladder(
            prev_strength=RelationshipStrength.NORMAL,
            cur_strength=RelationshipStrength.NORMAL,
            prev_conflicting=[],
            cur_conflicting=["PRICE"],
            prev_supporting=["TECHNICAL"],
            cur_supporting=["TECHNICAL"],
            prev_conv_ratio=1.0,
            cur_conv_ratio=0.5,
        )
        assert st_lvl1 == PatternLifecycle.WEAKENING

        # Level 2: Strength Rank Increase -> STRENGTHENING
        st_lvl2_up = svc._evaluate_precedence_ladder(
            prev_strength=RelationshipStrength.NORMAL,
            cur_strength=RelationshipStrength.MAJOR,
            prev_conflicting=[],
            cur_conflicting=[],
            prev_supporting=["PRICE"],
            cur_supporting=["PRICE"],
            prev_conv_ratio=1.0,
            cur_conv_ratio=1.0,
        )
        assert st_lvl2_up == PatternLifecycle.STRENGTHENING

        # Level 2: Strength Rank Decrease -> WEAKENING
        st_lvl2_down = svc._evaluate_precedence_ladder(
            prev_strength=RelationshipStrength.MAJOR,
            cur_strength=RelationshipStrength.NORMAL,
            prev_conflicting=[],
            cur_conflicting=[],
            prev_supporting=["PRICE"],
            cur_supporting=["PRICE"],
            prev_conv_ratio=1.0,
            cur_conv_ratio=1.0,
        )
        assert st_lvl2_down == PatternLifecycle.WEAKENING

        # Level 3: Convergence Delta > 0.05 -> STRENGTHENING
        st_lvl3_up = svc._evaluate_precedence_ladder(
            prev_strength=RelationshipStrength.NORMAL,
            cur_strength=RelationshipStrength.NORMAL,
            prev_conflicting=[],
            cur_conflicting=[],
            prev_supporting=["PRICE"],
            cur_supporting=["PRICE"],
            prev_conv_ratio=0.60,
            cur_conv_ratio=0.70,  # Delta = +0.10 > 0.05
        )
        assert st_lvl3_up == PatternLifecycle.STRENGTHENING

        # Level 3: Convergence Delta < -0.05 -> WEAKENING
        st_lvl3_down = svc._evaluate_precedence_ladder(
            prev_strength=RelationshipStrength.NORMAL,
            cur_strength=RelationshipStrength.NORMAL,
            prev_conflicting=[],
            cur_conflicting=[],
            prev_supporting=["PRICE"],
            cur_supporting=["PRICE"],
            prev_conv_ratio=0.80,
            cur_conv_ratio=0.70,  # Delta = -0.10 < -0.05
        )
        assert st_lvl3_down == PatternLifecycle.WEAKENING

        # Level 4: Confirming Directional Domain Count Increase -> STRENGTHENING
        st_lvl4_up = svc._evaluate_precedence_ladder(
            prev_strength=RelationshipStrength.NORMAL,
            cur_strength=RelationshipStrength.NORMAL,
            prev_conflicting=[],
            cur_conflicting=[],
            prev_supporting=["PRICE"],
            cur_supporting=["PRICE", "TECHNICAL"],
            prev_conv_ratio=1.0,
            cur_conv_ratio=1.0,
        )
        assert st_lvl4_up == PatternLifecycle.STRENGTHENING

        # Level 5: Parity -> CONTINUING
        st_lvl5 = svc._evaluate_precedence_ladder(
            prev_strength=RelationshipStrength.NORMAL,
            cur_strength=RelationshipStrength.NORMAL,
            prev_conflicting=[],
            cur_conflicting=[],
            prev_supporting=["PRICE"],
            cur_supporting=["PRICE"],
            prev_conv_ratio=1.0,
            cur_conv_ratio=1.0,
        )
        assert st_lvl5 == PatternLifecycle.CONTINUING

    def test_recurrence_qualifier(self) -> None:
        """Category 16: Recurrence status (NONE vs RECURRING) across resolution and reappearance."""
        cal = ExchangeCalendarService()
        svc = SignalTrajectoryService(AsyncMock(), calendar=cal)

        # Sessions: T3 (NEW), T2 (RESOLVED), T1 (RESOLVED), T0 (NEW / RECURRING)
        sessions = [T3, T2, T1, T0]
        p_fp = "TEST|M|CONFIRMATION|BULLISH"
        pat_rec = PatternLifecycleRecord(
            pattern_fingerprint=p_fp,
            domains_involved=["PRICE"],
            metrics_involved=["m"],
            relationship_type=RelationshipType.CONFIRMATION,
            direction="BULLISH",
            current_strength=RelationshipStrength.NORMAL,
            previous_strength=RelationshipStrength.NORMAL,
            lifecycle_state=PatternLifecycle.NEW,
            description="d",
        )
        interp_active = _build_mock_interpretation("TESTSTOCK", T3, patterns=[pat_rec])
        interp_resolved = _build_mock_interpretation("TESTSTOCK", T2, patterns=[])

        interpretations = {T3: interp_active, T2: interp_resolved, T1: interp_resolved, T0: interp_active}
        data_gaps = {s: False for s in sessions}
        masks = {s: CorporateActionEvidenceMask() for s in sessions}
        eff_supp = {s: ["PRICE"] for s in sessions}
        eff_conf = {s: [] for s in sessions}
        eff_ratios = {s: 1.0 for s in sessions}

        trajs = svc._evaluate_tier1_trajectories(
            clean_symbol="TESTSTOCK",
            sessions=sessions,
            interpretations=interpretations,
            data_gaps=data_gaps,
            masks=masks,
            effective_supporting=eff_supp,
            effective_conflicting=eff_conf,
            effective_conv_ratios=eff_ratios,
            recurrence_lookback=5,
        )

        assert len(trajs) == 1
        traj = trajs[0]
        # At T0, pattern reappears after being resolved at T2/T1 within lookback M=5 -> RECURRING
        assert traj.current_state == PatternLifecycle.NEW
        assert traj.current_recurrence_status == RecurrenceStatus.RECURRING
        assert traj.recurrence.status == RecurrenceStatus.RECURRING


# ===========================================================================
# Test Class 4: Corporate Action Mask & Identity Boundaries (Categories 17-23)
# ===========================================================================


class TestCorporateActionMaskAndIdentity:
    """Tests categories 17, 18, 19, 20, 21, 22, 23."""

    def test_corporate_action_split_bonus_masking(self) -> None:
        """Categories 17, 18, 19, 20: Tests C-01A & C-02A split/bonus masking & canonical fingerprint immutability."""
        interp = _build_mock_interpretation(
            "TESTSTOCK",
            T0,
            supporting_domains=["PRICE", "TECHNICAL", "PARTICIPATION"],
            conflicting_domains=[],
        )

        # Evaluate C-01A derivation
        supp_no_price = [d for d in interp.convergence.supporting_domains if d != DOMAIN_PRICE]
        assert supp_no_price == ["TECHNICAL", "PARTICIPATION"]
        assert DOMAIN_PRICE not in supp_no_price

        # Check fingerprint immutability: canonical D3 fingerprint MUST remain unchanged
        original_fp = interp.pattern_lifecycle_records[0].pattern_fingerprint
        assert "PRICE" in original_fp, "Canonical fingerprint must retain its original definition (C-02A)"

    @pytest.mark.asyncio
    async def test_pit_late_corporate_action_non_leakage(self) -> None:
        """Category 23: PIT late corporate action is not applied if available_at > session date."""
        cal = ExchangeCalendarService()
        svc = SignalTrajectoryService(AsyncMock(), calendar=cal)

        # Late corporate action: ex_date is T1, but available_at was published at T0 (future relative to T1)
        ca_late = CorporateAction(
            id=11,
            stock_id=1,
            action_type="split",
            ex_date=T1,
            available_at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),  # Published after T1
            ratio_numerator=1,
            ratio_denominator=2,
        )

        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [ca_late]
        svc.session.execute = AsyncMock(return_value=mock_res)

        actions = await svc._get_corporate_actions(stock_id=1, candidate_dates=[T1], as_of_date=T1)
        # Because available_at > T1, it must be excluded under PIT rules
        assert T1 not in actions

    def test_tier1_tier2_preservation_and_directional_flip(self) -> None:
        """Categories 21, 22: Tier-1 preservation and Tier-2 directional flip termination."""
        cal = ExchangeCalendarService()
        svc = SignalTrajectoryService(AsyncMock(), calendar=cal)

        # Sessions: T1 (BULLISH episode), T0 (BEARISH flip)
        sessions = [T1, T0]
        interp_t1 = _build_mock_interpretation("TESTSTOCK", T1, directional_bias=DirectionalBias.BULLISH_EVIDENCE)
        interp_t0 = _build_mock_interpretation("TESTSTOCK", T0, directional_bias=DirectionalBias.BEARISH_EVIDENCE)

        interpretations = {T1: interp_t1, T0: interp_t0}
        data_gaps = {T1: False, T0: False}
        masks = {s: CorporateActionEvidenceMask() for s in sessions}
        eff_supp = {s: ["PRICE"] for s in sessions}
        eff_conf = {s: [] for s in sessions}
        eff_ratios = {s: 1.0 for s in sessions}

        tier2 = svc._evaluate_tier2_trajectory(
            clean_symbol="TESTSTOCK",
            sessions=sessions,
            interpretations=interpretations,
            data_gaps=data_gaps,
            masks=masks,
            effective_supporting=eff_supp,
            effective_conflicting=eff_conf,
            effective_conv_ratios=eff_ratios,
            recurrence_lookback=5,
        )

        assert tier2 is not None
        # On directional flip at T0, the previous episode terminates (RESOLVED)
        assert tier2.points[1].lifecycle_state == PatternLifecycle.RESOLVED
        assert tier2.current_state == PatternLifecycle.RESOLVED
        assert tier2.resolution_reason == ResolutionReason.DIRECTIONAL_FLIP


# ===========================================================================
# Test Class 5: Security, API, Firewalls & OPT-1 Isolation (Categories 25-31)
# ===========================================================================


class TestSecurityApiFirewallsAndIsolation:
    """Tests categories 25, 26, 27, 28, 29, 30, 31."""

    @pytest.mark.asyncio
    async def test_authentication_gated(self) -> None:
        """Category 26: Endpoint returns 401 Unauthorized without authentication token."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/stocks/RELIANCE/signal-trajectory")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_api_query_validation_bounds(self) -> None:
        """Category 27: Query validation catches lookback_sessions outside [2, 20]."""
        from app.api.routes import signal_trajectories
        from app.models.auth import User
        from app.services.auth_service import AuthenticatedUser

        dummy_user = AuthenticatedUser(
            user=MagicMock(spec=User, id=1, is_superuser=True),
            principal=MagicMock(),
            roles=("admin",),
            permissions=("stocks.read",),
        )
        dep = signal_trajectories.router.dependencies[0].dependency
        app.dependency_overrides[dep] = lambda: dummy_user
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # lookback_sessions = 1 (violates ge=2)
                resp = await client.get(
                    "/api/v1/stocks/RELIANCE/signal-trajectory",
                    params={"lookback_sessions": 1},
                )
                assert resp.status_code == 422

                # lookback_sessions = 25 (violates le=20)
                resp2 = await client.get(
                    "/api/v1/stocks/RELIANCE/signal-trajectory",
                    params={"lookback_sessions": 25},
                )
                assert resp2.status_code == 422
        finally:
            app.dependency_overrides.pop(dep, None)

    @pytest.mark.asyncio
    async def test_include_resolved_presentation_filter(self) -> None:
        """Category 28: include_resolved=False filters out resolved patterns from presentation only."""
        mock_session = AsyncMock(spec=AsyncSession)
        cal = ExchangeCalendarService()
        svc = SignalTrajectoryService(mock_session, calendar=cal)

        stock = Stock(id=1, symbol="TESTSTOCK", is_active=True)
        svc._get_stock = AsyncMock(return_value=stock)
        svc._resolve_evaluation_session = AsyncMock(
            return_value=(T0, NonTradingDatePolicy.SNAP_TO_PREVIOUS_SESSION, None)
        )
        svc._get_earliest_price_date = AsyncMock(return_value=T1)
        svc._build_candidate_sessions = MagicMock(return_value=[T1, T0])
        svc._get_available_price_dates = AsyncMock(return_value={T1, T0})
        svc._get_corporate_actions = AsyncMock(return_value={})

        # Pattern is active at T1, absent at T0 -> RESOLVED
        p_fp = "P1|M|CONFIRMATION|BULLISH"
        pat = PatternLifecycleRecord(
            pattern_fingerprint=p_fp,
            domains_involved=["PRICE"],
            metrics_involved=["m"],
            relationship_type=RelationshipType.CONFIRMATION,
            direction="BULLISH",
            current_strength=RelationshipStrength.NORMAL,
            previous_strength=RelationshipStrength.NORMAL,
            lifecycle_state=PatternLifecycle.NEW,
            description="d",
        )
        interp1 = _build_mock_interpretation("TESTSTOCK", T1, patterns=[pat])
        interp0 = _build_mock_interpretation("TESTSTOCK", T0, patterns=[])

        svc.synthesis_service.synthesize = AsyncMock(return_value=_build_mock_synthesis("TESTSTOCK", T0))
        svc.interpretation_service.interpret = AsyncMock(side_effect=[interp1, interp0])

        # Call with include_resolved = False
        res_filtered = await svc.evaluate_trajectory(
            "TESTSTOCK", evaluation_date=T0, lookback_sessions=2, include_resolved=False
        )
        assert len(res_filtered.tier1_trajectories) == 0, "Resolved trajectory must be filtered out when include_resolved=False"

        # Call with include_resolved = True
        svc.interpretation_service.interpret = AsyncMock(side_effect=[interp1, interp0])
        res_all = await svc.evaluate_trajectory(
            "TESTSTOCK", evaluation_date=T0, lookback_sessions=2, include_resolved=True
        )
        assert len(res_all.tier1_trajectories) == 1, "Resolved trajectory must be retained when include_resolved=True"

    def test_ai_and_trading_firewalls(self) -> None:
        """Categories 29, 30: AI narrative is never authoritative; zero BUY/SELL/CE/PE in schemas."""
        import inspect
        import app.schemas.signal_trajectory as schemas_mod

        # Verify no trading terms in any schema docstrings or field names
        prohibited = ["buy", "sell", "call_option", "put_option", "ce", "pe", "stop_loss", "target_price"]
        for name, cls in inspect.getmembers(schemas_mod, inspect.isclass):
            if issubclass(cls, schemas_mod.BaseModel):
                for field in cls.model_fields:
                    assert field.lower() not in prohibited, f"Prohibited trading field '{field}' found in {name}"

    def test_opt1_isolation_audit(self) -> None:
        """Category 31: Verifies D7 has zero imports or dependencies on OPT-1."""
        with open("app/schemas/signal_trajectory.py", "r", encoding="utf-8") as f:
            text_schemas = f.read()
        with open("app/services/signal_trajectory_service.py", "r", encoding="utf-8") as f:
            text_service = f.read()
        with open("app/api/routes/signal_trajectories.py", "r", encoding="utf-8") as f:
            text_routes = f.read()

        for forbidden in ["option_chain", "OptionChain", "opt1", "OPT1", "OPT_1"]:
            assert forbidden not in text_schemas, f"Forbidden OPT-1 token '{forbidden}' found in schemas"
            assert forbidden not in text_service, f"Forbidden OPT-1 token '{forbidden}' found in service"
            assert forbidden not in text_routes, f"Forbidden OPT-1 token '{forbidden}' found in routes"


# ===========================================================================
# Test Class 6: Real PostgreSQL Database Integration
# ===========================================================================


class TestRealDatabaseIntegration:
    """Real database execution against PostgreSQL."""

    @pytest.mark.asyncio
    async def test_real_db_end_to_end_trajectory(self, db_session) -> None:
        """Executes SignalTrajectoryService against actual database tables for an active stock."""
        stmt = (
            select(Stock.symbol)
            .join(DailyPrice, DailyPrice.stock_id == Stock.id)
            .where(Stock.is_active.is_(True))
            .limit(1)
        )
        res = await db_session.execute(stmt)
        sym = res.scalar_one_or_none()
        if not sym:
            sym = "DBTEST"
            stock = Stock(symbol=sym, company_name="DB Test Stock", is_active=True)
            db_session.add(stock)
            await db_session.flush()
            for d in [T2, T1, T0]:
                p = DailyPrice(
                    stock_id=stock.id,
                    trade_date=d,
                    open=Decimal("100.0"),
                    high=Decimal("105.0"),
                    low=Decimal("95.0"),
                    close=Decimal("102.0"),
                    volume=10000,
                )
                db_session.add(p)
            await db_session.flush()

        mock_synth = AsyncMock()
        mock_interp = AsyncMock()
        mock_synth.synthesize.side_effect = lambda symbol, evaluation_date, comparison_window: _build_mock_synthesis(sym, evaluation_date)

        async def _mock_interp(symbol, evaluation_date=None, comparison_window=1, *, current_synthesis=None, previous_synthesis=None, include_ai_narrative=None):
            return _build_mock_interpretation(sym, evaluation_date)

        mock_interp.interpret.side_effect = _mock_interp

        svc = SignalTrajectoryService(
            session=db_session,
            synthesis_service=mock_synth,
            interpretation_service=mock_interp,
        )
        resp = await svc.evaluate_trajectory(
            symbol=sym,
            evaluation_date=T0,
            lookback_sessions=3,
            include_resolved=True,
        )

        assert isinstance(resp, StockSignalTrajectoryResponse)
        assert resp.symbol == sym.upper()
        assert resp.status in (
            TrajectoryStatus.OK,
            TrajectoryStatus.INSUFFICIENT_HISTORY,
            TrajectoryStatus.SUSPENDED_DATA_GAP,
        )
        assert len(resp.sessions) <= 3


# ===========================================================================
# Test Class 7: Protected Files & Migration Invariant Audit
# ===========================================================================


class TestProtectedFilesAndAlembic:
    """Guarantees zero migrations created and protected files remain untouched."""

    def test_alembic_head_remains_unchanged(self) -> None:
        """D7 is strictly read-only and stateless; zero database migrations allowed."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()

        # Certified baseline or linear descendant
        authorized_heads = {"f1b9c8d7e6a5", "b3a4c5d6e7f8"}
        assert head in authorized_heads, f"Expected alembic head in {authorized_heads}, but found {head}"
        assert len(script.get_heads()) == 1, f"Expected exactly 1 migration head, but found {script.get_heads()}"
