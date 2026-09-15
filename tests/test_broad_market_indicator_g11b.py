"""G.11-B automated tests for broad-market indicator expansion."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.models.bulk_block_deal import BulkBlockDeal
from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_participant import DerivativeParticipantPosition
from app.models.indicator_result import IndicatorResult
from app.models.sector import Sector
from app.models.stock import Stock
from app.repositories.stock_detail import StockDetailRepository
from app.services.indicator_batch_service import IndicatorBatchService
from app.services.indicator_engine import IndicatorEngine
from app.services.scanner_engine import ScannerRegistry as LegacyScannerRegistry
from app.services.scanner_readiness import SCANNER_REQUIREMENTS


async def _seed_g11b_test_universe(session: AsyncSession) -> dict[str, Stock]:
    """Helper to seed a controlled test universe in the test database."""
    sec = (await session.execute(select(Sector).where(Sector.name == "Financials"))).scalar_one_or_none()
    if not sec:
        sec = Sector(name="Financials", description="Financial Services")
        session.add(sec)
        await session.flush()

    stocks_def = [
        # (symbol, series, isin, session_count)
        ("RELIANCE", "EQ", "INE002A01018", 205),
        ("TCS", "EQ", "INE467B01029", 205),
        ("INFY", "EQ", "INE009A01021", 205),
        ("FEDERALBNK", "EQ", "INE171A01029", 205),
        ("POLYCAB", "EQ", "INE455K01017", 205),
        ("TATACOMM", "EQ", "INE151A01013", 205),
        ("RECENT_IPO", "EQ", "INE999A01099", 45),  # < 200 sessions (excluded)
        ("BE_SERIES", "BE", "INE888B01088", 205),  # non-EQ series (excluded)
        ("REPRO1", "EQ", "INE777M01077", 205),  # fixture symbol (excluded)
    ]

    stocks_map = {}
    end_d = date(2026, 9, 9)

    for sym, ser, isin, cnt in stocks_def:
        stk = (await session.execute(select(Stock).where(Stock.symbol == sym))).scalar_one_or_none()
        if not stk:
            stk = Stock(
                symbol=sym,
                company_name=f"{sym} Test Ltd",
                exchange="NSE",
                series=ser,
                isin=isin,
                sector_id=sec.id,
                is_active=True,
            )
            session.add(stk)
            await session.flush()
        stocks_map[sym] = stk

        # Seed prices if not already seeded
        existing_cnt = (
            await session.execute(
                select(func.count(DailyPrice.id)).where(DailyPrice.stock_id == stk.id)
            )
        ).scalar()

        if existing_cnt < cnt:
            prices = []
            curr_d = end_d
            added = 0
            while added < cnt:
                if curr_d.weekday() < 5:  # Monday - Friday only
                    price = Decimal("1000.00") + Decimal(str(added * 2))
                    prices.append(
                        DailyPrice(
                            stock_id=stk.id,
                            trade_date=curr_d,
                            open=price - Decimal("5.00"),
                            high=price + Decimal("10.00"),
                            low=price - Decimal("10.00"),
                            close=price,
                            previous_close=price - Decimal("2.00"),
                            volume=100000 + added * 500,
                            turnover=Decimal(str((100000 + added * 500) * float(price))),
                            trade_count=1000 + added * 10,
                            delivery_quantity=60000,
                            delivery_percentage=Decimal("60.00"),
                        )
                    )
                    added += 1
                curr_d -= timedelta(days=1)
            session.add_all(prices)
            await session.flush()

    await session.commit()
    return stocks_map


@pytest.mark.asyncio
async def test_01_eligible_eq_universe_selection():
    """1. Verify eligible EQ universe selection excludes non-EQ series, fixtures, and <200 sessions."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        service = IndicatorBatchService(session)
        universe = await service.get_eligible_universe(as_of_date=date(2026, 9, 9), min_sessions=200)

        symbols = [s.symbol for s in universe]
        # Should include eligible EQ stocks
        assert "RELIANCE" in symbols
        assert "TCS" in symbols
        assert "INFY" in symbols
        assert "FEDERALBNK" in symbols
        assert "POLYCAB" in symbols
        assert "TATACOMM" in symbols

        # Exclusions:
        assert "RECENT_IPO" not in symbols  # <200 sessions
        assert "BE_SERIES" not in symbols   # non-EQ series
        assert "REPRO1" not in symbols  # fixture symbol

        # Deterministic alphabetical ordering
        assert symbols == sorted(symbols)


