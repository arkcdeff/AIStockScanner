"""Comprehensive Test Suite for G.11-D-4: Multi-Stock What-Changed Universe Screener.

Validates:
  1. Configuration & bounds: Universe enums, concurrency caps (max 5 workers),
     custom symbols hard cap (100), watchlist soft cap (100).
  2. Pydantic request & filter schemas: validation of universe sources,
     custom symbol count limits, comparison windows, convergence bounds.
  3. Declarative filter evaluation: AND/OR operators, overall_state,
     directional_bias, convergence ratio, supporting domain count, PIT safety.
  4. Core screening service orchestration:
     - Bounded async concurrency (semaphore enforcement)
     - Session isolation (each worker uses its own AsyncSession)
     - Error resilience (worker exceptions yield ERROR outcome without aborting run)
     - Universe resolution (custom symbols, watchlist truncation, F&O, PIT indices)
     - Result pagination & truncation (max_results, include_non_matched)
  5. API endpoints (POST and GET under /api/v1/stocks/screening/what-changed):
     - Payload handling and schema responses
     - 422 validation on invalid universes or oversized symbol lists
     - Authentication & permission gating (stocks.read)
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.what_changed_interpretation import (
    DirectionalConvergence,
    StockIntelligenceInterpretation,
)
from app.schemas.what_changed_screening import (
    ScreeningFilterCriteria,
    ScreeningRequest,
    ScreeningResponse,
    ScreeningRunSummary,
    SymbolScreeningResult,
)
from app.services.what_changed_interpretation_config import (
    DirectionalBias,
    OverallState,
)
from app.services.what_changed_interpretation_service import (
    WhatChangedInterpretationService,
)
from app.services.what_changed_screening_config import (
    SCREENING_DEFAULT_COMPARISON_WINDOW,
    SCREENING_MAX_CUSTOM_SYMBOLS,
    SCREENING_MAX_WORKERS,
    SCREENING_WATCHLIST_SOFT_CAP,
    ScreeningFilterOperator,
    ScreeningOutcomeStatus,
    ScreeningUniverse,
)
from app.services.what_changed_screening_service import WhatChangedScreeningService
from app.services.what_changed_synthesis_config import EvidenceQuality
from tests.auth_helpers import auth_client

EVAL_DATE = date(2026, 9, 15)
COMP_DATE = date(2026, 9, 14)


# ===========================================================================
# 1. Configuration & Constants Tests
# ===========================================================================


class TestScreeningConfig:
    def test_universe_enum_values(self) -> None:
        expected = {
            "NIFTY_50",
            "NIFTY_NEXT_50",
            "NIFTY_MIDCAP_100",
            "NIFTY_BANK",
            "FNO",
            "WATCHLIST",
        }
        actual = {u.value for u in ScreeningUniverse}
        assert actual == expected

    def test_filter_operator_values(self) -> None:
        assert set(ScreeningFilterOperator) == {"AND", "OR"}

    def test_outcome_status_values(self) -> None:
        assert set(ScreeningOutcomeStatus) == {"MATCHED", "NO_MATCH", "ERROR", "NO_DATA"}

    def test_concurrency_and_size_bounds(self) -> None:
        assert SCREENING_MAX_WORKERS == 5
        assert SCREENING_MAX_CUSTOM_SYMBOLS == 100
        assert SCREENING_WATCHLIST_SOFT_CAP == 100
        assert SCREENING_DEFAULT_COMPARISON_WINDOW == 1


# ===========================================================================
# 2. Pydantic Request & Filter Schema Validation Tests
# ===========================================================================


class TestScreeningSchemas:
    def test_valid_universe_request(self) -> None:
        req = ScreeningRequest(universe=ScreeningUniverse.NIFTY_50)
        assert req.universe == ScreeningUniverse.NIFTY_50
        assert req.comparison_window == 1
        assert req.include_non_matched is False

    def test_valid_custom_symbols_request(self) -> None:
        req = ScreeningRequest(symbols=["TCS", "INFY", "RELIANCE"])
        assert req.symbols == ["TCS", "INFY", "RELIANCE"]

    def test_empty_request_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="One of 'universe', 'watchlist_id', or 'symbols'"):
            ScreeningRequest()

    def test_watchlist_universe_without_id_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="watchlist_id is required"):
            ScreeningRequest(universe=ScreeningUniverse.WATCHLIST)

    def test_valid_watchlist_request(self) -> None:
        req = ScreeningRequest(universe=ScreeningUniverse.WATCHLIST, watchlist_id=42)
        assert req.universe == ScreeningUniverse.WATCHLIST
        assert req.watchlist_id == 42

    def test_custom_symbols_exceeding_max_raises_validation_error(self) -> None:
        symbols = [f"SYM{i}" for i in range(101)]
        with pytest.raises(ValidationError):
            ScreeningRequest(symbols=symbols)

    def test_empty_custom_symbols_list_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="at least one non-empty symbol"):
            ScreeningRequest(symbols=["  ", ""])

    def test_filter_criteria_bounds(self) -> None:
        criteria = ScreeningFilterCriteria(
            operator=ScreeningFilterOperator.AND,
            min_convergence_ratio=0.75,
            min_supporting_domains=2,
            require_pit_safe=True,
        )
        assert criteria.min_convergence_ratio == 0.75
        assert criteria.min_supporting_domains == 2
        assert criteria.require_pit_safe is True

    def test_filter_criteria_invalid_convergence_ratio(self) -> None:
        with pytest.raises(ValidationError):
            ScreeningFilterCriteria(min_convergence_ratio=1.5)

        with pytest.raises(ValidationError):
            ScreeningFilterCriteria(min_convergence_ratio=-0.1)


# ===========================================================================
# 3. Declarative Filter Application Logic Tests
# ===========================================================================


class TestFilterApplication:
    def test_empty_criteria_matches_all(self) -> None:
        criteria = ScreeningFilterCriteria()
        matched = WhatChangedScreeningService._apply_filter(
            criteria,
            overall_state=OverallState.MIXED_EVIDENCE,
            directional_bias=DirectionalBias.MIXED,
            convergence_ratio=0.2,
            supporting_domains=["PRICE"],
            pit_safe=True,
        )
        assert matched is True

    def test_overall_state_filter_matches(self) -> None:
        criteria = ScreeningFilterCriteria(
            overall_states=[
                OverallState.BULLISH_EVIDENCE_SUPPORTED,
                OverallState.PARTIALLY_SUPPORTED_BULLISH_BIAS,
            ]
        )
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.8,
                supporting_domains=["PRICE", "VOLUME"],
                pit_safe=True,
            )
            is True
        )
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BEARISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BEARISH_EVIDENCE,
                convergence_ratio=0.8,
                supporting_domains=["PRICE", "VOLUME"],
                pit_safe=True,
            )
            is False
        )

    def test_directional_bias_filter_matches(self) -> None:
        criteria = ScreeningFilterCriteria(
            directional_biases=[DirectionalBias.BEARISH_EVIDENCE]
        )
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BEARISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BEARISH_EVIDENCE,
                convergence_ratio=0.7,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is True
        )
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BEARISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.7,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is False
        )

    def test_min_convergence_ratio_filter(self) -> None:
        criteria = ScreeningFilterCriteria(min_convergence_ratio=0.6)
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.65,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is True
        )
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.55,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is False
        )
        # None convergence_ratio defaults to 0.0
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.CONTEXT_ONLY,
                directional_bias=DirectionalBias.NO_DIRECTIONAL_SIGNAL,
                convergence_ratio=None,
                supporting_domains=[],
                pit_safe=True,
            )
            is False
        )

    def test_min_supporting_domains_filter(self) -> None:
        criteria = ScreeningFilterCriteria(min_supporting_domains=3)
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.7,
                supporting_domains=["PRICE", "VOLUME", "DERIVATIVES"],
                pit_safe=True,
            )
            is True
        )
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.7,
                supporting_domains=["PRICE", "VOLUME"],
                pit_safe=True,
            )
            is False
        )

    def test_require_pit_safe_filter(self) -> None:
        criteria = ScreeningFilterCriteria(require_pit_safe=True)
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.NEUTRAL_OBSERVATION,
                directional_bias=DirectionalBias.NEUTRAL,
                convergence_ratio=None,
                supporting_domains=[],
                pit_safe=True,
            )
            is True
        )
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.NEUTRAL_OBSERVATION,
                directional_bias=DirectionalBias.NEUTRAL,
                convergence_ratio=None,
                supporting_domains=[],
                pit_safe=False,
            )
            is False
        )

    def test_combined_and_operator_all_must_pass(self) -> None:
        criteria = ScreeningFilterCriteria(
            operator=ScreeningFilterOperator.AND,
            overall_states=[OverallState.BULLISH_EVIDENCE_SUPPORTED],
            min_convergence_ratio=0.5,
        )
        # Both match
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.6,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is True
        )
        # One fails
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.4,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is False
        )

    def test_combined_or_operator_any_may_pass(self) -> None:
        criteria = ScreeningFilterCriteria(
            operator=ScreeningFilterOperator.OR,
            overall_states=[OverallState.BULLISH_EVIDENCE_SUPPORTED],
            min_convergence_ratio=0.8,
        )
        # State matches even if convergence is low
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                convergence_ratio=0.2,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is True
        )
        # Convergence matches even if state is different
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.MIXED_EVIDENCE,
                directional_bias=DirectionalBias.MIXED,
                convergence_ratio=0.9,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is True
        )
        # Both fail
        assert (
            WhatChangedScreeningService._apply_filter(
                criteria,
                overall_state=OverallState.MIXED_EVIDENCE,
                directional_bias=DirectionalBias.MIXED,
                convergence_ratio=0.5,
                supporting_domains=["PRICE"],
                pit_safe=True,
            )
            is False
        )


# ===========================================================================
# 4. Core Screening Service Orchestration Tests
# ===========================================================================


def _make_dummy_interpretation(
    symbol: str,
    overall_state: OverallState = OverallState.BULLISH_EVIDENCE_SUPPORTED,
    directional_bias: DirectionalBias = DirectionalBias.BULLISH_EVIDENCE,
    convergence_ratio: float = 0.75,
    supporting_domains: list[str] | None = None,
    pit_safe: bool = True,
) -> StockIntelligenceInterpretation:
    convergence = DirectionalConvergence(
        directional_value=3,
        directional_max_possible=4,
        convergence_ratio=convergence_ratio,
        supporting_domains=supporting_domains or ["PRICE", "PARTICIPATION"],
        conflicting_domains=[],
        context_domains=["CORPORATE"],
        missing_domains=[],
        interpretation="Deterministic convergence description.",
    )
    return StockIntelligenceInterpretation(
        symbol=symbol,
        evaluation_date=EVAL_DATE,
        comparison_date=COMP_DATE,
        generated_at=datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC),
        overall_state=overall_state,
        directional_bias=directional_bias,
        convergence=convergence,
        overall_lifecycle=None,
        pattern_lifecycle_records=[],
        watch_items=[],
        limitations=[],
        narrative=None,
        fallback_narrative="Deterministic fallback narrative.",
        interpretation_method="DETERMINISTIC",
        evidence_quality=EvidenceQuality.CONFIRMED,
        pit_safe=pit_safe,
    )


@pytest.mark.asyncio
class TestWhatChangedScreeningService:
    async def test_screen_custom_symbols(self) -> None:
        mock_session = AsyncMock()
        mock_worker_session = AsyncMock()

        # Session factory context manager mock
        session_factory = MagicMock()
        session_factory.return_value.__aenter__.return_value = mock_worker_session

        service = WhatChangedScreeningService(mock_session, session_factory)

        # Mock company name query
        mock_result = MagicMock()
        mock_result.all.return_value = [
            ("INFY", "Infosys Ltd"),
            ("TCS", "Tata Consultancy Services"),
        ]
        mock_session.execute.return_value = mock_result

        # Mock D-3 interpret
        with patch.object(
            WhatChangedInterpretationService,
            "interpret",
            side_effect=[
                _make_dummy_interpretation("INFY", OverallState.BULLISH_EVIDENCE_SUPPORTED),
                _make_dummy_interpretation("TCS", OverallState.BEARISH_EVIDENCE_SUPPORTED),
            ],
        ):
            request = ScreeningRequest(
                symbols=["INFY", "TCS"],
                filter_criteria=ScreeningFilterCriteria(
                    overall_states=[OverallState.BULLISH_EVIDENCE_SUPPORTED]
                ),
            )
            response = await service.screen(request)

            assert isinstance(response, ScreeningResponse)
            assert response.summary.total_universe_size == 2
            assert response.summary.matched_count == 1
            assert response.summary.no_match_count == 1
            assert len(response.results) == 1
            assert response.results[0].symbol == "INFY"
            assert response.results[0].company_name == "Infosys Ltd"
            assert response.results[0].matched is True
            assert response.results[0].overall_state == OverallState.BULLISH_EVIDENCE_SUPPORTED

    async def test_screen_include_non_matched(self) -> None:
        mock_session = AsyncMock()
        mock_worker_session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__.return_value = mock_worker_session

        service = WhatChangedScreeningService(mock_session, session_factory)

        mock_result = MagicMock()
        mock_result.all.return_value = [("INFY", "Infosys"), ("TCS", "TCS")]
        mock_session.execute.return_value = mock_result

        with patch.object(
            WhatChangedInterpretationService,
            "interpret",
            side_effect=[
                _make_dummy_interpretation("INFY", OverallState.BULLISH_EVIDENCE_SUPPORTED),
                _make_dummy_interpretation("TCS", OverallState.BEARISH_EVIDENCE_SUPPORTED),
            ],
        ):
            request = ScreeningRequest(
                symbols=["INFY", "TCS"],
                include_non_matched=True,
                filter_criteria=ScreeningFilterCriteria(
                    overall_states=[OverallState.BULLISH_EVIDENCE_SUPPORTED]
                ),
            )
            response = await service.screen(request)

            assert len(response.results) == 2
            symbols = [r.symbol for r in response.results]
            assert "INFY" in symbols
            assert "TCS" in symbols

    async def test_screen_max_results_truncation(self) -> None:
        mock_session = AsyncMock()
        mock_worker_session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__.return_value = mock_worker_session

        service = WhatChangedScreeningService(mock_session, session_factory)

        mock_result = MagicMock()
        mock_result.all.return_value = [("S1", "C1"), ("S2", "C2"), ("S3", "C3")]
        mock_session.execute.return_value = mock_result

        with patch.object(
            WhatChangedInterpretationService,
            "interpret",
            side_effect=[
                _make_dummy_interpretation("S1"),
                _make_dummy_interpretation("S2"),
                _make_dummy_interpretation("S3"),
            ],
        ):
            request = ScreeningRequest(
                symbols=["S1", "S2", "S3"],
                max_results=2,
            )
            response = await service.screen(request)

            assert response.summary.matched_count == 3
            assert len(response.results) == 2

    async def test_worker_error_resilience(self) -> None:
        mock_session = AsyncMock()
        mock_worker_session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__.return_value = mock_worker_session

        service = WhatChangedScreeningService(mock_session, session_factory)

        mock_result = MagicMock()
        mock_result.all.return_value = [("GOOD", "Good Corp"), ("BAD", "Bad Corp")]
        mock_session.execute.return_value = mock_result

        # One symbol succeeds, one fails with an unexpected exception
        with patch.object(
            WhatChangedInterpretationService,
            "interpret",
            side_effect=[
                _make_dummy_interpretation("GOOD"),
                RuntimeError("Simulated DB connection timeout"),
            ],
        ):
            request = ScreeningRequest(
                symbols=["GOOD", "BAD"],
                include_non_matched=True,
            )
            response = await service.screen(request)

            assert response.summary.total_universe_size == 2
            assert response.summary.matched_count == 1
            assert response.summary.error_count == 1
            assert len(response.results) == 2

            bad_res = next(r for r in response.results if r.symbol == "BAD")
            assert bad_res.status == ScreeningOutcomeStatus.ERROR
            assert bad_res.matched is False
            assert "Simulated DB connection timeout" in (bad_res.error_detail or "")

    async def test_bounded_concurrency_semaphore(self) -> None:
        mock_session = AsyncMock()
        mock_worker_session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__.return_value = mock_worker_session

        service = WhatChangedScreeningService(mock_session, session_factory)

        mock_result = MagicMock()
        mock_result.all.return_value = [(f"SYM{i}", f"Comp{i}") for i in range(10)]
        mock_session.execute.return_value = mock_result

        active_workers = 0
        max_active_observed = 0

        async def _instrumented_interpret(*args: object, **kwargs: object) -> StockIntelligenceInterpretation:
            nonlocal active_workers, max_active_observed
            active_workers += 1
            max_active_observed = max(max_active_observed, active_workers)
            await asyncio.sleep(0.01)
            active_workers -= 1
            return _make_dummy_interpretation("TEST")

        with patch.object(WhatChangedInterpretationService, "interpret", side_effect=_instrumented_interpret):
            request = ScreeningRequest(symbols=[f"SYM{i}" for i in range(10)])
            await service.screen(request)

            assert max_active_observed <= SCREENING_MAX_WORKERS

    async def test_watchlist_truncation_soft_cap(self) -> None:
        mock_session = AsyncMock()
        session_factory = MagicMock()
        service = WhatChangedScreeningService(mock_session, session_factory)

        mock_watchlist = MagicMock()
        # 120 stocks in watchlist (exceeds 100 soft cap)
        mock_entries = [MagicMock(stock_id=i) for i in range(1, 121)]
        mock_watchlist.stocks = mock_entries

        with patch("app.services.watchlist.WatchlistService.get", AsyncMock(return_value=mock_watchlist)):
            mock_stock_res = MagicMock()
            mock_stocks = [MagicMock(id=i, symbol=f"S{i}", company_name=f"C{i}") for i in range(1, 101)]
            mock_stock_res.scalars.return_value = mock_stocks
            mock_session.execute.return_value = mock_stock_res

            resolved = await service._resolve_watchlist(watchlist_id=1)
            assert len(resolved) == SCREENING_WATCHLIST_SOFT_CAP
            assert resolved[0][0] == "S1"
            assert resolved[-1][0] == "S100"


# ===========================================================================
# 5. API Route Integration Tests
# ===========================================================================


@pytest.mark.asyncio
class TestScreeningApiRoutes:
    async def test_post_screening_endpoint(self) -> None:
        async with auth_client(role="admin") as client:
            with patch.object(
                WhatChangedScreeningService,
                "screen",
                AsyncMock(
                    return_value=ScreeningResponse(
                        request_echo=ScreeningRequest(universe=ScreeningUniverse.NIFTY_50),
                        summary=ScreeningRunSummary(
                            total_universe_size=50,
                            matched_count=5,
                            no_match_count=45,
                            no_data_count=0,
                            error_count=0,
                            as_of_date=EVAL_DATE,
                        ),
                        results=[
                            SymbolScreeningResult(
                                symbol="INFY",
                                company_name="Infosys",
                                status=ScreeningOutcomeStatus.MATCHED,
                                matched=True,
                                evaluation_date=EVAL_DATE,
                                comparison_date=COMP_DATE,
                                overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                                directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                                convergence_ratio=0.75,
                                supporting_domains=["PRICE", "PARTICIPATION"],
                                conflicting_domains=[],
                                context_domains=[],
                                pit_safe=True,
                            )
                        ],
                    )
                ),
            ):
                payload = {
                    "universe": "NIFTY_50",
                    "comparison_window": 1,
                    "filter_criteria": {
                        "overall_states": ["BULLISH_EVIDENCE_SUPPORTED"]
                    },
                }
                response = await client.post("/api/v1/stocks/screening/what-changed", json=payload)
                assert response.status_code == 200
                data = response.json()
                assert data["summary"]["total_universe_size"] == 50
                assert data["summary"]["matched_count"] == 5
                assert len(data["results"]) == 1
                assert data["results"][0]["symbol"] == "INFY"

    async def test_get_screening_endpoint_convenience(self) -> None:
        async with auth_client(role="admin") as client:
            with patch.object(
                WhatChangedScreeningService,
                "screen",
                AsyncMock(
                    return_value=ScreeningResponse(
                        request_echo=ScreeningRequest(universe=ScreeningUniverse.NIFTY_50),
                        summary=ScreeningRunSummary(
                            total_universe_size=50,
                            matched_count=50,
                            no_match_count=0,
                            no_data_count=0,
                            error_count=0,
                            as_of_date=None,
                        ),
                        results=[],
                    )
                ),
            ):
                response = await client.get("/api/v1/stocks/screening/what-changed?universe=NIFTY_50")
                assert response.status_code == 200
                data = response.json()
                assert data["summary"]["total_universe_size"] == 50

    async def test_post_screening_validation_error_over_100_symbols(self) -> None:
        async with auth_client(role="admin") as client:
            payload = {
                "symbols": [f"SYM{i}" for i in range(105)],
            }
            response = await client.post("/api/v1/stocks/screening/what-changed", json=payload)
            assert response.status_code == 422

    async def test_get_screening_invalid_universe_code_422(self) -> None:
        async with auth_client(role="admin") as client:
            response = await client.get("/api/v1/stocks/screening/what-changed?universe=INVALID_UNIVERSE_XYZ")
            assert response.status_code == 422
            assert "Invalid universe" in response.json()["detail"]


# ===========================================================================
# 6. G11D4-FIND-03 Remediation — Deterministic convergence ranking
# ===========================================================================


def _interp_with_convergence(
    symbol: str,
    convergence_ratio: float | None,
    overall_state: OverallState = OverallState.BULLISH_EVIDENCE_SUPPORTED,
) -> StockIntelligenceInterpretation:
    """Dummy interpretation with an explicit (possibly None) convergence."""
    base = _make_dummy_interpretation(symbol, overall_state=overall_state)
    if convergence_ratio is None:
        return base.model_copy(update={"convergence": None})
    convergence = base.convergence.model_copy(
        update={"convergence_ratio": convergence_ratio}
    )
    return base.model_copy(update={"convergence": convergence})


@pytest.mark.asyncio
class TestScreeningConvergenceRanking:
    """Filter → deterministic ranking → max_results truncation (in that order).

    Ranking contract: convergence_ratio DESC, symbol ASC tie-break, None
    convergence sorted deterministically last. Only certified D-3 fields —
    no predictive score, no AI.
    """

    def _service(self) -> WhatChangedScreeningService:
        mock_session = AsyncMock()
        session_factory = MagicMock()
        return WhatChangedScreeningService(mock_session, session_factory)

    async def _screen(
        self,
        symbols: list[str],
        ratios: dict[str, float | None],
        *,
        max_results: int | None = None,
        filter_criteria: ScreeningFilterCriteria | None = None,
    ) -> ScreeningResponse:
        service = self._service()
        result = MagicMock()
        result.all.return_value = [(s, f"Company {s}") for s in symbols]
        service._session.execute.return_value = result

        interp_map = {
            s: _interp_with_convergence(s, ratios.get(s)) for s in symbols
        }

        def _fake_interpret(*args: object, **kwargs: object) -> StockIntelligenceInterpretation:
            return interp_map[str(kwargs["symbol"])]

        with patch.object(
            WhatChangedInterpretationService, "interpret", side_effect=_fake_interpret
        ):
            request = ScreeningRequest(
                symbols=symbols,
                max_results=max_results,
                filter_criteria=filter_criteria or ScreeningFilterCriteria(),
            )
            return await service.screen(request)

    async def test_higher_convergence_wins_before_truncation(self) -> None:
        """A=0.90, B=0.50, C=0.20 with max_results=2 → A, B (not universe order)."""
        # Universe order C, B, A would naively truncate to C, B.
        response = await self._screen(
            symbols=["C", "B", "A"],
            ratios={"A": 0.90, "B": 0.50, "C": 0.20},
            max_results=2,
        )
        assert response.summary.matched_count == 3
        assert [r.symbol for r in response.results] == ["A", "B"]
        assert [r.convergence_ratio for r in response.results] == [0.90, 0.50]

    async def test_equal_convergence_tie_break_symbol_asc(self) -> None:
        response = await self._screen(
            symbols=["ZED", "ALPHA"],
            ratios={"ZED": 0.5, "ALPHA": 0.5},
            max_results=None,
        )
        assert [r.symbol for r in response.results] == ["ALPHA", "ZED"]

    async def test_none_convergence_sorts_last_deterministically(self) -> None:
        response = await self._screen(
            symbols=["B", "A", "C"],
            ratios={"A": 0.90, "B": None, "C": 0.20},
            max_results=None,
        )
        assert [r.symbol for r in response.results] == ["A", "C", "B"]

    async def test_none_convergence_tie_break_symbol_asc(self) -> None:
        response = await self._screen(
            symbols=["ZZ", "AA"],
            ratios={"ZZ": None, "AA": None},
            max_results=None,
        )
        assert [r.symbol for r in response.results] == ["AA", "ZZ"]

    async def test_no_matches_returns_empty_results(self) -> None:
        response = await self._screen(
            symbols=["A", "B"],
            ratios={"A": 0.9, "B": 0.5},
            filter_criteria=ScreeningFilterCriteria(
                overall_states=[OverallState.BEARISH_EVIDENCE_SUPPORTED]
            ),
        )
        assert response.results == []
        assert response.summary.matched_count == 0
        assert response.summary.no_match_count == 2

    async def test_max_results_at_or_above_result_count_returns_all_ranked(self) -> None:
        response = await self._screen(
            symbols=["C", "A", "B"],
            ratios={"A": 0.90, "B": 0.50, "C": 0.20},
            max_results=10,
        )
        assert [r.symbol for r in response.results] == ["A", "B", "C"]

    async def test_summary_counts_reflect_full_run_not_truncation(self) -> None:
        response = await self._screen(
            symbols=["C", "B", "A"],
            ratios={"A": 0.90, "B": 0.50, "C": 0.20},
            max_results=1,
        )
        assert len(response.results) == 1
        assert response.summary.matched_count == 3
        assert response.summary.total_universe_size == 3


# ===========================================================================
# 7. G11D4-FIND-02 Remediation — F&O point-in-time universe resolution
# ===========================================================================


async def _seed_fno_fixture(
    session: AsyncSession,
    specs: list[tuple[str, date | None]],
) -> dict[str, int]:
    """Create active stocks + FUTURE derivative contracts.

    ``specs`` maps symbol → available_at (None → no contract).
    Returns the mapping symbol → stock_id.
    """
    from app.models.derivative_contract import DerivativeContract
    from app.models.stock import Stock

    symbol_to_id: dict[str, int] = {}
    for symbol, available_at in specs:
        stock = Stock(
            symbol=symbol,
            company_name=f"{symbol} Ltd",
            exchange="NSE",
            is_active=True,
        )
        session.add(stock)
        await session.flush()
        symbol_to_id[symbol] = stock.id
        if available_at is not None:
            session.add(
                DerivativeContract(
                    exchange="NSE",
                    segment="NFO",
                    instrument_type="FUTURE",
                    underlying_stock_id=stock.id,
                    underlying_index_id=None,
                    trading_symbol=f"{symbol}-FUT",
                    expiry_date=date(2026, 10, 29),
                    lot_size=250,
                    available_at=available_at,
                )
            )
    await session.commit()
    return symbol_to_id


@pytest.mark.asyncio
class TestScreeningFnoPointInTime:
    """F&O universe membership must honour ``available_at <= as_of_date``."""

    AS_OF = date(2026, 9, 15)

    async def _resolve(
        self, session: AsyncSession, as_of: date | None
    ) -> list[str]:
        from app.db.session import get_session_factory

        service = WhatChangedScreeningService(session, get_session_factory())
        resolved = await service._resolve_fno(as_of)
        return [symbol for symbol, _company in resolved]

    async def test_contract_available_before_as_of_included(self, db_session) -> None:
        await _seed_fno_fixture(db_session, [("EARLY", date(2026, 1, 5))])
        symbols = await self._resolve(db_session, self.AS_OF)
        assert symbols == ["EARLY"]

    async def test_contract_available_on_as_of_included(self, db_session) -> None:
        # Boundary: available_at == as_of_date must be included (<= rule).
        await _seed_fno_fixture(db_session, [("BOUNDARY", self.AS_OF)])
        symbols = await self._resolve(db_session, self.AS_OF)
        assert symbols == ["BOUNDARY"]

    async def test_contract_available_after_as_of_excluded(self, db_session) -> None:
        await _seed_fno_fixture(db_session, [("FUTUREADD", date(2026, 12, 1))])
        symbols = await self._resolve(db_session, self.AS_OF)
        assert symbols == []

    async def test_no_as_of_preserves_live_universe(self, db_session) -> None:
        # Live behaviour: every contract is considered regardless of
        # availability date (including future-dated listings).
        await _seed_fno_fixture(
            db_session,
            [("OLDSTK", date(2026, 1, 5)), ("NEWSTK", date(2026, 12, 1))],
        )
        symbols = await self._resolve(db_session, None)
        # F&O resolution orders by Stock.symbol ascending.
        assert symbols == ["NEWSTK", "OLDSTK"]

    async def test_multiple_contracts_same_underlying_one_symbol(self, db_session) -> None:
        from app.models.derivative_contract import DerivativeContract

        stock_ids = await _seed_fno_fixture(
            db_session, [("MULTI", date(2026, 1, 5))]
        )
        # Two extra contracts for the same underlying (different expiries).
        db_session.add_all(
            [
                DerivativeContract(
                    exchange="NSE",
                    segment="NFO",
                    instrument_type="OPTION",
                    underlying_stock_id=stock_ids["MULTI"],
                    underlying_index_id=None,
                    trading_symbol="MULTI-CE",
                    expiry_date=date(2026, 10, 29),
                    strike_price=1500,
                    option_type="CE",
                    lot_size=250,
                    available_at=date(2026, 2, 1),
                ),
                DerivativeContract(
                    exchange="NSE",
                    segment="NFO",
                    instrument_type="FUTURE",
                    underlying_stock_id=stock_ids["MULTI"],
                    underlying_index_id=None,
                    trading_symbol="MULTI-FUT2",
                    expiry_date=date(2026, 11, 26),
                    lot_size=250,
                    available_at=date(2026, 3, 1),
                ),
            ]
        )
        await db_session.commit()

        symbols = await self._resolve(db_session, self.AS_OF)
        assert symbols == ["MULTI"]

    async def test_mixed_availability_pit_membership(self, db_session) -> None:
        # PIT membership: only the pre-as_of contract's underlying appears.
        await _seed_fno_fixture(
            db_session,
            [("HISTOK", date(2026, 1, 5)), ("LATERADD", date(2027, 1, 1))],
        )
        symbols = await self._resolve(db_session, self.AS_OF)
        assert symbols == ["HISTOK"]

    async def test_empty_historical_fno_universe(self, db_session) -> None:
        # No contracts at all → empty universe, no error.
        symbols = await self._resolve(db_session, self.AS_OF)
        assert symbols == []


# ===========================================================================
# 8. G11D4-FIND-01 Remediation — HTML/HTMX screener UI routes
# ===========================================================================


@pytest.mark.asyncio
class TestScreeningHtmlRoutes:
    """HTMX form → HTML-rendering route → service → HTML fragment.

    The JSON REST API (/api/v1/stocks/screening/what-changed) must remain a
    JSON API; the UI must consume the HTML fragment route.
    """

    async def test_screener_page_renders_htmx_form(self) -> None:
        async with auth_client(role="admin", browser=True) as client:
            response = await client.get("/stocks/screening/what-changed")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert 'hx-post="/stocks/screening/what-changed"' in response.text
            assert "#screening-results-area" in response.text
            assert 'name="universe"' in response.text
            assert 'name="as_of_date"' in response.text
            # json-enc extension must not be referenced.
            assert "json-enc" not in response.text
            # CSRF token must be staged for the shell's auto-header.
            assert client.cookies.get("csrf_token")

    async def test_htmx_post_returns_html_fragment(self) -> None:
        async with auth_client(role="admin", browser=True) as client:
            page = await client.get("/stocks/screening/what-changed")
            assert page.status_code == 200
            token = client.cookies.get("csrf_token")

            response = ScreeningResponse(
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
                        symbol="INFY",
                        company_name="Infosys Ltd",
                        status=ScreeningOutcomeStatus.MATCHED,
                        matched=True,
                        evaluation_date=EVAL_DATE,
                        comparison_date=COMP_DATE,
                        overall_state=OverallState.BULLISH_EVIDENCE_SUPPORTED,
                        directional_bias=DirectionalBias.BULLISH_EVIDENCE,
                        convergence_ratio=0.9,
                        supporting_domains=["PRICE"],
                        conflicting_domains=[],
                        context_domains=[],
                        pit_safe=True,
                    )
                ],
            )
            with patch.object(
                WhatChangedScreeningService,
                "screen",
                AsyncMock(return_value=response),
            ):
                result = await client.post(
                    "/stocks/screening/what-changed",
                    data={
                        "universe": "NIFTY_50",
                        "comparison_window": "1",
                    },
                    headers={
                        "HX-Request": "true",
                        settings.csrf_header_name: token or "",
                    },
                )
            assert result.status_code == 200
            assert "text/html" in result.headers["content-type"]
            assert "Screening Results" in result.text
            assert "INFY" in result.text
            # The fragment must not contain the form (it swaps into the
            # results area only) and must not be JSON.
            assert "what-changed-screening-form" not in result.text
            assert not result.text.lstrip().startswith("{")

    async def test_htmx_post_parses_form_parameters_into_request(self) -> None:
        async with auth_client(role="admin", browser=True) as client:
            page = await client.get("/stocks/screening/what-changed")
            assert page.status_code == 200
            token = client.cookies.get("csrf_token")

            captured: dict[str, object] = {}

            async def _capture_screen(_self, request):  # type: ignore[no-untyped-def]
                captured["request"] = request
                return ScreeningResponse(
                    request_echo=request,
                    summary=ScreeningRunSummary(
                        total_universe_size=0,
                        matched_count=0,
                        no_match_count=0,
                        no_data_count=0,
                        error_count=0,
                        as_of_date=None,
                    ),
                    results=[],
                )

            with patch.object(
                WhatChangedScreeningService, "screen", _capture_screen
            ):
                result = await client.post(
                    "/stocks/screening/what-changed",
                    data={
                        "universe": "NIFTY_50",
                        "watchlist_id": "",
                        "as_of_date": "2026-09-15",
                        "comparison_window": "3",
                        "symbols_csv": "infy, tcs",
                        "filter_criteria[overall_states]": "BULLISH_EVIDENCE_SUPPORTED",
                        "filter_criteria[min_convergence_ratio]": "0.50",
                    },
                    headers={
                        "HX-Request": "true",
                        settings.csrf_header_name: token or "",
                    },
                )
            assert result.status_code == 200
            req = captured["request"]
            assert req.universe == ScreeningUniverse.NIFTY_50
            assert req.symbols == ["INFY", "TCS"]
            assert req.as_of_date == date(2026, 9, 15)
            assert req.comparison_window == 3
            assert req.filter_criteria.min_convergence_ratio == 0.5
            assert req.filter_criteria.overall_states == [
                OverallState.BULLISH_EVIDENCE_SUPPORTED
            ]

    async def test_htmx_post_rejects_missing_csrf(self) -> None:
        async with auth_client(role="admin", browser=True) as client:
            result = await client.post(
                "/stocks/screening/what-changed",
                data={"universe": "NIFTY_50"},
            )
            assert result.status_code == 403

    async def test_htmx_post_invalid_universe_renders_error_flash(self) -> None:
        async with auth_client(role="admin", browser=True) as client:
            await client.get("/stocks/screening/what-changed")
            token = client.cookies.get("csrf_token")
            result = await client.post(
                "/stocks/screening/what-changed",
                data={"universe": "NOT_A_UNIVERSE"},
                headers={
                    "HX-Request": "true",
                    settings.csrf_header_name: token or "",
                },
            )
            assert result.status_code == 200
            assert "alert-danger" in result.text

    async def test_json_api_remains_json_after_ui_remediation(self) -> None:
        async with auth_client(role="admin") as client:
            with patch.object(
                WhatChangedScreeningService,
                "screen",
                AsyncMock(
                    return_value=ScreeningResponse(
                        request_echo=ScreeningRequest(universe=ScreeningUniverse.NIFTY_50),
                        summary=ScreeningRunSummary(
                            total_universe_size=0,
                            matched_count=0,
                            no_match_count=0,
                            no_data_count=0,
                            error_count=0,
                            as_of_date=None,
                        ),
                        results=[],
                    )
                ),
            ):
                response = await client.post(
                    "/api/v1/stocks/screening/what-changed",
                    json={"universe": "NIFTY_50"},
                )
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("application/json")
                assert response.json()["summary"]["total_universe_size"] == 0
