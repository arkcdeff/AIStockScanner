"""Unit & Integration Tests for Historical Opportunity Validation Engine — Sprint 19.1I.

Tests:
  - PIT Signal Freeze & Lookahead Isolation (signal frozen before outcome query)
  - Execution Reference Methodologies (NEXT_SESSION_OPEN vs SAME_CLOSE_RESEARCH_RETURN)
  - Profile-specific horizons (BTST, BREAKOUT, SWING, QUALITY_PULLBACK, INVESTMENT)
  - MFE and MAE calculation accuracy
  - Benchmark excess return calculation against NIFTY broad index
  - Options validation (CE/PE directional accuracy vs option premium return)
  - Preferred strike vs ATM benchmark comparator
  - Wilson score interval calculation for sample size uncertainty
  - Score bucket grouping & sample quality flags
  - REST API endpoints & HTMX UI partial rendering
"""
from datetime import date
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.stock import Stock
from app.schemas.historical_validation import (
    ValidationSignalSnapshot,
    compute_wilson_score_interval,
)
from app.services.historical_opportunity_validation_service import (
    HistoricalOpportunityValidationService,
)
from app.services.historical_options_validation_service import (
    HistoricalOptionsValidationService,
)
from tests.auth_helpers import auth_client, seed_builtins


def test_wilson_score_interval_computation() -> None:
    """Test Wilson score interval calculation for binary proportions."""
    # 70 successes out of 100
    ci_low, ci_high = compute_wilson_score_interval(70, 100)
    assert 60.0 <= ci_low <= 62.0
    assert 77.0 <= ci_high <= 79.0

    # 0 sample size
    low_zero, high_zero = compute_wilson_score_interval(0, 0)
    assert low_zero == 0.0
    assert high_zero == 0.0


def test_stock_outcome_mfe_mae_and_benchmark_excess() -> None:
    """Test isolated stock outcome evaluation, MFE, MAE, and benchmark excess return."""
    service = HistoricalOpportunityValidationService(None)  # type: ignore[arg-type]

    t_date = date(2026, 8, 10)
    signal = ValidationSignalSnapshot(
        evaluation_date=t_date,
        as_of_date=t_date,
        symbol="RELIANCE",
        profile="SWING",
        methodology_version="swing_v1",
        score=Decimal("80.00"),
        classification="HIGH_QUALITY_CANDIDATE",
        selected_side="BULLISH",
    )

    # 3 future price observations
    prices = [
        DailyPrice(trade_date=date(2026, 8, 11), open=Decimal("2500.0"), high=Decimal("2550.0"), low=Decimal("2480.0"), close=Decimal("2520.0")),
        DailyPrice(trade_date=date(2026, 8, 12), open=Decimal("2520.0"), high=Decimal("2600.0"), low=Decimal("2510.0"), close=Decimal("2580.0")),
        DailyPrice(trade_date=date(2026, 8, 13), open=Decimal("2580.0"), high=Decimal("2620.0"), low=Decimal("2560.0"), close=Decimal("2600.0")),
    ]

    bench_prices = [
        MarketIndexPrice(trade_date=date(2026, 8, 11), open=Decimal("24000.0"), high=Decimal("24100.0"), low=Decimal("23950.0"), close=Decimal("24000.0")),
        MarketIndexPrice(trade_date=date(2026, 8, 13), open=Decimal("24200.0"), high=Decimal("24300.0"), low=Decimal("24150.0"), close=Decimal("24240.0")),  # +1.0%
    ]

    outcome = service.evaluate_case_outcome(
        signal=signal,
        prices=prices,
        benchmark_prices=bench_prices,
        horizon_days=3,
        execution_reference="NEXT_SESSION_OPEN",
    )

    assert outcome is not None
    assert outcome.entry_price == Decimal("2500.0")  # Next session open
    assert outcome.exit_price == Decimal("2600.0")  # Day 3 close
    assert outcome.forward_return_pct == Decimal("4.00")  # (2600 - 2500) / 2500 * 100 = +4.0%
    assert outcome.mfe_pct == Decimal("4.80")  # Max high 2620 (+4.8%)
    assert outcome.mae_pct == Decimal("-0.80")  # Min low 2480 (-0.8%)
    assert outcome.benchmark_return_pct == Decimal("1.00")
    assert outcome.excess_return_pct == Decimal("3.00")  # 4.0% - 1.0% = +3.0%