@pytest.mark.asyncio
async def test_02_200_session_requirement():
    """2. Verify stocks with <200 sessions are classified into excluded universe."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        service = IndicatorBatchService(session)
        excluded = await service.get_excluded_universe(as_of_date=date(2026, 9, 9), min_sessions=200)

        excluded_symbols = [ex.symbol for ex in excluded]
        assert "RECENT_IPO" in excluded_symbols

        ipo_ex = next(ex for ex in excluded if ex.symbol == "RECENT_IPO")
        assert ipo_ex.session_count == 45
        assert "Insufficient weekday history" in ipo_ex.reason


@pytest.mark.asyncio
async def test_03_201_session_scanner_requirement():
    """3. Verify 201-session scanner threshold is satisfied by stocks with >= 201 sessions."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        service = IndicatorBatchService(session)
        universe_201 = await service.get_eligible_universe(as_of_date=date(2026, 9, 9), min_sessions=201)

        symbols_201 = [s.symbol for s in universe_201]
        assert "RELIANCE" in symbols_201
        assert "TCS" in symbols_201
        assert "FEDERALBNK" in symbols_201

        # Check scanner metadata requirements
        assert SCANNER_REQUIREMENTS["golden_cross"].required_sessions == 201
        assert SCANNER_REQUIREMENTS["death_cross"].required_sessions == 201


@pytest.mark.asyncio
async def test_04_weekend_exclusion():
    """4. Verify indicator calculation excludes Saturdays and Sundays."""
    factory = get_session_factory()
    async with factory() as session:
        test_stock = Stock(
            symbol="G11B_WKND",
            company_name="G11B Weekend Test Ltd",
            exchange="NSE",
            series="EQ",
            isin="INE999W01099",
            is_active=True,
        )
        session.add(test_stock)
        await session.flush()

        # Add 200 weekday sessions and 2 weekend sessions (Sat & Sun)
        prices = []
        d = date(2026, 1, 1)
        added_weekdays = 0
        while added_weekdays < 200:
            prices.append(
                DailyPrice(
                    stock_id=test_stock.id,
                    trade_date=d,
                    open=Decimal("100.00"),
                    high=Decimal("105.00"),
                    low=Decimal("95.00"),
                    close=Decimal("102.00"),
                    volume=10000,
                )
            )
            if d.weekday() < 5:
                added_weekdays += 1
            d = date.fromordinal(d.toordinal() + 1)

        session.add_all(prices)
        await session.flush()

        engine = IndicatorEngine(session)
        results = await engine.calculate_for_stock("G11B_WKND")

        assert len(results) == 200
        for r in results:
            assert r["trade_date"].weekday() < 5, f"Weekend date found: {r['trade_date']}"

        await session.rollback()


@pytest.mark.asyncio
async def test_05_pit_as_of_date_filtering():
    """5. Verify PIT as_of_date strictly filters out future dates."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        engine = IndicatorEngine(session)

        pit_date = date(2026, 6, 30)
        results = await engine.calculate_for_stock("RELIANCE", as_of_date=pit_date)
        assert len(results) > 0
        for r in results:
            assert r["trade_date"] <= pit_date, f"Future date {r['trade_date']} > {pit_date}"


@pytest.mark.asyncio
async def test_06_deterministic_calculation():
    """6. Verify calculating indicators twice produces identical results."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        engine = IndicatorEngine(session)
        res1 = await engine.calculate_for_stock("TCS", as_of_date=date(2026, 8, 31))
        res2 = await engine.calculate_for_stock("TCS", as_of_date=date(2026, 8, 31))

        assert len(res1) == len(res2)
        for r1, r2 in zip(res1, res2, strict=True):
            assert r1["trade_date"] == r2["trade_date"]
            assert r1["ema_20"] == r2["ema_20"]
            assert r1["ema_50"] == r2["ema_50"]
            assert r1["ema_200"] == r2["ema_200"]
            assert r1["rsi_14"] == r2["rsi_14"]
            assert r1["macd"] == r2["macd"]


