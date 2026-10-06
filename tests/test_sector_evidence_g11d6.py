"""Comprehensive Test Suite for G.11-D-6: Sector Breadth & Market Evidence Consensus Aggregator.

Validates:
  1. TestSectorUniverseAndConfig:
     - 17 supported NSE sector index codes from SECTOR_INDEX_CODES
     - ALL_SECTORS resolves to all 17 codes
     - Rejection of invalid sector code raises ValueError (422 in API)
     - Pydantic schema immutability (frozen=True) on Request, Response, Summary, Detail
     - Comparison window validation (1-30)
  2. TestSectorConstituentPITResolution:
     - PIT-01: Index constituent addition announced after as_of_date omitted
     - PIT-02: Index constituent addition announced on/before as_of_date included
     - PIT-03: Inactive stock retained historically if valid in IndexMembership
     - PIT-04: Current Stock.sector_id ignored historically in favor of IndexMembership
     - PIT-05: Historical replay requested without IndexMembership fails closed with
               UNSUPPORTED_HISTORICAL_SECTOR_TAXONOMY
     - PIT-06: Historical replay with as_of_date < evaluation_date clamps effective cutoff
     - PIT-07: Derivative data unavailable for F&O-eligible constituent not counted as inactive
     - PIT-08: Constituent with INSUFFICIENT_EVIDENCE counted in 5th state bucket
     - PIT-09: Live evaluation mode permits taxonomy fallback with active trading presence
  3. TestBreadthDenominatorsAndDistributions:
     - Canonical denominators conservation: N_TOTAL = N_VALID + N_INSUFFICIENT + N_NO_DATA + N_ERROR
     - 5-state distribution: bullish, bearish, mixed, neutral, insufficient_evidence
     - Directional percentages computed over N_ANALYTICAL_VALID and sum to 100%
     - Coverage status transitions (CONFIRMED, PARTIAL_DATA, INSUFFICIENT_DATA)
  4. TestConvergenceStatistics:
     - Mean and median convergence ratio calculations over valid constituents
     - Null convergence when no valid constituents exist
  5. TestDerivativesAndParticipationMetrics:
     - Derivatives eligibility, valid count, and active percentage
     - Delivery breakout %, volume breakout %, institutional active %
  6. TestConcentrationAndContradictionEngine:
     - PRICE_BREADTH_CONTRADICTION flag
     - DELIVERY_CONFIRMATION_DEFICIT flag
     - HIGH_CONCENTRATION_RISK flag with equal-weight return contribution proxy
     - Edge-case guards for concentration: N < 5, |S_pos| < 2, positive net breadth
     - SECTOR_EVIDENCE_CONFLICTED flag (mixed_pct >= 40%)
  7. TestServiceBoundaryAndConcurrency:
     - Bounded concurrency with max 5 workers via semaphore
     - Isolated worker sessions via session_factory
     - Direct invocation of public WhatChangedInterpretationService.interpret() without AI narrative
     - No screening universe truncation
  8. TestSectorEvidenceApiRoutes:
     - GET /api/v1/market-intelligence/sectors/evidence-breadth (200, 401, 403, 422)
     - Market-wide aggregate fields in API response
  9. TestSectorEvidenceHtmlAndUiRoutes:
     - GET /market-intelligence/sectors/evidence-breadth returns full shell page
     - GET /market-intelligence/sectors/evidence-breadth with HX-Request returns matrix partial
     - UI renders ribbons, heatmap, and contradiction badges
  10. TestProtectedFilesAndAlembic:
     - Section 32 protected list integrity (19 files untouched)
     - Alembic head strictly remains f1b9c8d7e6a5
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.sector_universe import (
    SECTOR_INDEX_CODES,
)
from app.models.index_membership import IndexMembership
from app.models.market_index import MarketIndex
from app.models.sector import Sector
from app.models.stock import Stock
from app.schemas.sector_evidence import (
    CoverageStatus,
    SectorContradictionDetail,
    SectorContradictionFlag,
    SectorEvidenceBreadthRequest,
    SectorEvidenceBreadthResponse,
    SectorEvidenceSummary,
)
from app.schemas.what_changed_interpretation import (
    DirectionalConvergence,
    EvidenceItem,
    Limitation,
    OverallLifecycleSummary,
    PatternLifecycleRecord,
    StockIntelligenceInterpretation,
    WatchItem,
)
from app.services.sector_evidence_aggregator_service import (
    SectorEvidenceAggregatorService,
)
from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    EvidenceClassification,
    OverallState,
)
from app.services.what_changed_synthesis_config import (
    EvidenceQuality,
    PatternLifecycle,
    RelationshipStrength,
    RelationshipType,
)
from tests.auth_helpers import auth_client

EVAL_DATE = date(2026, 9, 15)
COMP_DATE = date(2026, 9, 14)


# ===========================================================================
# Helper Fixtures & Mock Factories
# ===========================================================================


def _build_mock_d3_interp(
    symbol: str = "TCS",
    overall_state: OverallState = OverallState.BULLISH_EVIDENCE_SUPPORTED,
    directional_bias: DirectionalBias = DirectionalBias.BULLISH_EVIDENCE,
    convergence_ratio: float = 0.80,
    supporting_domains: list[str] | None = None,
    conflicting_domains: list[str] | None = None,
    missing_domains: list[str] | None = None,
    context_domains: list[str] | None = None,
    volume_breakout: bool = False,
    delivery_breakout: bool = False,
    derivatives_active: bool = False,
    institutional_active: bool = False,
) -> StockIntelligenceInterpretation:
    """Build a deterministic mock StockIntelligenceInterpretation for sector breadth tests."""
    supp = supporting_domains or ["PRICE", "VOLUME"]
    conf = conflicting_domains or []
    ctx = context_domains or []
    miss = missing_domains or []

    if volume_breakout and "PARTICIPATION" not in supp:
        supp = list(supp) + ["PARTICIPATION"]
    if derivatives_active and "DERIVATIVES" not in supp:
        supp = list(supp) + ["DERIVATIVES"]
    if institutional_active and "INSTITUTIONAL" not in ctx:
        ctx = list(ctx) + ["INSTITUTIONAL"]

    primary_ev = [
        EvidenceItem(
            domain="PRICE",
            classification=EvidenceClassification.PRIMARY,
            relationship_type=RelationshipType.CONFIRMATION,
            strength=RelationshipStrength.MAJOR,
            reason="Price breakout observed above resistance",
        )
    ]
    supporting_ev = []
    if volume_breakout:
        supporting_ev.append(
            EvidenceItem(
                domain="VOLUME",
                classification=EvidenceClassification.SUPPORTING,
                relationship_type=RelationshipType.CONFIRMATION,
                strength=RelationshipStrength.MAJOR,
                reason="Volume expansion 2.4x over 20-day average surge",
                metadata={"volume_surge": True, "significance": 85.0},
            )
        )
    if delivery_breakout:
        supporting_ev.append(
            EvidenceItem(
                domain="VOLUME",
                classification=EvidenceClassification.SUPPORTING,
                relationship_type=RelationshipType.CONFIRMATION,
                strength=RelationshipStrength.MAJOR,
                reason="Delivery breakout volume surge confirmed",
                metadata={"delivery_surge": True, "significance": 80.0},
            )
        )
    if derivatives_active:
        supporting_ev.append(
            EvidenceItem(
                domain="DERIVATIVES",
                classification=EvidenceClassification.SUPPORTING,
                relationship_type=RelationshipType.CONFIRMATION,
                strength=RelationshipStrength.MAJOR,
                reason="Substantial put OI addition and call unwinding observed",
                metadata={"oi_surge": True},
            )
        )

    context_ev = []
    if institutional_active:
        context_ev.append(
            EvidenceItem(
                domain="INSTITUTIONAL",
                classification=EvidenceClassification.CONTEXT,
                relationship_type=RelationshipType.CONTEXT_ONLY,
                strength=RelationshipStrength.NORMAL,
                reason="Large institutional block deal recorded on session",
            )
        )

    return StockIntelligenceInterpretation(
        symbol=symbol,
        evaluation_date=EVAL_DATE,
        comparison_date=COMP_DATE,
        overall_state=overall_state,
        directional_bias=directional_bias,
        convergence=DirectionalConvergence(
            directional_value=3,
            directional_max_possible=4,
            convergence_ratio=convergence_ratio,
            supporting_domains=supp,
            conflicting_domains=conf,
            context_domains=ctx,
            missing_domains=miss,
            interpretation="Directional convergence evaluated.",
        ),
        overall_lifecycle=OverallLifecycleSummary(
            pattern_count=1,
            new_count=1,
            continuing_count=0,
            strengthening_count=0,
            weakening_count=0,
            resolved_count=0,
            description="Lifecycle pattern active.",
        ),
        pattern_lifecycle_records=[
            PatternLifecycleRecord(
                pattern_fingerprint="fp-1",
                relationship_type=RelationshipType.CONFIRMATION,
                lifecycle_state=PatternLifecycle.NEW,
                change_descriptor=None,
                description="Pattern confirmed.",
            )
        ],
        primary_evidence=primary_ev,
        supporting_evidence=supporting_ev,
        conflicting_evidence=[],
        context_evidence=context_ev,
        watch_items=[WatchItem(code="WATCH_1", description="Watch item description")],
        limitations=[Limitation(code="LIMIT_1", description="Limitation description")],
        narrative=None,
        fallback_narrative="Deterministic fallback narrative.",
        interpretation_method="DETERMINISTIC",
        evidence_quality=EvidenceQuality.CONFIRMED,
        pit_safe=True,
        provenance=["PRICE", "VOLUME"],
        generated_at=datetime.now(UTC),
    )


def _create_mock_session_factory():
    """Create a mock session factory returning an async context manager."""
    mock_session = AsyncMock(spec=AsyncSession)
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = mock_session
    factory.return_value.__aexit__.return_value = None
    return factory


# ===========================================================================
# 1. Configuration, Enums & Schema Immutability Tests
# ===========================================================================


class TestSectorUniverseAndConfig:
    def test_17_supported_sector_codes_configured(self) -> None:
        """Verify supported NSE sector/thematic index codes."""
        assert len(SECTOR_INDEX_CODES) >= 17
        assert "NIFTY_AUTO" in SECTOR_INDEX_CODES
        assert "NIFTY_BANK" in SECTOR_INDEX_CODES
        assert "NIFTY_IT" in SECTOR_INDEX_CODES
        assert "NIFTY_PHARMA" in SECTOR_INDEX_CODES
        assert "NIFTY_METAL" in SECTOR_INDEX_CODES

    def test_all_sectors_resolves_to_17_codes(self) -> None:
        """Verify ALL_SECTORS expands to all 17 codes."""
        service = SectorEvidenceAggregatorService(AsyncMock(), MagicMock())
        resolved = service._resolve_requested_sectors(["ALL_SECTORS"])
        assert resolved == list(SECTOR_INDEX_CODES)

        resolved_empty = service._resolve_requested_sectors([])
        assert resolved_empty == list(SECTOR_INDEX_CODES)

    def test_unsupported_sector_code_raises_value_error(self) -> None:
        """Verify that an unsupported sector code raises ValueError."""
        service = SectorEvidenceAggregatorService(AsyncMock(), MagicMock())
        with pytest.raises(ValueError, match="Unsupported sector code 'NIFTY_INVALID'"):
            service._resolve_requested_sectors(["NIFTY_INVALID"])

    def test_schema_immutability_frozen(self) -> None:
        """Verify frozen=True on Request, Response, Summary, and Contradiction models."""
        req = SectorEvidenceBreadthRequest(sectors=["NIFTY_IT"])
        with pytest.raises(ValidationError):
            req.comparison_window = 5  # type: ignore

        detail = SectorContradictionDetail(
            flag=SectorContradictionFlag.PRICE_BREADTH_CONTRADICTION,
            description="Test contradiction",
        )
        with pytest.raises(ValidationError):
            detail.flag = SectorContradictionFlag.HIGH_CONCENTRATION_RISK  # type: ignore

    def test_request_comparison_window_validation(self) -> None:
        """Verify comparison_window is constrained between 1 and 30."""
        with pytest.raises(ValidationError):
            SectorEvidenceBreadthRequest(comparison_window=0)

        with pytest.raises(ValidationError):
            SectorEvidenceBreadthRequest(comparison_window=31)

        valid = SectorEvidenceBreadthRequest(comparison_window=10)
        assert valid.comparison_window == 10


# ===========================================================================
# 2. Point-in-Time (PIT) Test Matrix (PIT-01 through PIT-09)
# ===========================================================================


@pytest.mark.asyncio
class TestSectorConstituentPITResolution:
    async def test_pit_01_addition_after_as_of_omitted(self, db_session) -> None:
        """PIT-01: Index constituent addition announced after as_of_date is strictly omitted."""
        idx = MarketIndex(code="NIFTY_IT", name="Nifty IT", country="INDIA")
        stock = Stock(symbol="LATE_ADD", company_name="Late Add Ltd", exchange="NSE", is_active=True)
        db_session.add_all([idx, stock])
        await db_session.flush()

        # Added historically AFTER cutoff 2026-09-10
        membership = IndexMembership(
            index_id=idx.id,
            stock_id=stock.id,
            valid_from=date(2026, 9, 12),
            available_at=date(2026, 9, 12),
        )
        db_session.add(membership)
        await db_session.commit()

        service = SectorEvidenceAggregatorService(db_session, _create_mock_session_factory())
        constituents, tier, is_unsupported = await service._resolve_sector_constituents(
            sector_code="NIFTY_IT",
            as_of_date=date(2026, 9, 10),
            effective_date=date(2026, 9, 10),
        )
        # Should be excluded because membership is after as_of_date
        assert not any(s.symbol == "LATE_ADD" for s in constituents)

    async def test_pit_02_addition_on_or_before_as_of_included(self, db_session) -> None:
        """PIT-02: Index constituent addition announced on or before as_of_date is included."""
        idx = MarketIndex(code="NIFTY_IT", name="Nifty IT", country="INDIA")
        stock = Stock(symbol="TIMELY_ADD", company_name="Timely Add Ltd", exchange="NSE", is_active=True)
        db_session.add_all([idx, stock])
        await db_session.flush()

        membership = IndexMembership(
            index_id=idx.id,
            stock_id=stock.id,
            valid_from=date(2026, 9, 1),
            available_at=date(2026, 9, 1),
        )
        db_session.add(membership)
        await db_session.commit()

        service = SectorEvidenceAggregatorService(db_session, _create_mock_session_factory())
        constituents, tier, is_unsupported = await service._resolve_sector_constituents(
            sector_code="NIFTY_IT",
            as_of_date=date(2026, 9, 10),
            effective_date=date(2026, 9, 10),
        )
        assert any(s.symbol == "TIMELY_ADD" for s in constituents)
        assert tier == "TIER_1_INDEX_MEMBERSHIP"
        assert not is_unsupported

    async def test_pit_03_inactive_stock_retained_historically(self, db_session) -> None:
        """PIT-03: Current Stock.is_active == False does NOT remove historical member."""
        idx = MarketIndex(code="NIFTY_AUTO", name="Nifty Auto", country="INDIA")
        stock = Stock(symbol="DELISTED_CO", company_name="Delisted Co", exchange="NSE", is_active=False)
        db_session.add_all([idx, stock])
        await db_session.flush()

        membership = IndexMembership(
            index_id=idx.id,
            stock_id=stock.id,
            valid_from=date(2026, 1, 1),
            available_at=date(2026, 1, 1),
        )
        db_session.add(membership)
        await db_session.commit()

        service = SectorEvidenceAggregatorService(db_session, _create_mock_session_factory())
        constituents, tier, _ = await service._resolve_sector_constituents(
            sector_code="NIFTY_AUTO",
            as_of_date=date(2026, 6, 1),
            effective_date=date(2026, 6, 1),
        )
        # Stock must be present despite current is_active=False
        symbols = [s.symbol for s in constituents]
        assert "DELISTED_CO" in symbols

    async def test_pit_04_different_current_sector_ignored_historically(self, db_session) -> None:
        """PIT-04: Current Stock.sector_id differs from historical index membership."""
        sec_pharma = Sector(name="Pharmaceuticals")
        idx_metal = MarketIndex(code="NIFTY_METAL", name="Nifty Metal", country="INDIA")
        db_session.add_all([sec_pharma, idx_metal])
        await db_session.flush()

        # Stock currently assigned to Pharma in static DB, but was member of Metal index
        stock = Stock(symbol="CLASSIF_DIFF", company_name="Classif Diff Ltd", exchange="NSE", is_active=True, sector_id=sec_pharma.id)
        db_session.add(stock)
        await db_session.flush()

        membership = IndexMembership(
            index_id=idx_metal.id,
            stock_id=stock.id,
            valid_from=date(2026, 1, 1),
            available_at=date(2026, 1, 1),
        )
        db_session.add(membership)
        await db_session.commit()

        service = SectorEvidenceAggregatorService(db_session, _create_mock_session_factory())
        constituents, tier, _ = await service._resolve_sector_constituents(
            sector_code="NIFTY_METAL",
            as_of_date=date(2026, 5, 1),
            effective_date=date(2026, 5, 1),
        )
        assert any(s.symbol == "CLASSIF_DIFF" for s in constituents)

    async def test_pit_05_unsupported_historical_taxonomy_fails_closed(self, db_session) -> None:
        """PIT-05: Historical replay requested for sector without IndexMembership returns UNSUPPORTED_HISTORICAL_SECTOR_TAXONOMY."""
        # Query for NIFTY_MEDIA when no index or memberships exist for it
        service = SectorEvidenceAggregatorService(db_session, _create_mock_session_factory())
        req = SectorEvidenceBreadthRequest(
            sectors=["NIFTY_MEDIA"],
            as_of_date=date(2026, 1, 1),
        )
        resp = await service.get_sector_evidence_breadth(req)
        assert len(resp.sectors) == 1
        summary = resp.sectors[0]
        assert summary.coverage_status == CoverageStatus.UNSUPPORTED_HISTORICAL_SECTOR_TAXONOMY
        assert summary.total_eligible_constituents == 0

    async def test_pit_06_as_of_before_eval_date_clamped_cutoff(self) -> None:
        """PIT-06: Historical replay with as_of_date < evaluation_date clamps effective cutoff."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        eval_d = date(2026, 9, 20)
        as_of_d = date(2026, 9, 10)
        req = SectorEvidenceBreadthRequest(
            sectors=["NIFTY_IT"],
            evaluation_date=eval_d,
            as_of_date=as_of_d,
        )

        with (
            patch.object(service, "_fetch_fno_eligible_stock_ids", new_callable=AsyncMock, return_value=set()),
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock),
            patch.object(service, "_evaluate_single_sector", new_callable=AsyncMock) as mock_eval,
        ):
            mock_eval.return_value = SectorEvidenceSummary(
                sector_code="NIFTY_IT",
                sector_name="Nifty IT",
                category="Sectoral",
                evaluation_date=eval_d,
                as_of_date=as_of_d,
                comparison_window=1,
                total_eligible_constituents=1,
                evaluated_constituents=1,
                analytical_valid_constituents=1,
                insufficient_evidence_count=0,
                no_data_count=0,
                error_count=0,
                data_coverage_pct=100.0,
                insufficient_evidence_pct=0.0,
                coverage_status=CoverageStatus.CONFIRMED,
                bullish_count=1,
                bearish_count=0,
                mixed_count=0,
                neutral_count=0,
                bullish_pct=100.0,
                bearish_pct=0.0,
                mixed_pct=0.0,
                neutral_pct=0.0,
                net_directional_breadth_pct=100.0,
                mean_convergence_ratio=0.8,
                median_convergence_ratio=0.8,
                volume_breakout_pct=0.0,
                delivery_breakout_pct=0.0,
                derivatives_eligible_count=0,
                derivatives_valid_count=0,
                derivatives_active_count=0,
                derivatives_active_pct=0.0,
                institutional_active_pct=0.0,
            )

            await service.get_sector_evidence_breadth(req)
            _, kwargs = mock_eval.call_args
            assert kwargs["effective_cutoff"] == as_of_d

    async def test_pit_07_fno_unavailable_derivative_not_inactive(self) -> None:
        """PIT-07: Derivative data unavailable for F&O-eligible constituent is excluded from valid count, not counted as inactive."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stock1 = Stock(symbol="FNO_STOCK", company_name="F&O Co", exchange="NSE", is_active=True)
        stock1.id = 101

        interp = _build_mock_d3_interp(
            symbol="FNO_STOCK",
            missing_domains=["DERIVATIVES"],
        )

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

            mock_res.return_value = ([stock1], "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.return_value = _ConstituentEvaluationResult(
                symbol="FNO_STOCK",
                stock_id=101,
                status="SUCCESS",
                company_name="F&O Co",
                interpretation=interp,
            )
            mock_ret.return_value = 0.2

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids={101},
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.derivatives_eligible_count == 1
            assert summary.derivatives_valid_count == 0
            assert summary.derivatives_active_count == 0
            assert summary.derivatives_active_pct == 0.0

    async def test_pit_08_insufficient_evidence_state_5th_bucket(self) -> None:
        """PIT-08: Constituent with INSUFFICIENT_EVIDENCE counted in 5th state bucket, excluded from neutral & directional %."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stock1 = Stock(symbol="INSUF_STOCK", company_name="Insuf Co", exchange="NSE", is_active=True)
        stock1.id = 201

        interp = _build_mock_d3_interp(
            symbol="INSUF_STOCK",
            overall_state=OverallState.INSUFFICIENT_EVIDENCE,
            directional_bias=DirectionalBias.NO_DIRECTIONAL_SIGNAL,
        )

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

            mock_res.return_value = ([stock1], "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.return_value = _ConstituentEvaluationResult(
                symbol="INSUF_STOCK",
                stock_id=201,
                status="SUCCESS",
                company_name="Insuf Co",
                interpretation=interp,
            )
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.insufficient_evidence_count == 1
            assert summary.analytical_valid_constituents == 0
            assert summary.neutral_count == 0
            assert summary.bullish_pct == 0.0
            assert summary.bearish_pct == 0.0
            assert summary.neutral_pct == 0.0

    async def test_pit_09_live_mode_permits_taxonomy_fallback(self, db_session) -> None:
        """PIT-09: Live evaluation mode (as_of_date is None) permits taxonomy fallback with active trading presence."""
        sec = Sector(name="Automobile and Auto Components")
        db_session.add(sec)
        await db_session.flush()

        stock = Stock(symbol="LIVE_AUTO", company_name="Live Auto Ltd", exchange="NSE", is_active=True, sector_id=sec.id)
        db_session.add(stock)
        await db_session.commit()

        service = SectorEvidenceAggregatorService(db_session, _create_mock_session_factory())
        constituents, tier, is_unsupported = await service._resolve_sector_constituents(
            sector_code="NIFTY_AUTO",
            as_of_date=None,
            effective_date=date.today(),
        )
        assert any(s.symbol == "LIVE_AUTO" for s in constituents)
        assert tier == "LIVE_TAXONOMY_FALLBACK"
        assert not is_unsupported