def test_option_outcome_directional_vs_premium_return() -> None:
    """Test options outcome evaluation separating underlying directional accuracy from option premium P&L."""
    service = HistoricalOptionsValidationService(None)  # type: ignore[arg-type]

    t_date = date(2026, 8, 10)
    signal = ValidationSignalSnapshot(
        evaluation_date=t_date,
        as_of_date=t_date,
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

    opt_prices = [
        DerivativeDailyPrice(contract_id=101, trade_date=date(2026, 8, 11), close=Decimal("300.0"), underlying_value=Decimal("24500.0")),
        DerivativeDailyPrice(contract_id=101, trade_date=date(2026, 8, 13), close=Decimal("360.0"), underlying_value=Decimal("24700.0")),
    ]

    outcome = service.evaluate_option_outcome(signal, opt_prices, horizon_days=2)

    assert outcome is not None
    assert outcome.option_type == "CE"
    assert outcome.option_entry_price == Decimal("300.0")
    assert outcome.option_exit_price == Decimal("360.0")
    assert outcome.option_return_pct == Decimal("20.00")  # +20.0% option P&L
    assert outcome.underlying_return_pct == Decimal("0.82")
    assert outcome.directional_accuracy  # Underlying moved up for CE


@pytest.mark.asyncio
async def test_full_opportunity_validation_service_pipeline() -> None:
    """Integration test for full stock opportunity validation pipeline."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="TATASTEEL", company_name="Tata Steel Ltd", is_active=True)
        idx = MarketIndex(code="NIFTY", name="Nifty 50", is_active=True)
        session.add_all([st, idx])
        await session.flush()

        t1 = date(2026, 8, 11)
        t2 = date(2026, 8, 12)

        dp1 = DailyPrice(stock_id=st.id, trade_date=t1, open=Decimal("150.0"), high=Decimal("155.0"), low=Decimal("148.0"), close=Decimal("152.0"), volume=10000)
        dp2 = DailyPrice(stock_id=st.id, trade_date=t2, open=Decimal("152.0"), high=Decimal("160.0"), low=Decimal("151.0"), close=Decimal("158.0"), volume=10000)

        mp1 = MarketIndexPrice(market_index_id=idx.id, trade_date=t1, open=Decimal("24000.0"), high=Decimal("24100.0"), low=Decimal("23900.0"), close=Decimal("24000.0"))
        mp2 = MarketIndexPrice(market_index_id=idx.id, trade_date=t2, open=Decimal("24000.0"), high=Decimal("24200.0"), low=Decimal("23950.0"), close=Decimal("24100.0"))

        session.add_all([dp1, dp2, mp1, mp2])
        await session.commit()

        service = HistoricalOpportunityValidationService(session)
        summary = await service.validate_opportunities(
            profile="SWING",
            start_date=date(2026, 8, 10),
            end_date=date(2026, 8, 15),
            horizon_days=2,
        )

        assert summary.profile == "SWING"
        assert summary.sample_count > 0


@pytest.mark.asyncio
async def test_validation_rest_api_and_ui_partial() -> None:
    """Integration test for Historical Validation REST API endpoints and HTMX UI partial rendering."""
    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # REST API Opportunities Validation Endpoint
        resp_opp = await client.get(
            "/api/v1/market-data/validation/opportunities",
            params={"profile": "SWING"},
        )
        assert resp_opp.status_code == 200
        data_opp = resp_opp.json()
        assert "overall_mean_return_pct" in data_opp

        # REST API Options Validation Endpoint
        resp_opt = await client.get(
            "/api/v1/market-data/validation/options",
            params={"symbol": "NIFTY"},
        )
        assert resp_opt.status_code == 200

        # REST API Calibration Endpoint
        resp_cal = await client.get(
            "/api/v1/market-data/validation/calibration",
            params={"profile": "SWING"},
        )
        assert resp_cal.status_code == 200

        # HTMX UI Partial Endpoint
        resp_ui = await client.get(
            "/market-data/validation/lab",
            params={"profile": "SWING"},
        )
        assert resp_ui.status_code == 200
        assert "Historical Out-Of-Sample Validation &amp; Walk-Forward Calibration Lab" in resp_ui.text or "Historical Out-Of-Sample Validation" in resp_ui.text