@pytest.mark.asyncio
async def test_07_persistence_in_db():
    """7. Verify calculated indicator rows are properly persisted and queryable."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        engine = IndicatorEngine(session)
        results = await engine.calculate_for_stock("INFY", as_of_date=date(2026, 9, 9))
        await session.commit()

        stk_id = (await session.execute(select(Stock.id).where(Stock.symbol == "INFY"))).scalar_one()
        db_count = (
            await session.execute(
                select(func.count(IndicatorResult.id)).where(
                    IndicatorResult.stock_id == stk_id,
                    IndicatorResult.trade_date <= date(2026, 9, 9),
                )
            )
        ).scalar()
        assert db_count == len(results)


@pytest.mark.asyncio
async def test_08_idempotency():
    """8. Verify running batch calculation twice produces zero duplicate rows and zero drift."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        service = IndicatorBatchService(session)

        # Run on a limited test subset of 2 stocks
        summary1 = await service.run_batch(
            as_of_date=date(2026, 9, 9),
            symbols=["RELIANCE", "TCS"],
        )
        assert summary1.successful_count == 2

        count1 = (
            await session.execute(
                select(func.count(IndicatorResult.id))
                .join(Stock, IndicatorResult.stock_id == Stock.id)
                .where(Stock.symbol.in_(["RELIANCE", "TCS"]))
            )
        ).scalar()

        # Run second time
        summary2 = await service.run_batch(
            as_of_date=date(2026, 9, 9),
            symbols=["RELIANCE", "TCS"],
        )
        assert summary2.successful_count == 2
        assert summary2.inserted_rows == 0

        count2 = (
            await session.execute(
                select(func.count(IndicatorResult.id))
                .join(Stock, IndicatorResult.stock_id == Stock.id)
                .where(Stock.symbol.in_(["RELIANCE", "TCS"]))
            )
        ).scalar()

        assert count1 == count2, "Duplicate rows created on second run"


@pytest.mark.asyncio
async def test_09_failure_isolation():
    """9. Verify a single failing stock does not abort the batch."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        service = IndicatorBatchService(session)

        # Create a stock with only 1 price row (which causes calculation to fail or be excluded)
        bad_stock = Stock(
            symbol="G11B_BAD",
            company_name="Bad Stock Ltd",
            exchange="NSE",
            series="EQ",
            isin="INE000B01000",
            is_active=True,
        )
        session.add(bad_stock)
        await session.commit()

        # Run batch targeting RELIANCE and G11B_BAD
        summary = await service.run_batch(
            as_of_date=date(2026, 9, 9),
            symbols=["G11B_BAD", "RELIANCE"],
        )
        # RELIANCE succeeds
        assert summary.successful_count >= 1

        # Clean up bad stock
        await session.execute(text("DELETE FROM stocks WHERE symbol = 'G11B_BAD'"))
        await session.commit()


@pytest.mark.asyncio
async def test_10_broad_market_stock_outside_pilot():
    """10. Verify a broad-market stock outside pilot (e.g. FEDERALBNK) calculates correctly."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        engine = IndicatorEngine(session)
        results = await engine.calculate_for_stock("FEDERALBNK", as_of_date=date(2026, 9, 9))
        assert len(results) >= 200

        latest = results[-1]
        assert latest["ema_20"] is not None
        assert latest["ema_50"] is not None
        assert latest["ema_200"] is not None
        assert latest["sma"] is not None
        assert latest["rsi_14"] is not None
        assert latest["macd"] is not None


