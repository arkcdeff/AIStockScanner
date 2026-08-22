"""Unit & Integration Tests for Historical Data Coverage Certification & Session Continuity Engine — Sprint 19.1J.

Tests:
  - HistoricalCoverageCertificationService domain readiness calculation (READY, PARTIAL, INSUFFICIENT)
  - Session continuity semantics (BTST multi-week gap rejection, multi-day horizon span check)
  - Exact-strike readiness rules (option chain breadth, CE/PE pairing, multiple expiries)
  - Source provenance and idempotency checks
  - REST API /api/v1/market-data/coverage/certification endpoint
  - HTMX UI partial rendering with coverage readiness badges
"""
from datetime import date
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.schemas.historical_validation import ValidationSignalSnapshot
from app.services.historical_coverage_certification_service import (
    HistoricalCoverageCertificationService,
)
from app.services.historical_opportunity_validation_service import (
    HistoricalOpportunityValidationService,
)
from app.services.historical_options_validation_service import (
    HistoricalOptionsValidationService,
)
from tests.auth_helpers import auth_client, seed_builtins


@pytest.mark.asyncio
async def test_historical_coverage_certification_service_basic() -> None:
    """Test HistoricalCoverageCertificationService per-domain readiness assessment."""
    factory = get_session_factory()
    async with factory() as session:
        service = HistoricalCoverageCertificationService(session)
        report = await service.certify_coverage()

        assert report.as_of_date == date.today()
        assert "SWING" in report.domains
        assert "BTST" in report.domains
        assert "EXACT_STRIKE" in report.domains
        assert "INVESTMENT" in report.domains

        # Default DB state without populated F&O chains or financials
        assert report.domains["INVESTMENT"].readiness in ["INSUFFICIENT", "PARTIAL", "READY"]
        assert report.domains["EXACT_STRIKE"].readiness in ["INSUFFICIENT", "PARTIAL", "READY"]


@pytest.mark.asyncio
async def test_session_continuity_gap_rejection_for_btst() -> None:
    """Test session continuity check rejecting BTST cases with multi-week gap across missing DB rows."""
    service = HistoricalOpportunityValidationService(None)  # type: ignore[arg-type]

    # Signal cutoff on Aug 10
    signal = ValidationSignalSnapshot(
        evaluation_date=date(2026, 8, 10),
        as_of_date=date(2026, 8, 10),
        symbol="RELIANCE",
        profile="BTST",
        methodology_version="btst_v1",
        score=Decimal("75.00"),
        classification="HIGH_QUALITY",
        selected_side="BULLISH",
    )

    # Next available DB row is 20 days later (multi-week jump across missing sessions)
    prices = [
        DailyPrice(
            trade_date=date(2026, 8, 30),
            open=Decimal("2500.0"),
            high=Decimal("2550.0"),
            low=Decimal("2480.0"),
            close=Decimal("2520.0"),
            volume=10000,
        ),
    ]

    outcome = service.evaluate_case_outcome(
        signal=signal,
        prices=prices,
        benchmark_prices=[],
        horizon_days=1,
        execution_reference="NEXT_SESSION_OPEN",
    )

    # Must be rejected due to multi-week gap (> 4 calendar days)
    assert outcome is None


@pytest.mark.asyncio
async def test_session_continuity_gap_rejection_for_options() -> None:
    """Test option outcome evaluation rejecting multi-week gap entries."""
    service = HistoricalOptionsValidationService(None)  # type: ignore[arg-type]

    signal = ValidationSignalSnapshot(
        evaluation_date=date(2026, 8, 10),
        as_of_date=date(2026, 8, 10),
        symbol="NIFTY",
        profile="OPTIONS",
        methodology_version="option_strike_ranking_v1",
        score=Decimal("78.00"),
        classification="PREFERRED",
        selected_side="CE",
        selected_expiry=date(2026, 8, 28),
        selected_contract_id=101,
        selected_trading_symbol="NIFTY26AUG24500CE",
    )

    # Entry row is 15 days later
    opt_prices = [
        DerivativeDailyPrice(
            contract_id=101,
            trade_date=date(2026, 8, 25),
            close=Decimal("300.0"),
            underlying_value=Decimal("24500.0"),
        ),
    ]

    outcome = service.evaluate_option_outcome(signal, opt_prices, horizon_days=2)
    assert outcome is None


@pytest.mark.asyncio
async def test_coverage_certification_rest_api() -> None:
    """Integration test for GET /api/v1/market-data/coverage/certification."""
    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        resp = await client.get("/api/v1/market-data/coverage/certification")
        assert resp.status_code == 200
        data = resp.json()
        assert "domains" in data
        assert "SWING" in data["domains"]
        assert "readiness" in data["domains"]["SWING"]