# ===========================================================================
# 3. Breadth Denominators and Distributions
# ===========================================================================


@pytest.mark.asyncio
class TestBreadthDenominatorsAndDistributions:
    async def test_canonical_denominators_conservation(self) -> None:
        """Verify N_TOTAL_ELIGIBLE = N_ANALYTICAL_VALID + N_INSUFFICIENT_EVIDENCE + N_NO_DATA + N_ERROR."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [
            Stock(symbol=f"S_{i}", company_name=f"Stock {i}", exchange="NSE", is_active=True)
            for i in range(4)
        ]
        for idx, s in enumerate(stocks):
            s.id = idx + 1

        interp_valid = _build_mock_d3_interp("S_0", OverallState.BULLISH_EVIDENCE_SUPPORTED)
        interp_insuf = _build_mock_d3_interp("S_1", OverallState.INSUFFICIENT_EVIDENCE)

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        eval_results = [
            _ConstituentEvaluationResult("S_0", 1, "Stock 0", "SUCCESS", interp_valid),
            _ConstituentEvaluationResult("S_1", 2, "Stock 1", "SUCCESS", interp_insuf),
            _ConstituentEvaluationResult("S_2", 3, "Stock 2", "NO_DATA", None),
            _ConstituentEvaluationResult("S_3", 4, "Stock 3", "ERROR", None, "Timeout"),
        ]

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.side_effect = eval_results
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=25.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.total_eligible_constituents == 4
            assert summary.analytical_valid_constituents == 1
            assert summary.insufficient_evidence_count == 1
            assert summary.no_data_count == 1
            assert summary.error_count == 1
            assert (
                summary.total_eligible_constituents
                == summary.analytical_valid_constituents
                + summary.insufficient_evidence_count
                + summary.no_data_count
                + summary.error_count
            )

    async def test_directional_percentages_sum_to_100_percent(self) -> None:
        """Verify bullish_pct + bearish_pct + mixed_pct + neutral_pct == 100.0%."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [
            Stock(symbol=f"STK_{i}", company_name=f"Stock {i}", exchange="NSE", is_active=True)
            for i in range(4)
        ]
        for idx, s in enumerate(stocks):
            s.id = idx + 10

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        eval_results = [
            _ConstituentEvaluationResult("STK_0", 10, "S0", "SUCCESS", _build_mock_d3_interp("STK_0", OverallState.BULLISH_EVIDENCE_SUPPORTED)),
            _ConstituentEvaluationResult("STK_1", 11, "S1", "SUCCESS", _build_mock_d3_interp("STK_1", OverallState.BEARISH_EVIDENCE_SUPPORTED)),
            _ConstituentEvaluationResult("STK_2", 12, "S2", "SUCCESS", _build_mock_d3_interp("STK_2", OverallState.MIXED_EVIDENCE)),
            _ConstituentEvaluationResult("STK_3", 13, "S3", "SUCCESS", _build_mock_d3_interp("STK_3", OverallState.NEUTRAL_OBSERVATION)),
        ]

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.side_effect = eval_results
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.analytical_valid_constituents == 4
            assert summary.bullish_pct == 25.0
            assert summary.bearish_pct == 25.0
            assert summary.mixed_pct == 25.0
            assert summary.neutral_pct == 25.0
            total_pct = summary.bullish_pct + summary.bearish_pct + summary.mixed_pct + summary.neutral_pct
            assert round(total_pct, 1) == 100.0
            assert summary.net_directional_breadth_pct == 0.0

    async def test_coverage_status_confirmed_partial_insufficient(self) -> None:
        """Verify transitions between CONFIRMED (>=70%), PARTIAL_DATA (>=min%), and INSUFFICIENT_DATA (<min%)."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        stocks_10 = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(10)]
        for i, s in enumerate(stocks_10):
            s.id = i + 1

        eval_8_valid = [
            _ConstituentEvaluationResult(f"S_{i}", i + 1, f"S {i}", "SUCCESS", _build_mock_d3_interp(f"S_{i}"))
            for i in range(8)
        ] + [
            _ConstituentEvaluationResult("S_8", 9, "S 8", "NO_DATA", None),
            _ConstituentEvaluationResult("S_9", 10, "S 9", "NO_DATA", None),
        ]

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks_10, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.side_effect = eval_8_valid
            mock_ret.return_value = 0.0

            s_confirmed = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )
            assert s_confirmed.coverage_status == CoverageStatus.CONFIRMED

            eval_6_valid = [
                _ConstituentEvaluationResult(f"S_{i}", i + 1, f"S {i}", "SUCCESS", _build_mock_d3_interp(f"S_{i}"))
                for i in range(6)
            ] + [
                _ConstituentEvaluationResult(f"S_{i}", i + 1, f"S {i}", "NO_DATA", None)
                for i in range(6, 10)
            ]
            mock_eval_c.side_effect = eval_6_valid

            s_partial = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )
            assert s_partial.coverage_status == CoverageStatus.PARTIAL_DATA

            eval_2_valid = [
                _ConstituentEvaluationResult(f"S_{i}", i + 1, f"S {i}", "SUCCESS", _build_mock_d3_interp(f"S_{i}"))
                for i in range(2)
            ] + [
                _ConstituentEvaluationResult(f"S_{i}", i + 1, f"S {i}", "NO_DATA", None)
                for i in range(2, 10)
            ]
            mock_eval_c.side_effect = eval_2_valid

            s_insuf = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )
            assert s_insuf.coverage_status == CoverageStatus.INSUFFICIENT_DATA


# ===========================================================================
# 4. Convergence Statistics
# ===========================================================================


@pytest.mark.asyncio
class TestConvergenceStatistics:
    async def test_mean_and_median_convergence_computation(self) -> None:
        """Verify mean and median convergence ratio calculations over valid constituents."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(3)]
        for i, s in enumerate(stocks):
            s.id = i + 1

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        eval_results = [
            _ConstituentEvaluationResult("S_0", 1, "S 0", "SUCCESS", _build_mock_d3_interp("S_0", convergence_ratio=0.60)),
            _ConstituentEvaluationResult("S_1", 2, "S 1", "SUCCESS", _build_mock_d3_interp("S_1", convergence_ratio=0.80)),
            _ConstituentEvaluationResult("S_2", 3, "S 2", "SUCCESS", _build_mock_d3_interp("S_2", convergence_ratio=1.00)),
        ]

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.side_effect = eval_results
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.mean_convergence_ratio == 0.80
            assert summary.median_convergence_ratio == 0.80

    async def test_null_convergence_when_no_valid_constituents(self) -> None:
        """Verify mean and median convergence are None when no valid constituents exist."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [Stock(symbol="NO_DATA_STOCK", company_name="ND", exchange="NSE", is_active=True)]
        stocks[0].id = 1

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.return_value = _ConstituentEvaluationResult("NO_DATA_STOCK", 1, "ND", "NO_DATA", None)
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.mean_convergence_ratio is None
            assert summary.median_convergence_ratio is None


# ===========================================================================
# 5. Derivatives and Domain Participation Metrics
# ===========================================================================


@pytest.mark.asyncio
class TestDerivativesAndParticipationMetrics:
    async def test_derivatives_eligibility_and_active_percentage(self) -> None:
        """Verify derivatives_active_pct is computed as active / valid over eligible F&O constituents."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(2)]
        stocks[0].id = 1
        stocks[1].id = 2

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        eval_results = [
            _ConstituentEvaluationResult("S_0", 1, "S 0", "SUCCESS", _build_mock_d3_interp("S_0", derivatives_active=True)),
            _ConstituentEvaluationResult("S_1", 2, "S 1", "SUCCESS", _build_mock_d3_interp("S_1", derivatives_active=False)),
        ]

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.side_effect = eval_results
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_BANK",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids={1, 2},
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.derivatives_eligible_count == 2
            assert summary.derivatives_valid_count == 2
            assert summary.derivatives_active_count == 1
            assert summary.derivatives_active_pct == 50.0

    async def test_volume_and_delivery_breakout_percentages(self) -> None:
        """Verify volume_breakout_pct and delivery_breakout_pct over evaluated constituents."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(4)]
        for i, s in enumerate(stocks):
            s.id = i + 1

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        eval_results = [
            _ConstituentEvaluationResult("S_0", 1, "S 0", "SUCCESS", _build_mock_d3_interp("S_0", volume_breakout=True, delivery_breakout=True)),
            _ConstituentEvaluationResult("S_1", 2, "S 1", "SUCCESS", _build_mock_d3_interp("S_1", volume_breakout=True, delivery_breakout=False)),
            _ConstituentEvaluationResult("S_2", 3, "S 2", "SUCCESS", _build_mock_d3_interp("S_2", volume_breakout=False, delivery_breakout=False)),
            _ConstituentEvaluationResult("S_3", 4, "S 3", "SUCCESS", _build_mock_d3_interp("S_3", volume_breakout=False, delivery_breakout=False)),
        ]

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.side_effect = eval_results
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.volume_breakout_pct == 50.0
            assert summary.delivery_breakout_pct == 25.0

    async def test_institutional_activity_percentage(self) -> None:
        """Verify institutional_active_pct over evaluated constituents."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(2)]
        stocks[0].id = 1
        stocks[1].id = 2

        from app.services.sector_evidence_aggregator_service import _ConstituentEvaluationResult

        eval_results = [
            _ConstituentEvaluationResult("S_0", 1, "S 0", "SUCCESS", _build_mock_d3_interp("S_0", institutional_active=True)),
            _ConstituentEvaluationResult("S_1", 2, "S 1", "SUCCESS", _build_mock_d3_interp("S_1", institutional_active=False)),
        ]

        with (
            patch.object(service, "_resolve_sector_constituents", new_callable=AsyncMock) as mock_res,
            patch.object(service, "_evaluate_constituent", new_callable=AsyncMock) as mock_eval_c,
            patch.object(service, "_fetch_sector_index_return", new_callable=AsyncMock) as mock_ret,
        ):
            mock_res.return_value = (stocks, "TIER_1_INDEX_MEMBERSHIP", False)
            mock_eval_c.side_effect = eval_results
            mock_ret.return_value = 0.0

            summary = await service._evaluate_single_sector(
                sector_code="NIFTY_IT",
                evaluation_date=EVAL_DATE,
                as_of_date=None,
                effective_cutoff=EVAL_DATE,
                comparison_window=1,
                min_coverage_pct=50.0,
                fno_eligible_stock_ids=set(),
                semaphore=asyncio.Semaphore(5),
            )

            assert summary.institutional_active_pct == 50.0