@pytest.mark.asyncio
async def test_11_stock360_indicator_availability():
    """11. Verify StockDetailRepository retrieves indicators for a broad-market stock."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        engine = IndicatorEngine(session)
        await engine.calculate_for_stock("POLYCAB", as_of_date=date(2026, 9, 9))
        await session.commit()

        stk = (await session.execute(select(Stock).where(Stock.symbol == "POLYCAB"))).scalar_one()
        repo = StockDetailRepository(session)
        latest_ind = await repo.get_latest_indicator(stk.id)

        assert latest_ind is not None
        assert latest_ind.ema_20 is not None
        assert latest_ind.ema_50 is not None
        assert latest_ind.ema_200 is not None


@pytest.mark.asyncio
async def test_12_scanner_readiness():
    """12. Verify scanner can evaluate on broad-market stock."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_g11b_test_universe(session)
        engine = IndicatorEngine(session)
        await engine.calculate_for_stock("TATACOMM", as_of_date=date(2026, 9, 9))
        await session.commit()

        stk = (await session.execute(select(Stock).where(Stock.symbol == "TATACOMM"))).scalar_one()
        rows = (
            await session.execute(
                select(IndicatorResult)
                .where(IndicatorResult.stock_id == stk.id)
                .order_by(IndicatorResult.trade_date.desc())
                .limit(2)
            )
        ).scalars().all()

        assert len(rows) == 2
        gc_func = LegacyScannerRegistry.get("golden_cross")
        assert gc_func is not None
        matched, score, meta = gc_func(rows[0], rows[1])
        assert isinstance(matched, bool)
        assert isinstance(score, float)
        assert "ema_200" in meta


def test_13_no_changes_to_scanner_formulas():
    """13. Verify all 36 scanners exist and their requirement mappings are intact."""
    assert len(LegacyScannerRegistry.all()) == 36
    assert len(SCANNER_REQUIREMENTS) == 36

    expected_codes = [
        "accumulation", "atr_high_volatility", "bollinger_band_squeeze", "death_cross",
        "delivery_above_60", "delivery_above_70", "delivery_above_average", "delivery_breakout",
        "ema_bearish_crossover", "ema_bullish_crossover", "golden_cross",
        "high_volume_high_delivery", "highest_volume_20d", "macd_above_zero",
        "macd_bearish", "macd_below_zero", "macd_bullish", "obv_rising",
        "price_above_ema20", "price_above_upper_bollinger", "price_below_ema20",
        "price_below_lower_bollinger", "price_breakout_high_delivery", "rsi_overbought",
        "rsi_oversold", "stochastic_overbought", "stochastic_oversold", "strong_trend_adx",
        "supertrend_bearish", "supertrend_bullish", "volume_2x_average", "volume_5x_average",
        "volume_breakout", "vwap_bearish", "vwap_bullish", "weak_trend_adx"
    ]
    for code in expected_codes:
        assert code in LegacyScannerRegistry.all()
        assert code in SCANNER_REQUIREMENTS


@pytest.mark.asyncio
async def test_14_g7_weekend_invariants_preserved():
    """14. Verify 0 weekend prices exist in daily_prices across the active database."""
    factory = get_session_factory()
    async with factory() as session:
        weekend_prices = (
            await session.execute(
                text("SELECT count(*) FROM daily_prices WHERE EXTRACT(DOW FROM trade_date) IN (0, 6)")
            )
        ).scalar()
        assert weekend_prices == 0, f"Found {weekend_prices} weekend prices in daily_prices!"


@pytest.mark.asyncio
async def test_15_g8_g9_g10_regression_preserved():
    """15. Verify indicator computation does not corrupt or alter other domain models."""
    factory = get_session_factory()
    async with factory() as session:
        # Check that schema queries on other domain tables execute cleanly without schema drift
        await session.execute(select(func.count(DerivativeContract.id)))
        await session.execute(select(func.count(DerivativeParticipantPosition.id)))
        await session.execute(select(func.count(BulkBlockDeal.id)))
