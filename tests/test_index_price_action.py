"""Tests for the Indian index price-action engine (Sprint 18 Task 18.2).

These tests exercise the deterministic ``IndexPriceAction`` engine against a
real PostgreSQL test database. They verify:

- basic price (open/high/low/close/previous_close/change_percent)
- gap points / gap percent / gap classification
- session returns (1/5/20/60)
- moving averages (SMA20/50/100/200) + above/below + distance
- trend classification
- momentum
- 52-week high/low/position
- daily range %
- 20-session average daily move
- price_action_score bounded to -100..+100
- score breakdown
- missing values remain None (never fabricated as zero)
- no-look-ahead: historical calculations are unaffected by future rows
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.services.index_price_action import IndexPriceAction

INDEX_CODE = "NIFTY_50"


def _factory():
    return get_session_factory()


def _price(
    market_index_id: int,
    trade_date: date,
    close: Decimal,
    *,
    open_price: Decimal | None = None,
    high: Decimal | None = None,
    low: Decimal | None = None,
    previous_close: Decimal | None = None,
) -> MarketIndexPrice:
    """Build a MarketIndexPrice row with sensible defaults."""
    return MarketIndexPrice(
        market_index_id=market_index_id,
        trade_date=trade_date,
        open=open_price if open_price is not None else close,
        high=high if high is not None else close + Decimal("10"),
        low=low if low is not None else close - Decimal("10"),
        close=close,
        previous_close=previous_close,
        freshness_status="live",
    )


async def _seed_index_and_history(
    *,
    n_sessions: int = 260,
    start_close: Decimal = Decimal("10000"),
    step: Decimal = Decimal("10"),
) -> int:
    """Seed a MarketIndex + a rising price history; return the index id."""
    factory = _factory()
    async with factory() as session:
        mi = MarketIndex(
            code=INDEX_CODE,
            name="Nifty 50",
            country="INDIA",
            exchange="NSE",
            category="indian_index",
            provider_symbol="NIFTY50",
            is_active=True,
        )
        session.add(mi)
        await session.flush()

        base_date = date(2024, 1, 1)
        for i in range(n_sessions):
            close = start_close + step * i
            trade_date = base_date + timedelta(days=i)
            session.add(
                _price(
                    mi.id,
                    trade_date,
                    close,
                    previous_close=close - step if i > 0 else None,
                )
            )
        await session.commit()
        return mi.id


@asynccontextmanager
async def _make_engine(
    index_id: int, trade_date: date
) -> AsyncIterator[IndexPriceAction]:
    """Build an engine wired to a real repo; closes the session on exit.

    The session must be closed so the DB cleanup between tests does not
    block on open transactions/locks.
    """
    session: AsyncSession = _factory()()
    try:
        engine = IndexPriceAction(INDEX_CODE, trade_date)
        engine.set_repo(MarketIndexPriceRepository(session))
        yield engine
    finally:
        await session.close()


@asynccontextmanager
async def _make_empty_engine(trade_date: date) -> AsyncIterator[IndexPriceAction]:
    """Build an engine with no seeded history; closes the session on exit."""
    session: AsyncSession = _factory()()
    try:
        engine = IndexPriceAction(INDEX_CODE, trade_date)
        engine.set_repo(MarketIndexPriceRepository(session))
        yield engine
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# BASIC PRICE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_price_returns_ohlc_and_change():
    """basic_price returns open/high/low/close/previous_close/change_percent."""
    index_id = await _seed_index_and_history(n_sessions=5)
    async with _make_engine(index_id, date(2024, 1, 5)) as engine:
        result = await engine.basic_price()
        assert result["open"] is not None
        assert result["high"] is not None
        assert result["low"] is not None
        assert result["close"] is not None
        assert result["previous_close"] is not None
        assert result["change_percent"] is not None


@pytest.mark.asyncio
async def test_basic_price_missing_history_returns_none():
    """basic_price returns None fields when no history exists."""
    async with _make_empty_engine(date(2024, 1, 1)) as engine:
        result = await engine.basic_price()
        assert result["open"] is None
        assert result["high"] is None
        assert result["low"] is None
        assert result["close"] is None
        assert result["previous_close"] is None
        assert result["change_percent"] is None


# ---------------------------------------------------------------------------
# GAP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gap_returns_classification():
    """gap returns classification gap_up/gap_down/flat."""
    index_id = await _seed_index_and_history(n_sessions=5)
    async with _make_engine(index_id, date(2024, 1, 5)) as engine:
        result = await engine.gap()
        assert result["classification"] in ("gap_up", "gap_down", "flat/no_gap")
        assert "gap_points" in result
        assert "gap_percent" in result


@pytest.mark.asyncio
async def test_gap_up_classification():
    """A positive open - previous_close is classified gap_up."""
    index_id = await _seed_index_and_history(n_sessions=5)
    # Override the last row to force a gap up.
    factory = _factory()
    async with factory() as session:
        rows = (
            await session.scalars(
                select(MarketIndexPrice)
                .where(MarketIndexPrice.market_index_id == index_id)
                .order_by(MarketIndexPrice.trade_date.desc())
            )
        ).all()
        last = rows[0]
        last.open = last.close + Decimal("50")
        last.previous_close = last.close - Decimal("10")
        await session.commit()

    async with _make_engine(index_id, last.trade_date) as engine:
        result = await engine.gap()
        assert result["classification"] == "gap_up"
        assert result["gap_points"] > 0


# ---------------------------------------------------------------------------
# RETURNS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_insufficient_history():
    """returns returns None for horizons with insufficient history."""
    index_id = await _seed_index_and_history(n_sessions=5)
    async with _make_engine(index_id, date(2024, 1, 5)) as engine:
        result = await engine.returns()
        # 5 sessions is enough for 1-session but not 5/20/60.
        assert result[1] is not None
        assert result[5] is None
        assert result[20] is None
        assert result[60] is None


@pytest.mark.asyncio
async def test_returns_sufficient_history():
    """returns computes 1/5/20/60 session returns with enough history."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.returns()
        assert result[1] is not None
        assert result[5] is not None
        assert result[20] is not None
        assert result[60] is not None


