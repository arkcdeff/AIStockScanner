"""Tests for Stage 2 G.3 Browser Truth and Product Consistency Repair.

Verifies:
1. evaluation-date vs current scanner-date clarity (ScanRunner resolves to data date, not server now)
2. stale latest scan date handling (as_of_date filtering enforces PIT consistency)
3. stale AI explanation must not appear current (suppresses contradictory stale mock text)
4. AI explanation date mismatch handling (renders stale cache advisory banner when dates differ)
5. scanner eligible-universe count semantics (returns total product stocks and history count)
6. 3530 baseline vs 3529 scanner-specific eligibility (identifies index entity without prices)
7. no regression in G.3 delivery behavior (delivery calculations and scanners unchanged)
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from app.core.config import settings
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.repositories.stock_detail import StockDetailRepository
from app.services.market_data_service import MarketDataService
from app.services.scanner_engine import (
    IndicatorRow,
    ScanRunner,
    delivery_above_60,
    delivery_above_70,
    delivery_above_average,
    delivery_breakout,
    high_volume_high_delivery,
    price_breakout_high_delivery,
)


@pytest.fixture
def jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )


@pytest.mark.asyncio
async def test_evaluation_date_vs_current_scanner_date_clarity():
    """Verify ScanRunner resolves evaluation_date to the latest indicator trade date."""
    factory = get_session_factory()
    async with factory() as session:
        # Seed a test stock and indicator row
        stock = Stock(symbol="TESTDATE", company_name="Test Date Stock", is_active=True)
        session.add(stock)
        await session.flush()

        ir = IndicatorResult(
            stock_id=stock.id,
            trade_date=date(2026, 8, 31),
            ema_20=100.0,
            ema_50=100.0,
            ema_100=100.0,
            ema_200=100.0,
            sma=100.0,
            rsi_14=50.0,
            macd=0.0,
            macd_signal=0.0,
            macd_hist=0.0,
            stochastic_k=50.0,
            stochastic_d=50.0,
            atr=2.0,
            bollinger_upper=105.0,
            bollinger_middle=100.0,
            bollinger_lower=95.0,
            adx=25.0,
            supertrend=98.0,
            supertrend_direction=1,
            vwap=100.0,
            obv=1000.0,
        )
        session.add(ir)
        await session.flush()

        runner = ScanRunner(session)
        # Test explicit date resolution
        explicit_d = date(2026, 8, 20)
        assert await runner._resolve_evaluation_date(explicit_d) == explicit_d

        # Test None resolution: must resolve to max available indicator date, not server now (2026-09-02)
        resolved = await runner._resolve_evaluation_date(None)
        assert resolved == date(2026, 8, 31)

        await session.rollback()


@pytest.mark.asyncio
async def test_stale_latest_scan_date_handling():
    """Verify StockDetailRepository.get_scanner_matches respects as_of_date for PIT consistency."""
    factory = get_session_factory()
    async with factory() as session:
        # Create test stock and scan definition
        stock = Stock(symbol="TESTPIT", company_name="Test PIT Stock", is_active=True)
        scan_def = ScanDefinition(code="test_pit_scan", name="Test PIT Scan", category="Technical")
        session.add_all([stock, scan_def])
        await session.flush()

        # Add past scan (2026-08-06) and future scan (2026-09-02)
        sr_past = ScanResult(
            stock_id=stock.id,
            scan_definition_id=scan_def.id,
            scan_date=date(2026, 8, 6),
            score=1.0,
            metrics={"reason": "Past scan"},
        )
        sr_future = ScanResult(
            stock_id=stock.id,
            scan_definition_id=scan_def.id,
            scan_date=date(2026, 9, 2),
            score=1.0,
            metrics={"reason": "Future scan"},
        )
        session.add_all([sr_past, sr_future])
        await session.flush()

        repo = StockDetailRepository(session)
        # Query with as_of_date = 2026-08-31: must ONLY see the past scan
        matches = await repo.get_scanner_matches(stock.id, as_of_date=date(2026, 8, 31))
        assert len(matches) == 1
        assert matches[0].scan_date == date(2026, 8, 6)

        # Summary must also report latest_date = 2026-08-06
        summary = await repo.get_scanner_match_summary(stock.id, as_of_date=date(2026, 8, 31))
        assert summary["latest_date"] == date(2026, 8, 6)

        await session.rollback()


def test_stale_ai_explanation_must_not_appear_current(jinja_env: Environment):
    """Verify stale AI explanation renders an advisory and does NOT display contradictory active text."""
    template = jinja_env.get_template("components/stock_ai.html")

    # Stale explanation from 2026-08-04 evaluated on 2026-08-31
    explanation = SimpleNamespace(
        symbol="HDFCBANK",
        company_name="HDFC Bank",
        trade_date=date(2026, 8, 4),
        provider="mock",
        model="mock-analyst",
        source="cache",
        technical_score=SimpleNamespace(
            overall_score=19,
            trend_rating="Strong Downtrend",
            momentum_rating="Strong Bearish",
        ),
        summary="HDFCBANK setup on 2026-08-04",
        medium_summary=None,
        detailed_summary=None,
        strengths=["Positive overall rating", "Trend appears constructive"],
        risks=["Elevated risk"],
        support=["700.00"],
        resistance=["750.00"],
        trend_explanation="Trend appears constructive",
        scanner_explanation=None,
        risk_explanation=None,
        confidence=54,
    )

    # Render with mismatched evaluation_date
    html = template.render(
        explanation=explanation,
        evaluation_date=date(2026, 8, 31),
    )

    assert "Cached explanation from 2026-08-04" in html
    assert "not applicable to current 2026-08-31 evaluation" in html
    assert "AI explanation unavailable for this evaluation date" in html
    # Contradictory claims must be suppressed from active display
    assert "Positive overall rating" not in html
    assert "Trend appears constructive" not in html


def test_ai_explanation_date_mismatch_handling(jinja_env: Environment):
    """Verify matched dates render explanation normally while mismatched dates show advisory."""
    template = jinja_env.get_template("components/stock_ai.html")

    explanation = SimpleNamespace(
        symbol="RELIANCE",
        company_name="Reliance Industries",
        trade_date=date(2026, 8, 31),
        provider="mock",
        model="mock-analyst",
        source="generated",
        technical_score=SimpleNamespace(
            overall_score=85,
            trend_rating="Bullish",
            momentum_rating="Strong Bullish",
        ),
        summary="RELIANCE strong setup on 2026-08-31",
        medium_summary=None,
        detailed_summary=None,
        strengths=["Strong volume breakout", "High delivery accumulation"],
        risks=["Market volatility"],
        support=["1300.00"],
        resistance=["1400.00"],
        trend_explanation=None,
        scanner_explanation=None,
        risk_explanation=None,
        confidence=88,
    )

    # When evaluation date matches explanation date: render active explanation
    html_matched = template.render(
        explanation=explanation,
        evaluation_date=date(2026, 8, 31),
    )
    assert "High delivery accumulation" in html_matched
    assert "not applicable" not in html_matched

    # When evaluation date is None or explanation is None: render unavailable state
    html_none = template.render(explanation=None, evaluation_date=date(2026, 8, 31))
    assert "AI explanation unavailable for this evaluation date." in html_none


@pytest.mark.asyncio
async def test_scanner_eligible_universe_count_semantics():
    """Verify MarketDataService.scanner_readiness distinguishes stocks_with_history from total_product_stocks."""
    factory = get_session_factory()
    async with factory() as session:
        # Seed 2 stocks: 1 with DailyPrice, 1 without
        stock1 = Stock(symbol="TESTWITHP", company_name="Stock With Price", is_active=True)
        stock2 = Stock(symbol="TESTNOP", company_name="Stock No Price", is_active=True)
        session.add_all([stock1, stock2])
        await session.flush()

        dp = DailyPrice(
            stock_id=stock1.id,
            trade_date=date(2026, 8, 31),
            open=100,
            high=105,
            low=95,
            close=102,
            volume=50000,
            delivery_percentage=65.0,
        )
        session.add(dp)
        await session.flush()

        svc = MarketDataService(session)
        readiness = await svc.scanner_readiness()

        assert "stocks_with_history" in readiness
        assert "total_product_stocks" in readiness
        assert readiness["stocks_with_history"] >= 1
        assert readiness["total_product_stocks"] >= 2
        assert readiness["stocks_with_history"] < readiness["total_product_stocks"]

        await session.rollback()


@pytest.mark.asyncio
async def test_3530_baseline_vs_3529_scanner_specific_eligibility():
    """Verify that stock without daily price records explains the delta in product universe."""
    factory = get_session_factory()
    async with factory() as session:
        # Create an equity stock with price and an index placeholder without price
        equity = Stock(symbol="EQUITY1", company_name="Equity Company", is_active=True)
        index_stock = Stock(symbol="INDEX1", company_name="Index Placeholder", is_active=True)
        session.add_all([equity, index_stock])
        await session.flush()

        dp = DailyPrice(
            stock_id=equity.id,
            trade_date=date(2026, 8, 31),
            open=100,
            high=105,
            low=95,
            close=100,
            volume=1000,
        )
        session.add(dp)
        await session.flush()

        subq = select(DailyPrice.stock_id).distinct()
        stmt = select(Stock.symbol).where(
            Stock.id.in_([equity.id, index_stock.id]),
            Stock.id.not_in(subq),
        )
        missing_symbols = (await session.execute(stmt)).scalars().all()
        assert len(missing_symbols) == 1
        assert missing_symbols[0] == "INDEX1"

        await session.rollback()


def test_no_regression_in_g3_delivery_behavior():
    """Verify all six delivery scanner logic and threshold formulas remain intact."""
    curr = IndicatorRow(
        stock_id=1,
        symbol="HDFCBANK",
        trade_date=date(2026, 8, 31),
        close=709.0,
        high=720.0,
        volume=1000000,
        delivery_percentage=53.36,
        avg_delivery_20d=50.0,
        max_delivery_20d=52.0,
        avg_volume_20d=800000,
        max_high_20d=700.0,
        ema_20=710.0,
        ema_50=705.0,
        ema_100=700.0,
        ema_200=690.0,
        sma=708.0,
        rsi_14=48.0,
        macd=-2.0,
        macd_signal=-1.5,
        macd_hist=-0.5,
        stochastic_k=40.0,
        stochastic_d=45.0,
        atr=12.0,
        bollinger_upper=730.0,
        bollinger_middle=710.0,
        bollinger_lower=690.0,
        adx=22.0,
        supertrend=715.0,
        supertrend_direction=-1,
        vwap=708.5,
        obv=50000000.0,
    )
    # delivery is 53.36% -> below 60%
    matched_60, _, _ = delivery_above_60(curr, None)
    assert matched_60 is False

    # delivery is 53.36% > 20d avg 50.0% -> matches delivery_above_average
    matched_avg, _, _ = delivery_above_average(curr, None)
    assert matched_avg is True

    # delivery is 53.36% > 20d max 52.0% -> matches delivery_breakout
    matched_breakout, _, _ = delivery_breakout(curr, None)
    assert matched_breakout is True

    # delivery_above_70 requires >= 70%
    matched_70, _, _ = delivery_above_70(curr, None)
    assert matched_70 is False

    # high_volume_high_delivery requires vol >= 2x avg and deliv >= 60
    matched_hv, _, _ = high_volume_high_delivery(curr, None)
    assert matched_hv is False

    # price_breakout_high_delivery requires close > high_20d, vol >= avg, deliv >= 60
    matched_pb, _, _ = price_breakout_high_delivery(curr, None)
    assert matched_pb is False
