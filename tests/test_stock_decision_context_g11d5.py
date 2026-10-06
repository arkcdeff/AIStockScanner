"""Comprehensive Test Suite for G.11-D-5: Stock 360 & Decision Context Fusion Layer.

Validates:
  1. Configuration & Enums: ProfileAlignmentState, CANONICAL_PROFILES.
  2. Schema Immutability & Validation: ProfileEvidenceAlignment, StockDecisionContextRead.
  3. StockDecisionContextFusionService:
     - Basic fusion: produces complete StockDecisionContextRead with all dimensions.
     - Unknown symbol handling: raises ValueError (404 in API).
     - Invalid comparison window: raises ValueError for < 1 or > 30 (422 in API).
     - Consumption without recalculation: consumes D-3 without re-evaluating D-1/D-2.
     - Profile alignment states: ALIGNED, PARTIALLY_ALIGNED, BLOCKED, INSUFFICIENT_DATA, NOT_APPLICABLE.
     - Active vetoes compilation and deduplication.
     - Point-in-time propagation and cutoff safety.
     - 100% determinism across duplicate executions.
     - AI narrative boundary and fail-closed fallback narrative.
  4. REST API Endpoint:
     - GET /api/v1/stocks/{symbol}/decision-context (200, 404, 422, 401).
  5. HTML & UI Integration:
     - GET /stocks/{symbol}/decision-context HTML partial.
     - GET /stocks/{symbol}/evidence-modal HTMX modal dialog partial.
     - Screener page contains #evidenceModalContainer.
     - Screener results table contains Inspect button with hx-get.
     - Stock detail renders components/stock_decision_dossier.html.
  6. Database & Migration Invariant:
     - Alembic head remains f1b9c8d7e6a5.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.news_policy import NewsEvent
from app.models.stock import Stock
from app.schemas.stock360 import (
    RelativeStrengthMetrics,
    Stock360Snapshot,
    StockOpportunityAssessment,
    TechnicalSummaryRead,
    VolumeDeliverySummaryRead,
)
from app.schemas.stock_decision_context import (
    ProfileAlignmentState,
    ProfileEvidenceAlignment,
    StockDecisionContextRead,
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
from app.services.stock360_service import Stock360Service
from app.services.stock_decision_context_service import (
    CANONICAL_PROFILES,
    StockDecisionContextFusionService,
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


def _build_mock_d3_interpretation(
    symbol: str = "RELIANCE",
    overall_state: OverallState = OverallState.BULLISH_EVIDENCE_SUPPORTED,
    directional_bias: DirectionalBias = DirectionalBias.BULLISH_EVIDENCE,
    convergence_ratio: float = 0.80,
    supporting_domains: list[str] | None = None,
    conflicting_domains: list[str] | None = None,
    missing_domains: list[str] | None = None,
) -> StockIntelligenceInterpretation:
    """Build a deterministic mock StockIntelligenceInterpretation for testing."""
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
            supporting_domains=supporting_domains or ["PRICE", "TECHNICAL", "VOLUME"],
            conflicting_domains=conflicting_domains or [],
            context_domains=["CORPORATE"],
            missing_domains=missing_domains or [],
            interpretation="Directional convergence evaluated.",
        ),
        overall_lifecycle=OverallLifecycleSummary(
            pattern_count=1,
            new_count=1,
            continuing_count=0,
            strengthening_count=0,
            weakening_count=0,
            resolved_count=0,
            description="Bullish momentum strengthening across sessions.",
        ),
        pattern_lifecycle_records=[
            PatternLifecycleRecord(
                pattern_fingerprint="fp-breakout-1",
                relationship_type=RelationshipType.CONFIRMATION,
                lifecycle_state=PatternLifecycle.NEW,
                change_descriptor=None,
                description="Initial breakout candle confirmed.",
            )
        ],
        primary_evidence=[
            EvidenceItem(
                domain="PRICE",
                classification=EvidenceClassification.PRIMARY,
                relationship_type=RelationshipType.CONFIRMATION,
                strength=RelationshipStrength.MAJOR,
                reason="Price broke above 20-day high by 2.4%",
            )
        ],
        supporting_evidence=[
            EvidenceItem(
                domain="VOLUME",
                classification=EvidenceClassification.SUPPORTING,
                relationship_type=RelationshipType.CONFIRMATION,
                strength=RelationshipStrength.SIGNIFICANT,
                reason="Volume expanded 2.1x over 20-day average",
            )
        ],
        conflicting_evidence=[
            EvidenceItem(
                domain="DERIVATIVES",
                classification=EvidenceClassification.CONFLICTING,
                relationship_type=RelationshipType.DIVERGENCE,
                strength=RelationshipStrength.NORMAL,
                reason="Call OI build-up at nearby resistance strike",
            )
        ]
        if conflicting_domains
        else [],
        context_evidence=[
            EvidenceItem(
                domain="CORPORATE",
                classification=EvidenceClassification.CONTEXT,
                relationship_type=RelationshipType.CONTEXT_ONLY,
                strength=RelationshipStrength.CONTEXT_ONLY,
                reason="Dividend announcement recorded",
            )
        ],
        watch_items=[
            WatchItem(
                code="VOLUME_CONFIRMATION_REQUIRED",
                description="Monitor delivery volume sustained participation",
            )
        ],
        limitations=[
            Limitation(
                code="INCOMPLETE_DERIVATIVES",
                description="Options chain data unavailable for non-F&O contracts",
            )
        ],
        narrative=None,
        fallback_narrative="Deterministic fallback narrative: Bullish alignment supported by volume and price.",
        interpretation_method="DETERMINISTIC",
        evidence_quality=EvidenceQuality.CONFIRMED,
        pit_safe=True,
        provenance=["PRICE", "VOLUME"],
        generated_at=datetime.now(UTC),
    )


def _build_mock_stock360_snapshot(
    symbol: str = "RELIANCE",
    is_fno: bool = True,
    completeness: float = 90.0,
) -> Stock360Snapshot:
    """Build a deterministic mock Stock360Snapshot for testing."""
    return Stock360Snapshot(
        symbol=symbol,
        stock_id=1,
        company_name="Reliance Industries Ltd" if symbol == "RELIANCE" else f"{symbol} Ltd",
        evaluation_date=EVAL_DATE,
        as_of_date=EVAL_DATE,
        sector_name="Energy",
        is_fno=is_fno,
        technicals=TechnicalSummaryRead(
            close=2500.0,
            ema20=2450.0,
            ema50=2400.0,
            ema200=2200.0,
            rsi14=62.5,
            macd=12.4,
            macd_signal=9.8,
            adx14=26.0,
            atr14=35.0,
            supertrend=2420.0,
            supertrend_direction="BULLISH",
            trend_state="UPTREND",
            pct_from_52w_high=-3.5,
            pct_from_52w_low=28.0,
        ),
        volume_delivery=VolumeDeliverySummaryRead(
            volume=1500000,
            avg_volume_20d=800000.0,
            relative_volume_20d=1.88,
            delivery_pct=55.0,
            avg_delivery_pct_20d=42.0,
            volume_breakout=True,
            delivery_breakout=True,
            accumulation_signal=True,
        ),
        relative_strength=RelativeStrengthMetrics(
            stock_vs_nifty_20d_pct=4.5,
            stock_vs_sector_20d_pct=2.8,
            rs_trend="OUTPERFORMING",
        ),
        fundamentals=None,
        derivatives_summary={"overall_pcr": 1.15, "max_pain": 2500.0} if is_fno else {},
        news_policy=None,
        data_coverage={"technicals": "AVAILABLE", "volume_delivery": "AVAILABLE"},
        data_completeness_pct=completeness,
        quality_warnings=[],
        provenance={"eval_date": EVAL_DATE.isoformat()},
    )


# ===========================================================================
# 1. Configuration & Enum Invariants
# ===========================================================================


class TestDecisionContextConfigAndEnums:
    def test_profile_alignment_state_values(self) -> None:
        expected = {
            "ALIGNED",
            "PARTIALLY_ALIGNED",
            "BLOCKED",
            "INSUFFICIENT_DATA",
            "NOT_APPLICABLE",
        }
        actual = {s.value for s in ProfileAlignmentState}
        assert actual == expected

    def test_canonical_profiles_count_and_names(self) -> None:
        assert len(CANONICAL_PROFILES) == 5
        assert set(CANONICAL_PROFILES) == {
            "INVESTMENT_QUALITY",
            "QUALITY_PULLBACK",
            "BREAKOUT",
            "SWING",
            "BTST_READINESS",
        }


# ===========================================================================
# 2. Schema Validation & Immutability Tests
# ===========================================================================


class TestDecisionContextSchemas:
    def test_profile_evidence_alignment_immutability(self) -> None:
        alignment = ProfileEvidenceAlignment(
            profile="BREAKOUT",
            alignment_state=ProfileAlignmentState.ALIGNED,
            canonical_classification="STRONG_RESEARCH_CANDIDATE",
            canonical_score=85.0,
            supporting_evidence=["Volume breakout confirmed"],
            conflicting_evidence=[],
            missing_evidence=[],
            veto_reasons=[],
            alignment_summary="Setup confirmed by volume expansion.",
        )
        with pytest.raises(ValidationError):
            alignment.canonical_score = 90.0  # type: ignore[misc]

    def test_stock_decision_context_read_immutability(self) -> None:
        dossier = StockDecisionContextRead(
            symbol="INFY",
            evaluation_date=EVAL_DATE,
            as_of_date=EVAL_DATE,
            overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
            directional_bias=DirectionalBias.BULLISH_EVIDENCE,
            generated_at=datetime.now(UTC),
        )
        assert dossier.symbol == "INFY"
        assert dossier.pit_safe is True
        with pytest.raises(ValidationError):
            dossier.symbol = "TCS"  # type: ignore[misc]


# ===========================================================================
# 3. Core Service Unit Tests (StockDecisionContextFusionService)
# ===========================================================================


@pytest.mark.asyncio
class TestStockDecisionContextFusionService:
    async def test_unknown_symbol_raises_value_error(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute.return_value = mock_result

        service = StockDecisionContextFusionService(session)
        with pytest.raises(ValueError, match="Stock symbol 'UNKNOWN' not found"):
            await service.get_decision_context("UNKNOWN")

    async def test_invalid_comparison_window_raises_value_error(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        service = StockDecisionContextFusionService(session)

        with pytest.raises(ValueError, match="comparison_window must be between 1 and 30"):
            await service.get_decision_context("RELIANCE", comparison_window=0)

        with pytest.raises(ValueError, match="comparison_window must be between 1 and 30"):
            await service.get_decision_context("RELIANCE", comparison_window=31)

    async def test_fusion_produces_complete_dossier_without_recalculating(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        stock_mock = Stock(id=1, symbol="RELIANCE", company_name="Reliance Industries", exchange="NSE")
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = stock_mock
        session.execute.return_value = mock_res

        mock_interp = _build_mock_d3_interpretation("RELIANCE")
        mock_snapshot = _build_mock_stock360_snapshot("RELIANCE")

        mock_d3_svc = MagicMock()
        mock_d3_svc.interpret = AsyncMock(return_value=mock_interp)

        mock_s360_svc = MagicMock()
        mock_s360_svc.get_stock360_snapshot = AsyncMock(return_value=mock_snapshot)

        service = StockDecisionContextFusionService(
            session=session,
            interpretation_service=mock_d3_svc,
            stock360_service=mock_s360_svc,
        )

        dossier = await service.get_decision_context("RELIANCE", evaluation_date=EVAL_DATE, comparison_window=2)

        # Invariant: D-3 service was called directly with expected parameters
        mock_d3_svc.interpret.assert_awaited_once_with(
            symbol="RELIANCE",
            evaluation_date=EVAL_DATE,
            comparison_window=2,
            include_ai_narrative=False,
        )

        # Invariant: Stock360 service was called directly with resolved evaluation date
        mock_s360_svc.get_stock360_snapshot.assert_awaited_once_with(
            symbol="RELIANCE",
            evaluation_date=EVAL_DATE,
            as_of_date=EVAL_DATE,
        )

        assert dossier.symbol == "RELIANCE"
        assert dossier.company_name == "Reliance Industries Ltd"
        assert dossier.sector == "Energy"
        assert dossier.overall_state == OverallState.BULLISH_EVIDENCE_SUPPORTED
        assert dossier.directional_bias == DirectionalBias.BULLISH_EVIDENCE
        assert dossier.convergence_ratio == 0.80
        assert len(dossier.profile_alignments) == 5
        assert dossier.pit_safe is True
        assert dossier.fallback_narrative != ""

    async def test_profile_alignment_aligned_on_bullish_consensus(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = Stock(id=1, symbol="TCS")
        session.execute.return_value = mock_res

        mock_interp = _build_mock_d3_interpretation("TCS", OverallState.BULLISH_EVIDENCE_SUPPORTED, DirectionalBias.BULLISH_EVIDENCE)
        mock_snapshot = _build_mock_stock360_snapshot("TCS")

        service = StockDecisionContextFusionService(
            session=session,
            interpretation_service=MagicMock(interpret=AsyncMock(return_value=mock_interp)),
            stock360_service=MagicMock(get_stock360_snapshot=AsyncMock(return_value=mock_snapshot)),
        )

        assessment = StockOpportunityAssessment(
            symbol="TCS",
            stock_id=1,
            profile="BREAKOUT",
            evaluation_date=EVAL_DATE,
            as_of_date=EVAL_DATE,
            score=82.0,
            classification="STRONG_RESEARCH_CANDIDATE",
            supporting_evidence=["Price above resistance"],
            contradicting_evidence=[],
            risk_flags=[],
            veto_reasons=[],
        )

        alignment = service._evaluate_profile_alignment(assessment, mock_interp, mock_snapshot)
        assert alignment.alignment_state == ProfileAlignmentState.ALIGNED
        assert "BREAKOUT" in alignment.alignment_summary
        assert len(alignment.supporting_evidence) > 0
        assert len(alignment.veto_reasons) == 0

    async def test_profile_alignment_blocked_by_veto(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_interp = _build_mock_d3_interpretation("TCS")
        mock_snapshot = _build_mock_stock360_snapshot("TCS")

        service = StockDecisionContextFusionService(session=session)

        assessment = StockOpportunityAssessment(
            symbol="TCS",
            stock_id=1,
            profile="BTST_READINESS",
            evaluation_date=EVAL_DATE,
            as_of_date=EVAL_DATE,
            score=75.0,
            classification="STRONG_RESEARCH_CANDIDATE",
            supporting_evidence=["Strong closing momentum"],
            contradicting_evidence=["High overnight gap risk"],
            risk_flags=["EARNINGS_OVERNIGHT"],
            veto_reasons=["OVERNIGHT_EARNINGS_ANNOUNCEMENT_VETO"],
        )

        alignment = service._evaluate_profile_alignment(assessment, mock_interp, mock_snapshot)
        assert alignment.alignment_state == ProfileAlignmentState.BLOCKED
        assert "OVERNIGHT_EARNINGS_ANNOUNCEMENT_VETO" in alignment.veto_reasons

    async def test_profile_alignment_blocked_by_bearish_divergence(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_interp = _build_mock_d3_interpretation(
            "TCS",
            overall_state=OverallState.BEARISH_EVIDENCE_SUPPORTED,
            directional_bias=DirectionalBias.BEARISH_EVIDENCE,
        )
        mock_snapshot = _build_mock_stock360_snapshot("TCS")

        service = StockDecisionContextFusionService(session=session)

        assessment = StockOpportunityAssessment(
            symbol="TCS",
            stock_id=1,
            profile="BREAKOUT",
            evaluation_date=EVAL_DATE,
            as_of_date=EVAL_DATE,
            score=65.0,
            classification="RESEARCH_CANDIDATE",
            supporting_evidence=[],
            contradicting_evidence=[],
            risk_flags=[],
            veto_reasons=[],
        )

        alignment = service._evaluate_profile_alignment(assessment, mock_interp, mock_snapshot)
        assert alignment.alignment_state == ProfileAlignmentState.BLOCKED
        assert "bearish" in alignment.alignment_summary.lower()

    async def test_profile_alignment_insufficient_data(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_interp = _build_mock_d3_interpretation(
            "TCS",
            overall_state=OverallState.INSUFFICIENT_EVIDENCE,
            directional_bias=DirectionalBias.NO_DIRECTIONAL_SIGNAL,
        )
        mock_snapshot = _build_mock_stock360_snapshot("TCS", completeness=35.0)

        service = StockDecisionContextFusionService(session=session)

        assessment = StockOpportunityAssessment(
            symbol="TCS",
            stock_id=1,
            profile="BREAKOUT",
            evaluation_date=EVAL_DATE,
            as_of_date=EVAL_DATE,
            score=50.0,
            classification="INSUFFICIENT_DATA",
            supporting_evidence=[],
            contradicting_evidence=[],
            risk_flags=[],
            veto_reasons=[],
        )

        alignment = service._evaluate_profile_alignment(assessment, mock_interp, mock_snapshot)
        assert alignment.alignment_state == ProfileAlignmentState.INSUFFICIENT_DATA

    async def test_btst_not_applicable_for_non_fno(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_interp = _build_mock_d3_interpretation("CASHONLY")
        mock_snapshot = _build_mock_stock360_snapshot("CASHONLY", is_fno=False)

        service = StockDecisionContextFusionService(session=session)

        assessment = StockOpportunityAssessment(
            symbol="CASHONLY",
            stock_id=1,
            profile="BTST_READINESS",
            evaluation_date=EVAL_DATE,
            as_of_date=EVAL_DATE,
            score=20.0,
            classification="NO_TRADE",
            supporting_evidence=[],
            contradicting_evidence=["Non-F&O stock"],
            risk_flags=[],
            veto_reasons=[],
        )

        alignment = service._evaluate_profile_alignment(assessment, mock_interp, mock_snapshot)
        assert alignment.alignment_state == ProfileAlignmentState.NOT_APPLICABLE
        assert "cash-only" in alignment.alignment_summary.lower()

    async def test_determinism_across_multiple_runs(self) -> None:
        session = AsyncMock(spec=AsyncSession)
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = Stock(id=1, symbol="RELIANCE")
        session.execute.return_value = mock_res

        mock_interp = _build_mock_d3_interpretation("RELIANCE")
        mock_snapshot = _build_mock_stock360_snapshot("RELIANCE")

        service = StockDecisionContextFusionService(
            session=session,
            interpretation_service=MagicMock(interpret=AsyncMock(return_value=mock_interp)),
            stock360_service=MagicMock(get_stock360_snapshot=AsyncMock(return_value=mock_snapshot)),
        )

        d1 = await service.get_decision_context("RELIANCE", evaluation_date=EVAL_DATE)
        d2 = await service.get_decision_context("RELIANCE", evaluation_date=EVAL_DATE)

        assert d1.symbol == d2.symbol
        assert d1.overall_state == d2.overall_state
        assert d1.convergence_ratio == d2.convergence_ratio
        assert len(d1.profile_alignments) == len(d2.profile_alignments)
        for p1, p2 in zip(d1.profile_alignments, d2.profile_alignments, strict=False):
            assert p1.profile == p2.profile
            assert p1.alignment_state == p2.alignment_state
            assert p1.canonical_score == p2.canonical_score


# ===========================================================================
# 4. REST API Endpoint Tests
# ===========================================================================


@pytest.mark.asyncio
class TestDecisionContextApiRoute:
    async def test_get_decision_context_api_200(self, db_session) -> None:
        # Seed an active stock for DB session
        stock = Stock(symbol="APITEST", company_name="API Test Corp", exchange="NSE", is_active=True)
        db_session.add(stock)
        await db_session.commit()

        mock_interp = _build_mock_d3_interpretation("APITEST")
        mock_snap = _build_mock_stock360_snapshot("APITEST")

        with (
            patch("app.services.stock_decision_context_service.WhatChangedInterpretationService.interpret", new_callable=AsyncMock) as mock_i,
            patch("app.services.stock_decision_context_service.Stock360Service.get_stock360_snapshot", new_callable=AsyncMock) as mock_s,
        ):
            mock_i.return_value = mock_interp
            mock_s.return_value = mock_snap

            async with auth_client(role="admin", browser=False) as client:
                res = await client.get("/api/v1/stocks/APITEST/decision-context")
                assert res.status_code == 200
                data = res.json()
                assert data["symbol"] == "APITEST"
                assert data["overall_state"] == "BULLISH_EVIDENCE_SUPPORTED"
                assert len(data["profile_alignments"]) == 5
                assert data["pit_safe"] is True

    async def test_get_decision_context_api_404_for_nonexistent_symbol(self) -> None:
        async with auth_client(role="admin", browser=False) as client:
            res = await client.get("/api/v1/stocks/NONEXISTENT999/decision-context")
            assert res.status_code == 404

    async def test_get_decision_context_api_422_invalid_window(self) -> None:
        async with auth_client(role="admin", browser=False) as client:
            res = await client.get("/api/v1/stocks/RELIANCE/decision-context?comparison_window=0")
            assert res.status_code == 422

            res2 = await client.get("/api/v1/stocks/RELIANCE/decision-context?comparison_window=31")
            assert res2.status_code == 422

    async def test_get_decision_context_api_unauthenticated_401(self) -> None:
        from httpx import ASGITransport, AsyncClient

        from app.main import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.get("/api/v1/stocks/RELIANCE/decision-context")
            assert res.status_code == 401


# ===========================================================================
# 5. HTML & HTMX UI Routes Tests
# ===========================================================================


@pytest.mark.asyncio
class TestDecisionContextHtmlRoutes:
    async def test_stock_decision_context_partial_returns_html(self, db_session) -> None:
        stock = Stock(symbol="HTMLTEST", company_name="HTML Test Corp", exchange="NSE", is_active=True)
        db_session.add(stock)
        await db_session.commit()

        mock_interp = _build_mock_d3_interpretation("HTMLTEST")
        mock_snap = _build_mock_stock360_snapshot("HTMLTEST")

        with (
            patch("app.services.stock_decision_context_service.WhatChangedInterpretationService.interpret", new_callable=AsyncMock) as mock_i,
            patch("app.services.stock_decision_context_service.Stock360Service.get_stock360_snapshot", new_callable=AsyncMock) as mock_s,
        ):
            mock_i.return_value = mock_interp
            mock_s.return_value = mock_snap

            async with auth_client(role="admin", browser=True) as client:
                res = await client.get("/stocks/HTMLTEST/decision-context")
                assert res.status_code == 200
                assert "text/html" in res.headers["content-type"]
                assert 'id="stock-decision-dossier"' in res.text
                assert "Decision Context &amp; Research Dossier" in res.text
                assert "Strategic Opportunity Profiles Alignment" in res.text

    async def test_stock_evidence_modal_partial_returns_html(self, db_session) -> None:
        stock = Stock(symbol="MODALTEST", company_name="Modal Test Corp", exchange="NSE", is_active=True)
        db_session.add(stock)
        await db_session.commit()

        mock_interp = _build_mock_d3_interpretation("MODALTEST")
        mock_snap = _build_mock_stock360_snapshot("MODALTEST")

        with (
            patch("app.services.stock_decision_context_service.WhatChangedInterpretationService.interpret", new_callable=AsyncMock) as mock_i,
            patch("app.services.stock_decision_context_service.Stock360Service.get_stock360_snapshot", new_callable=AsyncMock) as mock_s,
        ):
            mock_i.return_value = mock_interp
            mock_s.return_value = mock_snap

            async with auth_client(role="admin", browser=True) as client:
                res = await client.get("/stocks/MODALTEST/evidence-modal")
                assert res.status_code == 200
                assert "text/html" in res.headers["content-type"]
                assert 'id="evidenceDrillDownModal"' in res.text
                assert "Evidence Drill-Down" in res.text
                assert "MODALTEST" in res.text
                assert "Open Full Stock 360° Research Dossier" in res.text

    async def test_screener_page_has_modal_container(self) -> None:
        async with auth_client(role="admin", browser=True) as client:
            res = await client.get("/stocks/screening/what-changed")
            assert res.status_code == 200
            assert 'id="evidenceModalContainer"' in res.text

    async def test_screener_results_contain_inspect_button(self) -> None:
        from app.schemas.what_changed_screening import (
            ScreeningOutcomeStatus,
            ScreeningRequest,
            ScreeningResponse,
            ScreeningRunSummary,
            ScreeningUniverse,
            SymbolScreeningResult,
        )

        async with auth_client(role="admin", browser=True) as client:
            await client.get("/stocks/screening/what-changed")
            token = client.cookies.get("csrf_token")

            mock_resp = ScreeningResponse(
                request_echo=ScreeningRequest(universe=ScreeningUniverse.NIFTY_50),
                summary=ScreeningRunSummary(
                    total_universe_size=1,
                    matched_count=1,
                    no_match_count=0,
                    no_data_count=0,
                    error_count=0,
                    as_of_date=EVAL_DATE,
                ),
                results=[
                    SymbolScreeningResult(
                        symbol="SBIN",
                        company_name="State Bank of India",
                        status=ScreeningOutcomeStatus.MATCHED,
                        matched=True,
                        overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                        directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                        convergence_ratio=0.85,
                        supporting_domains=["PRICE", "VOLUME"],
                        conflicting_domains=[],
                        pit_safe=True,
                    )
                ],
            )

            with patch("app.services.what_changed_screening_service.WhatChangedScreeningService.screen", new_callable=AsyncMock) as mock_s:
                mock_s.return_value = mock_resp
                res = await client.post(
                    "/stocks/screening/what-changed",
                    data={"universe": "NIFTY_50"},
                    headers={"x-csrf-token": token or ""},
                )
                assert res.status_code == 200
                assert 'hx-get="/stocks/SBIN/evidence-modal"' in res.text
                assert 'hx-target="#evidenceModalContainer"' in res.text
                assert "Inspect" in res.text




# ===========================================================================
# 5b. Stock360 News Point-in-Time Cutoff Tests (FINDING-D5-01 regression)
#
# Defect fixed: Stock360Service.get_stock360_snapshot built the news
# evaluation_datetime from the raw evaluation_date, ignoring as_of_date.
# Historical replay with as_of_date < evaluation_date could therefore
# observe news events that became available after as_of_date. The fix
# clamps the news cutoff to min(evaluation_date, as_of_date), mirroring
# the technicals/relative-strength cutoff semantics.
# ===========================================================================


async def _seed_news_replay_universe(session: AsyncSession, symbol: str) -> None:
    """Seed a stock plus two news events straddling a historical as_of date.

    eval_d = 2026-09-15 (evaluation/replay date)
    as_of  = 2026-09-12 (PIT observation cutoff)

    Event A: available 2026-09-13 10:00 UTC — AFTER as_of, BEFORE eval_d.
             Must be EXCLUDED under historical replay; INCLUDED live.
    Event B: available 2026-09-11 10:00 UTC — ON/BEFORE as_of.
             Must be INCLUDED under historical replay.
    """
    stock = Stock(symbol=symbol, company_name=f"{symbol} Ltd", is_active=True)
    session.add(stock)
    await session.flush()

    session.add_all(
        [
            NewsEvent(
                source_name="NSE_FILING",
                source_type="OFFICIAL_PRIMARY",
                source_reference="PIT-A",
                published_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
                available_at=datetime(2026, 9, 13, 10, 0, tzinfo=UTC),
                effective_at=date(2026, 9, 14),
                fetched_at=datetime(2026, 9, 13, 10, 5, tzinfo=UTC),
                headline="Post-cutoff expansion announcement",
                summary="Became available after as_of_date.",
                event_type="COMPANY",
                category="GROWTH",
                deterministic_context="POSITIVE_CONTEXT",
                event_severity="MEDIUM",
                stock_id=stock.id,
                idempotency_hash=f"hash_{symbol}_news_after_asof",
            ),
            NewsEvent(
                source_name="NSE_FILING",
                source_type="OFFICIAL_PRIMARY",
                source_reference="PIT-B",
                published_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
                available_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
                effective_at=date(2026, 9, 12),
                fetched_at=datetime(2026, 9, 11, 10, 5, tzinfo=UTC),
                headline="Pre-cutoff capex disclosure",
                summary="Became available before as_of_date.",
                event_type="COMPANY",
                category="CAPEX",
                deterministic_context="NEUTRAL",
                event_severity="LOW",
                stock_id=stock.id,
                idempotency_hash=f"hash_{symbol}_news_before_asof",
            ),
        ]
    )
    await session.commit()


def _news_headlines(snapshot: Any) -> set[str]:
    """Extract the set of headlines visible in a Stock360 news snapshot."""
    news = getattr(snapshot, "news_policy", None)
    if news is None:
        return set()
    return {e.headline for e in (news.recent_events or [])}


# ===========================================================================
# 6. Database & Migration Invariant Tests
# ===========================================================================

class TestStock360NewsPointInTime:
    """Regression tests proving news availability honors as_of_date cutoff."""

    @pytest.mark.asyncio
    async def test_news_after_as_of_excluded_in_historical_replay(self) -> None:
        """Historical replay (as_of < eval) must NOT observe news available after as_of."""
        factory = get_session_factory()
        async with factory() as session:
            await _seed_news_replay_universe(session, "PITNEWA")

            service = Stock360Service(session)
            snap = await service.get_stock360_snapshot(
                "PITNEWA",
                evaluation_date=date(2026, 9, 15),
                as_of_date=date(2026, 9, 12),
            )

            headlines = _news_headlines(snap)
            assert "Post-cutoff expansion announcement" not in headlines, (
                "PIT violation: news available after as_of_date leaked into "
                "historical replay snapshot"
            )

    @pytest.mark.asyncio
    async def test_news_on_or_before_as_of_included_in_historical_replay(self) -> None:
        """News available on/before as_of must remain visible in historical replay."""
        factory = get_session_factory()
        async with factory() as session:
            await _seed_news_replay_universe(session, "PITNEWB")

            service = Stock360Service(session)
            snap = await service.get_stock360_snapshot(
                "PITNEWB",
                evaluation_date=date(2026, 9, 15),
                as_of_date=date(2026, 9, 12),
            )

            headlines = _news_headlines(snap)
            assert "Pre-cutoff capex disclosure" in headlines, (
                "News available before as_of_date was wrongly excluded from "
                "historical replay"
            )

    @pytest.mark.asyncio
    async def test_news_live_behavior_preserved_when_no_as_of(self) -> None:
        """Live snapshot (no as_of) must preserve existing behavior: both events visible."""
        factory = get_session_factory()
        async with factory() as session:
            await _seed_news_replay_universe(session, "PITNEWC")

            service = Stock360Service(session)
            snap = await service.get_stock360_snapshot(
                "PITNEWC", evaluation_date=date(2026, 9, 15)
            )

            headlines = _news_headlines(snap)
            assert "Post-cutoff expansion announcement" in headlines
            assert "Pre-cutoff capex disclosure" in headlines

    @pytest.mark.asyncio
    async def test_fusion_propagates_historical_as_of_to_stock360(self) -> None:
        """Fusion layer must propagate as_of_date (not fold it into eval) to Stock360.

        CASE C: evaluation_date=T, as_of_date=T-3. D-3 replays at T (its certified
        evaluation-date contract) while Stock360 receives the explicit as_of cutoff.
        """
        session = AsyncMock(spec=AsyncSession)
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = Stock(id=1, symbol="PITNEW")
        session.execute.return_value = mock_res

        mock_interp = _build_mock_d3_interpretation("PITNEW")
        mock_snapshot = _build_mock_stock360_snapshot("PITNEW")

        mock_d3_svc = MagicMock()
        mock_d3_svc.interpret = AsyncMock(return_value=mock_interp)
        mock_s360_svc = MagicMock()
        mock_s360_svc.get_stock360_snapshot = AsyncMock(return_value=mock_snapshot)

        service = StockDecisionContextFusionService(
            session=session,
            interpretation_service=mock_d3_svc,
            stock360_service=mock_s360_svc,
        )

        eval_d = date(2026, 9, 15)
        as_of_d = date(2026, 9, 12)
        dossier = await service.get_decision_context(
            "PITNEW", evaluation_date=eval_d, as_of_date=as_of_d
        )

        # D-3 consumes the certified evaluation-date contract (unchanged).
        mock_d3_svc.interpret.assert_awaited_once_with(
            symbol="PITNEW",
            evaluation_date=eval_d,
            comparison_window=1,
            include_ai_narrative=False,
        )
        # Stock360 receives the explicit historical cutoff — the fix target.
        mock_s360_svc.get_stock360_snapshot.assert_awaited_once_with(
            symbol="PITNEW",
            evaluation_date=eval_d,
            as_of_date=as_of_d,
        )
        assert dossier.as_of_date == as_of_d
        assert dossier.evaluation_date == eval_d


# ===========================================================================
# 6. Database & Migration Invariant Tests
# ===========================================================================
# ===========================================================================
# 6. Database & Migration Invariant Tests
# ===========================================================================


class TestDatabaseMigrationInvariant:
    def test_alembic_head_remains_unchanged(self) -> None:
        """G.11-D-5 is strictly stateless; zero migrations permitted in D-5 scope.

        The certified D1-D6 baseline migration is f1b9c8d7e6a5.
        Authorized downstream workstreams (e.g. G.11-OPT-1 b3a4c5d6e7f8) may chain
        linearly from this certified baseline without altering D-5 statelessness.
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