# ---------------------------------------------------------------------------
# MOVING AVERAGES
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_moving_averages_return_smas():
    """moving_averages returns SMA values and above/below/distance."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.moving_averages()
        assert result["sma_20"] is not None
        assert result["sma_50"] is not None
        assert result["sma_100"] is not None
        assert result["sma_200"] is not None
        assert result["sma_20_above"] in (True, False)
        assert result["sma_20_distance_percent"] is not None


@pytest.mark.asyncio
async def test_moving_averages_insufficient_history():
    """moving_averages returns None for windows with insufficient data."""
    index_id = await _seed_index_and_history(n_sessions=10)
    async with _make_engine(index_id, date(2024, 1, 10)) as engine:
        result = await engine.moving_averages()
        assert result["sma_20"] is None
        assert result["sma_50"] is None
        assert result["sma_100"] is None
        assert result["sma_200"] is None


# ---------------------------------------------------------------------------
# TREND
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trend_classification():
    """trend returns one of strong_bullish/bullish/neutral/bearish/strong_bearish."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.trend()
        assert result["trend"] in (
            "strong_bullish",
            "bullish",
            "neutral",
            "bearish",
            "strong_bearish",
        )


@pytest.mark.asyncio
async def test_trend_rising_market_is_bullish():
    """A steadily rising market should be classified bullish or strong_bullish."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.trend()
        assert result["trend"] in ("bullish", "strong_bullish")


# ---------------------------------------------------------------------------
# MOMENTUM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_momentum_returns():
    """momentum returns 5/20/60 session returns dict."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.momentum()
        assert 5 in result
        assert 20 in result
        assert 60 in result
        assert result[5] is not None
        assert result[20] is not None
        assert result[60] is not None


# ---------------------------------------------------------------------------
# 52-WEEK POSITION
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_52_week_position():
    """fifty_two_week_position returns high/low/position values."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.fifty_two_week_position()
        assert result["fifty_two_week_high"] is not None
        assert result["fifty_two_week_low"] is not None
        assert result["distance_from_52w_high_percent"] is not None
        assert result["distance_from_52w_low_percent"] is not None
        assert result["position_in_52w_range_percent"] is not None


@pytest.mark.asyncio
async def test_52_week_position_insufficient_history():
    """fifty_two_week_position returns None when < 252 sessions."""
    index_id = await _seed_index_and_history(n_sessions=100)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=99)) as engine:
        result = await engine.fifty_two_week_position()
        assert result["fifty_two_week_high"] is None
        assert result["fifty_two_week_low"] is None
        assert result["position_in_52w_range_percent"] is None


# ---------------------------------------------------------------------------
# DAILY RANGE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_range():
    """daily_range returns day_range_percent."""
    index_id = await _seed_index_and_history(n_sessions=5)
    async with _make_engine(index_id, date(2024, 1, 5)) as engine:
        result = await engine.daily_range()
        assert result["day_range_percent"] is not None
        assert result["high"] is not None
        assert result["low"] is not None
        assert result["open"] is not None


# ---------------------------------------------------------------------------
# 20-SESSION AVERAGE DAILY MOVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_twenty_session_avg_move():
    """twenty_session_avg_daily_move returns average absolute move or None."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.twenty_session_avg_daily_move()
        assert result is not None
        assert result > 0


