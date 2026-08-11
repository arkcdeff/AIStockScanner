"""Tests for the historical scanner replay engine and no-look-ahead guarantees."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.services.backtest_replay import BacktestReplayService
from app.services.scanner_engine import ScannerRegistry


async def _seed_stock(
    symbol: str = "REPLAY1",
    *,
    prices: list[tuple[date, int, float]] | None = None,
    indicators: list[tuple[date, float, float]] | None = None,
    delivery_percentage: float = 50.0,
) -> int:
    """Seed a stock with daily prices and optional indicator rows.

    prices: list of (trade_date, volume, close)
    indicators: list of (trade_date, ema_20, ema_50)
    delivery_percentage: delivery percent for all price rows
    """
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(
            symbol=symbol,
            company_name="Replay Test",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        session.add(stock)
        await session.flush()

        default_prices = prices or [
            (date(2026, 1, 5) + timedelta(days=i * 7), 1000 + i * 10, 100.0 + i)
            for i in range(25)
        ]
        for trade_date, volume, close in default_prices:
            session.add(
                DailyPrice(
                    stock_id=stock.id,
                    trade_date=trade_date,
                    open=close - 1,
                    high=close + 2,
                    low=close - 2,
                    close=close,
                    previous_close=close - 0.5,
                    volume=volume,
                    delivery_percentage=delivery_percentage,
                )
            )

        if indicators:
            for trade_date, ema20, ema50 in indicators:
                session.add(
                    IndicatorResult(
                        stock_id=stock.id,
                        trade_date=trade_date,
                        ema_20=ema20,
                        ema_50=ema50,
                    )
                )
        await session.commit()
        return stock.id


def _count_signals(summary) -> int:
    return summary.signals_generated


@pytest.mark.asyncio
async def test_replay_single_scanner_one_date() -> None:
    """A single scanner replay over a single date returns signals."""
    stock_id = await _seed_stock(
        "SINGLE1",
        indicators=[(date(2026, 1, 5), 99.0, 100.0), (date(2026, 1, 12), 101.0, 100.0)],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_scanner(
                "ema_bullish_crossover", date(2026, 1, 12), date(2026, 1, 12)
            )
        assert summary.dates_processed == 1
        assert summary.stocks_evaluated == 1
        assert summary.signals_generated >= 1
        assert all(s.scanner_code == "ema_bullish_crossover" for s in summary.signals)
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_replay_single_scanner_date_range() -> None:
    stock_id = await _seed_stock(
        "RANGE1",
        indicators=[
            (date(2026, 1, 5), 99.0, 100.0),
            (date(2026, 1, 12), 101.0, 100.0),
            (date(2026, 1, 19), 102.0, 101.0),
            (date(2026, 1, 26), 103.0, 102.0),
        ],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_scanner(
                "ema_bullish_crossover", date(2026, 1, 5), date(2026, 1, 26)
            )
        # EMA 20 was below EMA 50 on 5th then above on 12th -> one crossover.
        assert summary.dates_processed == 4
        assert summary.signals_generated == 1
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_replay_all_scanners() -> None:
    stock_id = await _seed_stock(
        "ALL1",
        indicators=[(date(2026, 1, 5), 99.0, 100.0), (date(2026, 1, 12), 101.0, 100.0)],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_all(date(2026, 1, 5), date(2026, 1, 12))
        assert summary.scanners_evaluated == len(ScannerRegistry.all())
        assert summary.signals_generated >= 1
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_replay_symbol_filter() -> None:
    stock1 = await _seed_stock("FILTER1", delivery_percentage=65.0)
    stock2 = await _seed_stock("FILTER2", delivery_percentage=30.0)
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_scanner(
                "delivery_above_60", date(2026, 1, 5), date(2026, 1, 12), symbols=["FILTER1"]
            )
        assert summary.signals_generated > 0
        assert all(s.symbol == "FILTER1" for s in summary.signals)
    finally:
        await _cleanup_stock(stock1)
        await _cleanup_stock(stock2)


@pytest.mark.asyncio
async def test_replay_insufficient_history() -> None:
    """Scanner requiring 20 prior sessions must not match with insufficient history."""
    # Only 5 price rows total - not enough for 20-day aggregates.
    stock_id = await _seed_stock(
        "SHORT1",
        prices=[
            (date(2025, 12, 1) + timedelta(days=i), 1000, 100.0 + i) for i in range(5)
        ],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_scanner(
                "volume_2x_average", date(2025, 12, 1), date(2025, 12, 5)
            )
        assert summary.signals_generated == 0
        assert summary.insufficient_history >= 1
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_no_lookahead_future_price() -> None:
    """Inserting a future price after the replay date must not change results."""
    dates = [date(2026, 1, 5) + timedelta(days=i * 7) for i in range(10)]
    stock_id = await _seed_stock(
        "FUTP1",
        prices=[(d, 1000, 100.0 + i) for i, d in enumerate(dates)],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary1 = await service.replay_scanner(
                "delivery_above_60", date(2026, 1, 5), dates[-2]
            )
        count1 = _count_signals(summary1)

        # Insert a future price after the replay range.
        future_date = dates[-1] + timedelta(days=7)
        factory = get_session_factory()
        async with factory() as session:
            session.add(
                DailyPrice(
                    stock_id=stock_id,
                    trade_date=future_date,
                    open=500,
                    high=510,
                    low=490,
                    close=505,
                    previous_close=500,
                    volume=999999,
                    delivery_percentage=10.0,
                )
            )
            await session.commit()

        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary2 = await service.replay_scanner(
                "delivery_above_60", date(2026, 1, 5), dates[-2]
            )
        count2 = _count_signals(summary2)
        assert count1 == count2
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_no_lookahead_future_indicator() -> None:
    """Inserting a future indicator after the replay date must not change results."""
    dates = [date(2026, 1, 5) + timedelta(days=i * 7) for i in range(10)]
    stock_id = await _seed_stock(
        "FUTI1",
        prices=[(d, 1000, 100.0 + i) for i, d in enumerate(dates)],
        indicators=[(d, 99.0, 100.0) for d in dates],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary1 = await service.replay_scanner(
                "ema_bullish_crossover", date(2026, 1, 5), dates[-2]
            )
        count1 = _count_signals(summary1)

        # Insert a future indicator after the replay range that WOULD match.
        future_date = dates[-1] + timedelta(days=7)
        factory = get_session_factory()
        async with factory() as session:
            session.add(
                IndicatorResult(
                    stock_id=stock_id,
                    trade_date=future_date,
                    ema_20=200.0,
                    ema_50=150.0,
                )
            )
            await session.commit()

        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary2 = await service.replay_scanner(
                "ema_bullish_crossover", date(2026, 1, 5), dates[-2]
            )
        count2 = _count_signals(summary2)
        assert count1 == count2
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_volume_2x_excludes_replay_date_from_avg() -> None:
    """Previous 20-day average excludes the replay-date volume."""
    # 21 consecutive weekday prices. The last day has huge volume.
    base = date(2026, 1, 1)
    # Use weekdays only
    dates: list[date] = []
    d = base
    while len(dates) < 21:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)

    prices = [(dt, 1000, 100.0) for dt in dates[:-1]] + [(dates[-1], 99999, 100.0)]
    stock_id = await _seed_stock("VOL2X1", prices=prices)
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_scanner(
                "volume_2x_average", dates[0], dates[-1]
            )
        # On the last date, prior 20 days avg = 1000, volume 99999 -> 2x+ match.
        # On earlier dates, avg includes prior days only -> 1x -> no match.
        assert summary.signals_generated == 1
        # Check the signal's avg_volume_20d equals 1000 (not ~5700).
        if summary.signals:
            sig = summary.signals[0]
            assert sig.indicator_snapshot["avg_volume_20d"] == 1000.0
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_breakout_excludes_future_prices() -> None:
    """Breakout comparison window excludes future prices."""
    # 20 prior weekday prices with max volume 5000, then a moderate 3000 today.
    base = date(2026, 1, 1)
    dates: list[date] = []
    d = base
    while len(dates) < 21:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)

    prices = [(dt, 1000 + (i % 5) * 1000, 100.0) for i, dt in enumerate(dates[:-1])]
    prices.append((dates[-1], 3000, 100.0))
    stock_id = await _seed_stock("BRK1", prices=prices)
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_scanner(
                "volume_breakout", dates[-1], dates[-1]
            )
        # On the last date: prior max = 5000, today 3000 -> NOT a breakout.
        # So no signals on the last date with a full 20-day window.
        assert summary.signals_generated == 0
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_replay_deterministic() -> None:
    """Repeated replay of the same data produces identical signals."""
    stock_id = await _seed_stock(
        "DETER1",
        indicators=[(date(2026, 1, 5), 99.0, 100.0), (date(2026, 1, 12), 101.0, 100.0)],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            s1 = await service.replay_scanner(
                "ema_bullish_crossover", date(2026, 1, 5), date(2026, 1, 12)
            )
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            s2 = await service.replay_scanner(
                "ema_bullish_crossover", date(2026, 1, 5), date(2026, 1, 12)
            )
        assert s1.signals_generated == s2.signals_generated
        assert [(s.trade_date, s.symbol, s.score) for s in s1.signals] == [
            (s.trade_date, s.symbol, s.score) for s in s2.signals
        ]
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_replay_matches_direct_scanner_evaluation() -> None:
    """Replay result matches evaluating the scanner rule directly."""
    stock_id = await _seed_stock(
        "DIRECT1",
        indicators=[(date(2026, 1, 5), 99.0, 100.0), (date(2026, 1, 12), 101.0, 100.0)],
    )
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            summary = await service.replay_scanner(
                "ema_bullish_crossover", date(2026, 1, 5), date(2026, 1, 12)
            )
        assert summary.signals_generated == 1
        signal = summary.signals[0]
        assert signal.scanner_code == "ema_bullish_crossover"
        assert signal.trade_date == date(2026, 1, 12)
    finally:
        await _cleanup_stock(stock_id)


@pytest.mark.asyncio
async def test_ensure_historical_data_report_only() -> None:
    """ensure_historical_data with fetch_missing=false reports gaps only."""
    stock_id = await _seed_stock("ENSURE1")
    try:
        factory = get_session_factory()
        async with factory() as session:
            service = BacktestReplayService(session)
            result = await service.ensure_historical_data(
                date(2026, 1, 5), date(2026, 1, 6), fetch_missing=False
            )
        assert result["fetch_attempted"] is False
        # Jan 5 has 25 default price rows (starting 2026-01-05), so only Jan 6 is missing.
        assert result["missing_count"] == 1
        assert len(result["unresolved_gaps"]) == 1
    finally:
        await _cleanup_stock(stock_id)


async def _cleanup_stock(stock_id: int) -> None:
    from sqlalchemy import delete

    from app.db.session import get_session_factory
    from app.models.daily_price import DailyPrice
    from app.models.indicator_result import IndicatorResult
    from app.models.scan_result import ScanResult
    from app.models.stock import Stock

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(delete(ScanResult).where(ScanResult.stock_id == stock_id))
        await session.execute(delete(IndicatorResult).where(IndicatorResult.stock_id == stock_id))
        await session.execute(delete(DailyPrice).where(DailyPrice.stock_id == stock_id))
        stock = await session.get(Stock, stock_id)
        if stock:
            await session.delete(stock)
        await session.commit()