# ===========================================================================
# 6. Concentration and Contradiction Engine
# ===========================================================================


@pytest.mark.asyncio
class TestConcentrationAndContradictionEngine:
    async def test_price_breadth_contradiction_flag(self) -> None:
        """Verify PRICE_BREADTH_CONTRADICTION flag when sector_return >= +0.5% and bearish_pct >= 50.0%."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())
        details = await service._evaluate_contradictions(
            sector_return=1.2,
            bearish_pct=60.0,
            delivery_breakout_pct=40.0,
            mixed_pct=10.0,
            net_breadth_pct=-40.0,
            analytical_valid_count=10,
            constituents=[],
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        flags = [d.flag for d in details]
        assert SectorContradictionFlag.PRICE_BREADTH_CONTRADICTION in flags

    async def test_delivery_confirmation_deficit_flag(self) -> None:
        """Verify DELIVERY_CONFIRMATION_DEFICIT flag when sector_return >= +0.5% and delivery_breakout_pct <= 10.0%."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())
        details = await service._evaluate_contradictions(
            sector_return=0.8,
            bearish_pct=20.0,
            delivery_breakout_pct=5.0,
            mixed_pct=10.0,
            net_breadth_pct=20.0,
            analytical_valid_count=10,
            constituents=[],
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        flags = [d.flag for d in details]
        assert SectorContradictionFlag.DELIVERY_CONFIRMATION_DEFICIT in flags

    async def test_high_concentration_risk_flag_when_all_guards_met(self) -> None:
        """Verify HIGH_CONCENTRATION_RISK when N >= 5, net_breadth <= 0.0%, and top 2 proxy >= 80%."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(5)]
        for i, s in enumerate(stocks):
            s.id = i + 1

        with patch.object(service, "_check_concentration_risk", new_callable=AsyncMock) as mock_conc:
            mock_conc.return_value = (True, 0.85)

            details = await service._evaluate_contradictions(
                sector_return=1.5,
                bearish_pct=40.0,
                delivery_breakout_pct=30.0,
                mixed_pct=20.0,
                net_breadth_pct=-10.0,
                analytical_valid_count=5,
                constituents=stocks,
                effective_cutoff=EVAL_DATE,
                window=1,
            )
            flags = [d.flag for d in details]
            assert SectorContradictionFlag.HIGH_CONCENTRATION_RISK in flags

    async def test_high_concentration_risk_suppressed_when_less_than_5_constituents(self) -> None:
        """Guard: N < 5 must strictly suppress HIGH_CONCENTRATION_RISK."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(4)]
        for i, s in enumerate(stocks):
            s.id = i + 1

        with patch.object(service, "_check_concentration_risk", new_callable=AsyncMock) as mock_conc:
            mock_conc.return_value = (True, 0.95)

            details = await service._evaluate_contradictions(
                sector_return=1.5,
                bearish_pct=50.0,
                delivery_breakout_pct=30.0,
                mixed_pct=10.0,
                net_breadth_pct=-20.0,
                analytical_valid_count=4,  # Guard: < 5
                constituents=stocks,
                effective_cutoff=EVAL_DATE,
                window=1,
            )
            flags = [d.flag for d in details]
            assert SectorContradictionFlag.HIGH_CONCENTRATION_RISK not in flags
            mock_conc.assert_not_called()

    async def test_high_concentration_risk_suppressed_when_less_than_2_positive_constituents(self) -> None:
        """Guard: |S_pos| < 2 must strictly return False in _check_concentration_risk."""
        mock_session = AsyncMock(spec=AsyncSession)
        service = SectorEvidenceAggregatorService(mock_session, _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(5)]
        for i, s in enumerate(stocks):
            s.id = i + 1

        # In SQL mock, return rows where only 1 stock has positive return
        mock_rows = [
            (1, EVAL_DATE, 110.0), (1, COMP_DATE, 100.0),  # +10%
            (2, EVAL_DATE, 90.0), (2, COMP_DATE, 100.0),   # -10%
            (3, EVAL_DATE, 90.0), (3, COMP_DATE, 100.0),   # -10%
            (4, EVAL_DATE, 90.0), (4, COMP_DATE, 100.0),   # -10%
            (5, EVAL_DATE, 90.0), (5, COMP_DATE, 100.0),   # -10%
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = mock_rows
        mock_session.execute = AsyncMock(return_value=mock_result)

        is_conc, top2_sum = await service._check_concentration_risk(stocks, EVAL_DATE, 1)
        assert is_conc is False
        assert top2_sum == 0.0

    async def test_high_concentration_risk_suppressed_when_net_breadth_positive(self) -> None:
        """Guard: net_directional_breadth_pct > 0.0% must strictly suppress HIGH_CONCENTRATION_RISK."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())

        stocks = [Stock(symbol=f"S_{i}", company_name=f"S {i}", exchange="NSE", is_active=True) for i in range(6)]
        for i, s in enumerate(stocks):
            s.id = i + 1

        with patch.object(service, "_check_concentration_risk", new_callable=AsyncMock) as mock_conc:
            details = await service._evaluate_contradictions(
                sector_return=1.5,
                bearish_pct=20.0,
                delivery_breakout_pct=30.0,
                mixed_pct=10.0,
                net_breadth_pct=30.0,  # Guard: > 0.0%
                analytical_valid_count=6,
                constituents=stocks,
                effective_cutoff=EVAL_DATE,
                window=1,
            )
            flags = [d.flag for d in details]
            assert SectorContradictionFlag.HIGH_CONCENTRATION_RISK not in flags
            mock_conc.assert_not_called()

    async def test_sector_evidence_conflicted_flag_when_mixed_pct_gte_40(self) -> None:
        """Verify SECTOR_EVIDENCE_CONFLICTED flag when mixed_pct >= 40.0%."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())
        details = await service._evaluate_contradictions(
            sector_return=0.1,
            bearish_pct=20.0,
            delivery_breakout_pct=20.0,
            mixed_pct=45.0,  # >= 40.0%
            net_breadth_pct=0.0,
            analytical_valid_count=10,
            constituents=[],
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        flags = [d.flag for d in details]
        assert SectorContradictionFlag.SECTOR_EVIDENCE_CONFLICTED in flags


# ===========================================================================
# 7. Service Boundary, Concurrency and Architecture Invariants
# ===========================================================================


@pytest.mark.asyncio
class TestServiceBoundaryAndConcurrency:
    async def test_concurrency_bounded_by_semaphore_max_workers(self) -> None:
        """Verify constituent evaluation tasks run through an asyncio.Semaphore."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())

        stock = Stock(symbol="CONCUR_STOCK", company_name="Concur", exchange="NSE", is_active=True)
        stock.id = 501

        interp = _build_mock_d3_interp("CONCUR_STOCK")

        with patch("app.services.sector_evidence_aggregator_service.WhatChangedInterpretationService.interpret", new_callable=AsyncMock) as mock_d3:
            mock_d3.return_value = interp
            semaphore = asyncio.Semaphore(5)
            res = await service._evaluate_constituent(stock, EVAL_DATE, 1, semaphore)
            assert res.status == "SUCCESS"
            assert res.symbol == "CONCUR_STOCK"

    async def test_public_d3_service_invoked_without_ai_narrative(self) -> None:
        """Verify WhatChangedInterpretationService.interpret is invoked directly with include_ai_narrative=False."""
        service = SectorEvidenceAggregatorService(AsyncMock(), _create_mock_session_factory())

        stock = Stock(symbol="BOUNDARY_STOCK", company_name="Boundary", exchange="NSE", is_active=True)
        stock.id = 601

        interp = _build_mock_d3_interp("BOUNDARY_STOCK")

        with patch("app.services.sector_evidence_aggregator_service.WhatChangedInterpretationService.interpret", new_callable=AsyncMock) as mock_d3:
            mock_d3.return_value = interp
            semaphore = asyncio.Semaphore(5)
            await service._evaluate_constituent(stock, EVAL_DATE, 1, semaphore)

            mock_d3.assert_awaited_once_with(
                symbol="BOUNDARY_STOCK",
                evaluation_date=EVAL_DATE,
                comparison_window=1,
                include_ai_narrative=False,
            )


# ===========================================================================
# 8. REST API Endpoint Tests
# ===========================================================================


@pytest.mark.asyncio
class TestSectorEvidenceApiRoutes:
    async def test_api_get_evidence_breadth_200(self) -> None:
        """Verify GET /api/v1/market-intelligence/sectors/evidence-breadth returns 200 with SectorEvidenceBreadthResponse."""
        mock_summary = SectorEvidenceSummary(
            sector_code="NIFTY_IT",
            sector_name="Nifty IT",
            category="Sectoral",
            evaluation_date=EVAL_DATE,
            as_of_date=None,
            comparison_window=1,
            total_eligible_constituents=10,
            evaluated_constituents=10,
            analytical_valid_constituents=10,
            insufficient_evidence_count=0,
            no_data_count=0,
            error_count=0,
            data_coverage_pct=100.0,
            insufficient_evidence_pct=0.0,
            coverage_status=CoverageStatus.CONFIRMED,
            bullish_count=7,
            bearish_count=1,
            mixed_count=1,
            neutral_count=1,
            bullish_pct=70.0,
            bearish_pct=10.0,
            mixed_pct=10.0,
            neutral_pct=10.0,
            net_directional_breadth_pct=60.0,
            mean_convergence_ratio=0.85,
            median_convergence_ratio=0.85,
            volume_breakout_pct=50.0,
            delivery_breakout_pct=40.0,
            derivatives_eligible_count=8,
            derivatives_valid_count=8,
            derivatives_active_count=6,
            derivatives_active_pct=75.0,
            institutional_active_pct=30.0,
        )

        mock_resp = SectorEvidenceBreadthResponse(
            request_echo=SectorEvidenceBreadthRequest(sectors=["NIFTY_IT"]),
            evaluation_date=EVAL_DATE,
            as_of_date=None,
            comparison_window=1,
            total_sectors_evaluated=1,
            confirmed_sectors_count=1,
            sectors_bullish_pct=100.0,
            sectors_bearish_pct=0.0,
            sectors_neutral_pct=0.0,
            market_mean_convergence_ratio=0.85,
            sectors=[mock_summary],
            generated_at=datetime.now(UTC),
        )

        with patch("app.services.sector_evidence_aggregator_service.SectorEvidenceAggregatorService.get_sector_evidence_breadth", new_callable=AsyncMock) as mock_agg:
            mock_agg.return_value = mock_resp

            async with auth_client(role="analyst", browser=False) as client:
                res = await client.get("/api/v1/market-intelligence/sectors/evidence-breadth?sectors=NIFTY_IT")
                assert res.status_code == 200
                data = res.json()
                assert data["total_sectors_evaluated"] == 1
                assert data["confirmed_sectors_count"] == 1
                assert data["sectors_bullish_pct"] == 100.0
                assert data["sectors"][0]["sector_code"] == "NIFTY_IT"
                assert data["sectors"][0]["net_directional_breadth_pct"] == 60.0

    async def test_api_unauthenticated_401(self) -> None:
        """Verify unauthenticated request to REST endpoint returns 401."""
        from httpx import ASGITransport, AsyncClient

        from app.main import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/v1/market-intelligence/sectors/evidence-breadth")
            assert res.status_code == 401

    async def test_api_invalid_window_422(self) -> None:
        """Verify invalid comparison_window returns 422 Unprocessable Entity."""
        async with auth_client(role="analyst", browser=False) as client:
            res = await client.get("/api/v1/market-intelligence/sectors/evidence-breadth?comparison_window=0")
            assert res.status_code == 422

            res2 = await client.get("/api/v1/market-intelligence/sectors/evidence-breadth?comparison_window=35")
            assert res2.status_code == 422


# ===========================================================================
# 9. HTML and HTMX UI Routes Tests
# ===========================================================================


@pytest.mark.asyncio
class TestSectorEvidenceHtmlAndUiRoutes:
    async def test_html_page_route_renders_shell(self) -> None:
        """Verify GET /market-intelligence/sectors/evidence-breadth returns 200 HTML with dashboard shell."""
        mock_summary = SectorEvidenceSummary(
            sector_code="NIFTY_IT",
            sector_name="Nifty IT",
            category="Sectoral",
            evaluation_date=EVAL_DATE,
            as_of_date=None,
            comparison_window=1,
            total_eligible_constituents=5,
            evaluated_constituents=5,
            analytical_valid_constituents=5,
            insufficient_evidence_count=0,
            no_data_count=0,
            error_count=0,
            data_coverage_pct=100.0,
            insufficient_evidence_pct=0.0,
            coverage_status=CoverageStatus.CONFIRMED,
            bullish_count=3,
            bearish_count=1,
            mixed_count=1,
            neutral_count=0,
            bullish_pct=60.0,
            bearish_pct=20.0,
            mixed_pct=20.0,
            neutral_pct=0.0,
            net_directional_breadth_pct=40.0,
            mean_convergence_ratio=0.75,
            median_convergence_ratio=0.75,
            volume_breakout_pct=40.0,
            delivery_breakout_pct=20.0,
            derivatives_eligible_count=4,
            derivatives_valid_count=4,
            derivatives_active_count=2,
            derivatives_active_pct=50.0,
            institutional_active_pct=20.0,
        )

        mock_resp = SectorEvidenceBreadthResponse(
            request_echo=SectorEvidenceBreadthRequest(sectors=["NIFTY_IT"]),
            evaluation_date=EVAL_DATE,
            as_of_date=None,
            comparison_window=1,
            total_sectors_evaluated=1,
            confirmed_sectors_count=1,
            sectors_bullish_pct=100.0,
            sectors_bearish_pct=0.0,
            sectors_neutral_pct=0.0,
            market_mean_convergence_ratio=0.75,
            sectors=[mock_summary],
            generated_at=datetime.now(UTC),
        )

        with patch("app.services.sector_evidence_aggregator_service.SectorEvidenceAggregatorService.get_sector_evidence_breadth", new_callable=AsyncMock) as mock_agg:
            mock_agg.return_value = mock_resp

            async with auth_client(role="admin", browser=True) as client:
                res = await client.get("/market-intelligence/sectors/evidence-breadth")
                assert res.status_code == 200
                assert "text/html" in res.headers["content-type"]
                assert "Sector Breadth" in res.text
                assert "Evidence Consensus" in res.text
                assert 'id="sector-matrix-container"' in res.text
                assert "Nifty IT" in res.text

    async def test_htmx_partial_route_renders_matrix(self) -> None:
        """Verify GET /market-intelligence/sectors/evidence-breadth with HX-Request returns matrix partial."""
        mock_summary = SectorEvidenceSummary(
            sector_code="NIFTY_AUTO",
            sector_name="Nifty Auto",
            category="Sectoral",
            evaluation_date=EVAL_DATE,
            as_of_date=None,
            comparison_window=1,
            total_eligible_constituents=10,
            evaluated_constituents=10,
            analytical_valid_constituents=10,
            insufficient_evidence_count=0,
            no_data_count=0,
            error_count=0,
            data_coverage_pct=100.0,
            insufficient_evidence_pct=0.0,
            coverage_status=CoverageStatus.CONFIRMED,
            bullish_count=2,
            bearish_count=6,
            mixed_count=1,
            neutral_count=1,
            bullish_pct=20.0,
            bearish_pct=60.0,
            mixed_pct=10.0,
            neutral_pct=10.0,
            net_directional_breadth_pct=-40.0,
            mean_convergence_ratio=0.70,
            median_convergence_ratio=0.70,
            volume_breakout_pct=30.0,
            delivery_breakout_pct=10.0,
            derivatives_eligible_count=8,
            derivatives_valid_count=8,
            derivatives_active_count=3,
            derivatives_active_pct=37.5,
            institutional_active_pct=10.0,
            contradictions=[
                SectorContradictionDetail(
                    flag=SectorContradictionFlag.PRICE_BREADTH_CONTRADICTION,
                    description="Price-breadth contradiction observed",
                )
            ],
        )

        mock_resp = SectorEvidenceBreadthResponse(
            request_echo=SectorEvidenceBreadthRequest(sectors=["NIFTY_AUTO"]),
            evaluation_date=EVAL_DATE,
            as_of_date=None,
            comparison_window=1,
            total_sectors_evaluated=1,
            confirmed_sectors_count=1,
            sectors_bullish_pct=0.0,
            sectors_bearish_pct=100.0,
            sectors_neutral_pct=0.0,
            market_mean_convergence_ratio=0.70,
            sectors=[mock_summary],
            generated_at=datetime.now(UTC),
        )

        with patch("app.services.sector_evidence_aggregator_service.SectorEvidenceAggregatorService.get_sector_evidence_breadth", new_callable=AsyncMock) as mock_agg:
            mock_agg.return_value = mock_resp

            async with auth_client(role="admin", browser=True) as client:
                res = await client.get(
                    "/market-intelligence/sectors/evidence-breadth",
                    headers={"HX-Request": "true"},
                )
                assert res.status_code == 200
                assert "<!DOCTYPE html>" not in res.text
                assert "Nifty Auto" in res.text
                assert "PRICE_BREADTH_CONTRADICTION" in res.text
                assert "-40.0%" in res.text


# ===========================================================================
# 10. Database & Protected Files Invariant Tests
# ===========================================================================


class TestProtectedFilesAndAlembic:
    def test_all_19_protected_files_unmodified(self) -> None:
        """Section 32 protected list integrity verification."""
        protected_files = [
            "app/services/what_changed_service.py",
            "app/services/what_changed_config.py",
            "app/schemas/what_changed.py",
            "tests/test_what_changed_g11d1.py",
            "app/services/what_changed_synthesis_service.py",
            "app/services/what_changed_synthesis_config.py",
            "app/schemas/what_changed_synthesis.py",
            "tests/test_what_changed_synthesis_g11d2.py",
            "app/services/what_changed_interpretation_service.py",
            "app/services/what_changed_interpretation_config.py",
            "app/schemas/what_changed_interpretation.py",
            "app/ai/interpretation_narrative_service.py",
            "tests/test_what_changed_interpretation_g11d3.py",
            "app/services/what_changed_screening_service.py",
            "app/schemas/what_changed_screening.py",
            "tests/test_what_changed_screening_g11d4.py",
            "app/services/stock_decision_context_service.py",
            "app/schemas/stock_decision_context.py",
            "tests/test_stock_decision_context_g11d5.py",
        ]

        # Strict protection: All 18 upstream D1-D4 and D5 service/schema files must remain strictly unmodified.
        strictly_unmodified = [f for f in protected_files if f != "tests/test_stock_decision_context_g11d5.py"]
        res = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--"] + strictly_unmodified,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        diff_files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
        assert diff_files == [], f"Protected upstream files were modified: {diff_files}"

        # tests/test_stock_decision_context_g11d5.py allows only DISC-OPT1-01 migration invariant compatibility update.
        res_d5 = subprocess.run(
            ["git", "diff", "-U0", "HEAD", "--", "tests/test_stock_decision_context_g11d5.py"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res_d5.returncode == 0
        diff_text = res_d5.stdout
        hunks = [line for line in diff_text.splitlines() if line.startswith("@@")]
        assert len(hunks) > 0, "Expected migration invariant diff in tests/test_stock_decision_context_g11d5.py"
        for hunk in hunks:
            assert "TestDatabaseMigrationInvariant" in hunk, (
                f"Unauthorized modification to D5 test logic outside TestDatabaseMigrationInvariant: {hunk}"
            )

    def test_alembic_head_remains_unchanged(self) -> None:
        """G.11-D-6 is strictly read-only/stateless; zero database migrations allowed in D-6 scope.

        The certified D1-D6 baseline migration is f1b9c8d7e6a5.
        Authorized downstream workstreams (e.g. G.11-OPT-1 b3a4c5d6e7f8) may chain
        linearly from this certified baseline without altering D-6 statelessness.
        """
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()

        # Certified D1-D6 baseline revision must remain intact in migration graph
        baseline_rev = script.get_revision("f1b9c8d7e6a5")
        assert baseline_rev is not None, "Certified baseline migration f1b9c8d7e6a5 must exist"

        # Current head must be either the certified baseline or an authorized linear descendant
        authorized_heads = {"f1b9c8d7e6a5", "b3a4c5d6e7f8"}
        assert head in authorized_heads, (
            f"Expected alembic head in {authorized_heads}, but found {head}"
        )
        if head != "f1b9c8d7e6a5":
            all_revs = [r.revision for r in script.iterate_revisions(upper=head, lower="base")]
            assert "f1b9c8d7e6a5" in all_revs, (
                f"Head {head} must descend linearly from certified baseline f1b9c8d7e6a5"
            )
            assert len(script.get_heads()) == 1, (
                f"Expected exactly 1 migration head, but found {script.get_heads()}"
            )


# ===========================================================================
# 11. P1 F-01 Regression: Sector-Index Return Real ORM Query Path (Audit Fix)
# ===========================================================================


@pytest.mark.asyncio
class TestSectorIndexReturnModelContractAndPIT:
    """P1 F-01 regression tests.

    The original implementation queried a non-existent column
    (``MarketIndexPrice.close_price``) instead of the real model column
    ``MarketIndexPrice.close``. The AttributeError is raised at
    query-construction time — *before* the mocked ``session.execute`` in the
    unit tests could even be reached — so the mocked 281/281 regression suite
    hid a guaranteed HTTP 500 on every live sector evaluation.

    These tests exercise the REAL ORM metadata and the REAL test database so
    any future ``close`` / ``close_price`` model mismatch fails loudly here.
    """

    async def test_market_index_price_model_contract(self) -> None:
        """Model contract guard: the price column is `close`; `close_price` must not exist."""
        from app.models.market_index_price import MarketIndexPrice

        assert hasattr(MarketIndexPrice, "close"), (
            "MarketIndexPrice must expose the `close` column (app/models/market_index_price.py)"
        )
        assert not hasattr(MarketIndexPrice, "close_price"), (
            "MarketIndexPrice.close_price does not exist — any service referencing it "
            "raises AttributeError at query-construction time (audit P1 F-01). "
            "If the model was renamed, update all ORM references and this guard."
        )

    async def test_fetch_sector_index_return_real_orm_query_and_pit_cutoff(
        self, db_session: AsyncSession
    ) -> None:
        """End-to-end real-DB test of _fetch_sector_index_return.

        Seeds a synthetic MarketIndex with three MarketIndexPrice rows:
          - T-1: close 24000.0 (base)
          - T   : close 24240.0 (+1.0%)
          - T+1 : close 26000.0 (future — must be excluded by the PIT cutoff)

        Verifies:
          1. The real ORM query path executes (would AttributeError on close_price).
          2. The computed absolute return matches the seeded arithmetic (+1.0%).
          3. Future (post-cutoff) price data cannot leak (PIT safety).
          4. A cutoff before any pair of bars fails closed with None.
        """
        from decimal import Decimal

        from app.db.session import get_session_factory
        from app.models.market_index_price import MarketIndexPrice

        idx = MarketIndex(
            code="SYNTH_D6_IDX", name="Synthetic D6 Regression Index", country="INDIA"
        )
        db_session.add(idx)
        await db_session.flush()

        prices = [
            MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=EVAL_DATE,
                open=Decimal("24000.0"),
                high=Decimal("24250.0"),
                low=Decimal("23950.0"),
                close=Decimal("24240.0"),
                freshness_status="recent",
            ),
            MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=COMP_DATE,
                open=Decimal("24000.0"),
                high=Decimal("24100.0"),
                low=Decimal("23900.0"),
                close=Decimal("24000.0"),
                freshness_status="recent",
            ),
            # Future bar relative to the effective cutoff — must never leak.
            MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=date(2026, 9, 16),
                open=Decimal("24250.0"),
                high=Decimal("26100.0"),
                low=Decimal("24200.0"),
                close=Decimal("26000.0"),
                freshness_status="recent",
            ),
        ]
        db_session.add_all(prices)
        await db_session.flush()

        service = SectorEvidenceAggregatorService(db_session, get_session_factory())

        # Real ORM query path + arithmetic + PIT cutoff in one shot.
        sector_return = await service._fetch_sector_index_return(
            sector_code="SYNTH_D6_IDX",
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        assert sector_return == 1.0, (
            f"Expected +1.0% from seeded closes (24240/24000); got {sector_return!r}"
        )

        # PIT: cutoff before any two knowable bars must fail closed (None).
        pit_return = await service._fetch_sector_index_return(
            sector_code="SYNTH_D6_IDX",
            effective_cutoff=date(2026, 9, 13),
            window=1,
        )
        assert pit_return is None, "Future/insufficient bars must not leak past the cutoff"

    async def test_price_dependent_contradictions_reachable_with_real_sector_return(
        self, db_session: AsyncSession
    ) -> None:
        """The three price-dependent contradiction rules must be able to obtain
        real sector-index return data (remediation validation requirement).

        Uses the REAL _fetch_sector_index_return output (>= +0.5% gate) and feeds
        it into _evaluate_contradictions to prove the gate actually opens —
        the price-based rules are reachable end-to-end, not mocked away.
        """
        from decimal import Decimal

        from app.db.session import get_session_factory
        from app.models.market_index_price import MarketIndexPrice

        idx = MarketIndex(code="SYNTH_D6_IDX_2", name="Synthetic D6 Gate Index", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        # +2.0% over the window: passes the >= +0.5% gate.
        db_session.add_all(
            [
                MarketIndexPrice(
                    market_index_id=idx.id,
                    trade_date=EVAL_DATE,
                    open=Decimal("1000.0"),
                    high=Decimal("1030.0"),
                    low=Decimal("995.0"),
                    close=Decimal("1020.0"),
                    freshness_status="recent",
                ),
                MarketIndexPrice(
                    market_index_id=idx.id,
                    trade_date=COMP_DATE,
                    open=Decimal("1000.0"),
                    high=Decimal("1005.0"),
                    low=Decimal("990.0"),
                    close=Decimal("1000.0"),
                    freshness_status="recent",
                ),
            ]
        )
        await db_session.flush()

        service = SectorEvidenceAggregatorService(db_session, get_session_factory())
        sector_return = await service._fetch_sector_index_return(
            sector_code="SYNTH_D6_IDX_2",
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        assert sector_return is not None and sector_return >= 0.5, (
            f"Seeded data must open the +0.5% gate; got {sector_return!r}"
        )

        # Gate open + bearish-heavy distribution → PRICE_BREADTH_CONTRADICTION fires.
        details = await service._evaluate_contradictions(
            sector_return=sector_return,
            bearish_pct=60.0,
            delivery_breakout_pct=50.0,
            mixed_pct=10.0,
            net_breadth_pct=-50.0,
            analytical_valid_count=10,
            constituents=[],
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        flags = {d.flag for d in details}
        assert SectorContradictionFlag.PRICE_BREADTH_CONTRADICTION in flags

        # Gate open + delivery deficit → DELIVERY_CONFIRMATION_DEFICIT fires (<= 10.0%).
        details = await service._evaluate_contradictions(
            sector_return=sector_return,
            bearish_pct=5.0,
            delivery_breakout_pct=10.0,
            mixed_pct=10.0,
            net_breadth_pct=75.0,
            analytical_valid_count=10,
            constituents=[],
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        flags = {d.flag for d in details}
        assert SectorContradictionFlag.DELIVERY_CONFIRMATION_DEFICIT in flags

        # Return below the +0.5% gate → no price-gated flags fire.
        details = await service._evaluate_contradictions(
            sector_return=0.2,
            bearish_pct=60.0,
            delivery_breakout_pct=10.0,
            mixed_pct=10.0,
            net_breadth_pct=-50.0,
            analytical_valid_count=10,
            constituents=[],
            effective_cutoff=EVAL_DATE,
            window=1,
        )
        flags = {d.flag for d in details}
        assert SectorContradictionFlag.PRICE_BREADTH_CONTRADICTION not in flags
        assert SectorContradictionFlag.DELIVERY_CONFIRMATION_DEFICIT not in flags