@pytest.mark.asyncio
async def test_twenty_session_avg_move_insufficient_history():
    """twenty_session_avg_daily_move returns None with < 21 sessions."""
    index_id = await _seed_index_and_history(n_sessions=10)
    async with _make_engine(index_id, date(2024, 1, 10)) as engine:
        result = await engine.twenty_session_avg_daily_move()
        assert result is None


# ---------------------------------------------------------------------------
# PRICE-ACTION SCORE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_action_score_bounded():
    """price_action_score returns value clamped to -100..+100."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.price_action_score()
        score = result["price_action_score"]
        assert -100 <= score <= 100


@pytest.mark.asyncio
async def test_price_action_score_breakdown():
    """price_action_score includes score_breakdown dict."""
    index_id = await _seed_index_and_history(n_sessions=260)
    async with _make_engine(index_id, date(2024, 1, 1) + timedelta(days=259)) as engine:
        result = await engine.price_action_score()
        breakdown = result["score_breakdown"]
        assert "trend" in breakdown
        assert "momentum" in breakdown
        assert "moving_average_position" in breakdown
        assert "fifty_two_week_position" in breakdown
        assert "gap" in breakdown


@pytest.mark.asyncio
async def test_price_action_score_missing_data_returns_none_not_zero():
    """Missing values in breakdown remain None, not fabricated zeros."""
    async with _make_empty_engine(date(2024, 1, 1)) as engine:
        result = await engine.price_action_score()
        breakdown = result["score_breakdown"]
        # Momentum and 52-week position are None when no data.
        assert breakdown["momentum"] is None
        assert breakdown["fifty_two_week_position"] is None


# ---------------------------------------------------------------------------
# NO-LOOK-AHEAD REGRESSION TEST (mandatory)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_look_ahead_historical_calculation_unchanged_by_future():
    """Historical price-action must not change when future rows are inserted.

    Pattern:
      1. Insert historical prices through date D.
      2. Calculate price action for D.
      3. Save the result.
      4. Insert extreme price records dated AFTER D.
      5. Recalculate D.
      6. Assert the historical result is unchanged.
    """
    factory = _factory()
    async with factory() as session:
        mi = MarketIndex(
            code=INDEX_CODE,
            name="Nifty 50",
            country="INDIA",
            exchange="NSE",
            category="indian_index",
            provider_symbol="NIFTY50",
            is_active=True,
        )
        session.add(mi)
        await session.flush()

        # 1. Insert historical prices through date D (260 sessions).
        base_date = date(2024, 1, 1)
        d = base_date + timedelta(days=259)
        for i in range(260):
            close = Decimal("10000") + Decimal("10") * i
            trade_date = base_date + timedelta(days=i)
            session.add(
                _price(
                    mi.id,
                    trade_date,
                    close,
                    previous_close=close - Decimal("10") if i > 0 else None,
                )
            )
        await session.commit()

    # 2. Calculate price action for D.
    async with _make_engine(mi.id, d) as engine_d:
        before = {
            "basic": await engine_d.basic_price(),
            "returns": await engine_d.returns(),
            "sma": await engine_d.moving_averages(),
            "trend": await engine_d.trend(),
            "momentum": await engine_d.momentum(),
            "fifty_two_week": await engine_d.fifty_two_week_position(),
            "score": await engine_d.price_action_score(),
        }

    # 4. Insert extreme price records dated AFTER D.
    async with factory() as session:
        for i in range(1, 6):
            future_date = d + timedelta(days=i)
            session.add(
                _price(
                    mi.id,
                    future_date,
                    Decimal("999999"),
                    open_price=Decimal("999999"),
                    high=Decimal("999999"),
                    low=Decimal("999999"),
                    previous_close=Decimal("999999"),
                )
            )
        await session.commit()

    # 5. Recalculate D.
    async with _make_engine(mi.id, d) as engine_d2:
        after = {
            "basic": await engine_d2.basic_price(),
            "returns": await engine_d2.returns(),
            "sma": await engine_d2.moving_averages(),
            "trend": await engine_d2.trend(),
            "momentum": await engine_d2.momentum(),
            "fifty_two_week": await engine_d2.fifty_two_week_position(),
            "score": await engine_d2.price_action_score(),
        }

    # 6. Assert the historical result is unchanged.
    assert after["basic"] == before["basic"]
    assert after["returns"] == before["returns"]
    assert after["sma"] == before["sma"]
    assert after["trend"] == before["trend"]
    assert after["momentum"] == before["momentum"]
    assert after["fifty_two_week"] == before["fifty_two_week"]
    assert after["score"] == before["score"]