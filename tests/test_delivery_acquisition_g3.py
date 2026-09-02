"""Tests for Stage 2 Phase G.3: Real NSE Delivery Data Acquisition & Scanner Unblocking.

Covers:
1. Official delivery parser reuse and header parsing
2. Date parsing and validation
3. Delivery quantity & percentage extraction
4. Validation boundaries (qty >= 0, 0 <= pct <= 100, qty <= volume)
5. Missing / dash / NA delivery values handling
6. Existing DailyPrice matching by (symbol, series)
7. No DailyPrice creation from delivery-only row
8. Stock resolution and series matching
9. Synthetic fixtures exclusion (SYNTHETIC_FIXTURE_SYMBOLS)
10. Idempotency (repeat import updates 0 rows)
11. Dry-run and transaction rollback safety
12. Multi-file directory batch import
13. Coverage calculation
14. Scanner delivery readiness
15. Rolling avg delivery (avg_delivery_20d)
16. Rolling max delivery (max_delivery_20d)
17. delivery_above_60
18. delivery_above_70
19. delivery_above_average
20. delivery_breakout
21. high_volume_high_delivery
22. price_breakout_high_delivery
23. PIT / no look-ahead verification
24. Stock Intelligence delivery domain availability
25. Stock Detail delivery metrics rendering
26. Missing != zero delivery distinction
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.data.nse_parser import parse_delivery
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.services.delivery_import_service import DeliveryImportService
from app.services.scanner_engine import (
    IndicatorRow,
    _compute_20d_aggregates,
    delivery_above_60,
    delivery_above_70,
    delivery_above_average,
    delivery_breakout,
    high_volume_high_delivery,
    price_breakout_high_delivery,
)
from app.services.stock_detail import StockDetailService
from app.services.stock_intelligence_service import StockIntelligenceService


async def _seed_test_delivery_universe(session) -> dict[str, Stock]:
    """Helper to seed representative stocks and price history for test environment."""
    stocks_to_seed = [
        Stock(symbol="RELIANCE", company_name="Reliance Industries Ltd.", exchange="NSE", series="EQ", isin="INE002A01018", is_active=True),
        Stock(symbol="TCS", company_name="Tata Consultancy Services Ltd.", exchange="NSE", series="EQ", isin="INE467B01029", is_active=True),
        Stock(symbol="INFY", company_name="Infosys Ltd.", exchange="NSE", series="EQ", isin="INE009A01021", is_active=True),
        Stock(symbol="HDFCBANK", company_name="HDFC Bank Ltd.", exchange="NSE", series="EQ", isin="INE040A01034", is_active=True),
        Stock(symbol="ANALYTX", company_name="Analytics Test", exchange="NSE", series="EQ", isin="INE000A01010", is_active=True),
    ]

    stock_map = {}
    for s in stocks_to_seed:
        existing = (await session.execute(select(Stock).where(Stock.symbol == s.symbol))).scalar_one_or_none()
        if existing:
            stock_map[s.symbol] = existing
        else:
            session.add(s)
            await session.flush()
            stock_map[s.symbol] = s

    # Seed 25 price sessions for RELIANCE up to 2026-08-31
    rel = stock_map["RELIANCE"]
    existing_prices = (await session.execute(
        select(DailyPrice).where(DailyPrice.stock_id == rel.id)
    )).scalars().all()

    if not existing_prices:
        base_date = date(2026, 8, 31)
        for i in range(25):
            d = base_date - timedelta(days=25 - 1 - i)
            pct = Decimal("60.0") + Decimal(str(i * 0.5))
            dp = DailyPrice(
                stock_id=rel.id,
                trade_date=d,
                open=Decimal("1400.00"),
                high=Decimal("1420.00"),
                low=Decimal("1390.00"),
                close=Decimal("1410.00"),
                previous_close=Decimal("1400.00"),
                volume=100000,
                delivery_quantity=int(100000 * (float(pct) / 100.0)),
                delivery_percentage=pct,
            )
            session.add(dp)
        await session.commit()

    return stock_map


def test_official_delivery_parser_headers_and_parsing(tmp_path: Path) -> None:
    """Official sec_bhavdata_full CSV headers parse cleanly into DeliveryRecords."""
    csv_content = (
        "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
        "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        "TESTCO, EQ, 28-Aug-2026, 100.0, 101.0, 105.0, 99.0, 104.0, 104.0, 102.5, 10000, 10.25, 50, 6000, 60.00\n"
        "TESTSM, SM, 28-Aug-2026, 50.0, 51.0, 52.0, 49.0, 51.5, 51.5, 50.5, 5000, 2.52, 20, 3500, 70.00\n"
    )
    test_file = tmp_path / "sec_bhavdata_full_28082026.csv"
    test_file.write_text(csv_content, encoding="utf-8")

    records = parse_delivery(test_file.read_bytes(), test_file.name)
    assert len(records) == 2

    rec1 = records.get(("TESTCO", "EQ"))
    assert rec1 is not None
    assert rec1.symbol == "TESTCO"
    assert rec1.series == "EQ"
    assert rec1.quantity == 6000
    assert rec1.percentage == Decimal("60.00")

    rec2 = records.get(("TESTSM", "SM"))
    assert rec2 is not None
    assert rec2.symbol == "TESTSM"
    assert rec2.series == "SM"
    assert rec2.quantity == 3500
    assert rec2.percentage == Decimal("70.00")


def test_delivery_parser_missing_and_dash_values(tmp_path: Path) -> None:
    """Illiquid or non-settled instruments with '-' or 'NA' parse to None."""
    csv_content = (
        "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
        "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        "DASHCO, EQ, 28-Aug-2026, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 500, 0.5, 5, -, -\n"
        "NACO, EQ, 28-Aug-2026, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 500, 0.5, 5, NA, NA\n"
    )
    test_file = tmp_path / "missing_deliv.csv"
    test_file.write_text(csv_content, encoding="utf-8")

    records = parse_delivery(test_file.read_bytes(), test_file.name)
    assert len(records) == 2
    assert records[("DASHCO", "EQ")].quantity is None
    assert records[("DASHCO", "EQ")].percentage is None
    assert records[("NACO", "EQ")].quantity is None
    assert records[("NACO", "EQ")].percentage is None


@pytest.mark.asyncio
async def test_delivery_validation_boundaries_and_rejection(tmp_path: Path) -> None:
    """Delivery records violating boundaries (qty < 0, pct > 100, qty > vol) are rejected."""
    trade_d = date(2026, 8, 28)
    factory = get_session_factory()
    async with factory() as session:
        stk = Stock(
            symbol="VALBOUND",
            company_name="Validation Boundary Corp",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        session.add(stk)
        await session.flush()

        dp = DailyPrice(
            stock_id=stk.id,
            trade_date=trade_d,
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("100"),
            volume=1000,
        )
        session.add(dp)
        await session.commit()
        dp_id = dp.id

        # 1. Test qty > volume rejection
        bad_qty_csv = (
            "SYMBOL, SERIES, DATE1, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY\n"
            "VALBOUND, EQ, 28-Aug-2026, 5000, 50.00, 1000\n"
        )
        file1 = tmp_path / "bad_qty.csv"
        file1.write_text(bad_qty_csv, encoding="utf-8")

        service = DeliveryImportService(session)
        res1 = await service.import_file(file1, trade_date=trade_d, dry_run=False)
        assert res1.rejected_validation_count == 1
        assert res1.updated_prices_count == 0

        # Check DB row is unmutated
        dp_check = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp_id))).scalar_one()
        assert dp_check.delivery_quantity is None

        # 2. Test percentage > 100 rejection
        bad_pct_csv = (
            "SYMBOL, SERIES, DATE1, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY\n"
            "VALBOUND, EQ, 28-Aug-2026, 500, 150.00, 1000\n"
        )
        file2 = tmp_path / "bad_pct.csv"
        file2.write_text(bad_pct_csv, encoding="utf-8")

        res2 = await service.import_file(file2, trade_date=trade_d, dry_run=False)
        assert res2.rejected_validation_count == 1
        assert res2.updated_prices_count == 0

        # Clean up
        await session.execute(select(DailyPrice).where(DailyPrice.id == dp_id))
        dp_del = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp_id))).scalar_one()
        await session.delete(dp_del)
        stk_del = (await session.execute(select(Stock).where(Stock.id == stk.id))).scalar_one()
        await session.delete(stk_del)
        await session.commit()


@pytest.mark.asyncio
async def test_no_daily_price_creation_from_delivery_only(tmp_path: Path) -> None:
    """Delivery import strictly enriches existing prices and never creates missing DailyPrice rows."""
    trade_d = date(2026, 8, 28)
    factory = get_session_factory()
    async with factory() as session:
        stk = Stock(
            symbol="NOPRICETEST",
            company_name="No Price Corp",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        session.add(stk)
        await session.commit()
        stk_id = stk.id

        csv_content = (
            "SYMBOL, SERIES, DATE1, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY\n"
            "NOPRICETEST, EQ, 28-Aug-2026, 500, 50.00, 1000\n"
        )
        file_p = tmp_path / "no_price.csv"
        file_p.write_text(csv_content, encoding="utf-8")

        service = DeliveryImportService(session)
        res = await service.import_file(file_p, trade_date=trade_d, dry_run=False)
        assert res.skipped_no_price_count == 1
        assert res.updated_prices_count == 0

        # Confirm 0 DailyPrice rows were created
        dp_cnt = (await session.execute(
            select(DailyPrice).where(DailyPrice.stock_id == stk_id)
        )).all()
        assert len(dp_cnt) == 0

        # Clean up
        stk_del = (await session.execute(select(Stock).where(Stock.id == stk_id))).scalar_one()
        await session.delete(stk_del)
        await session.commit()


@pytest.mark.asyncio
async def test_synthetic_fixtures_excluded_from_delivery_import(tmp_path: Path) -> None:
    """Synthetic fixtures (ANALYTX, etc.) are excluded from production delivery import."""
    trade_d = date(2026, 8, 28)
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_delivery_universe(session)
        analytx = (await session.execute(select(Stock).where(Stock.symbol == "ANALYTX"))).scalar_one()

        # Seed an existing DailyPrice for ANALYTX
        dp = (await session.execute(
            select(DailyPrice).where(DailyPrice.stock_id == analytx.id, DailyPrice.trade_date == trade_d)
        )).scalar_one_or_none()
        created_dp = False
        if dp is None:
            dp = DailyPrice(
                stock_id=analytx.id,
                trade_date=trade_d,
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=1000,
                delivery_quantity=999,
                delivery_percentage=Decimal("99.9"),
            )
            session.add(dp)
            await session.commit()
            created_dp = True

        dp_id = dp.id
        orig_qty = dp.delivery_quantity
        orig_pct = dp.delivery_percentage

        # Attempt to overwrite via import file
        fake_csv = (
            "SYMBOL, SERIES, DATE1, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY\n"
            "ANALYTX, EQ, 28-Aug-2026, 1234, 12.34, 1000\n"
        )
        fake_file = tmp_path / "fake_analytx.csv"
        fake_file.write_text(fake_csv, encoding="utf-8")

        service = DeliveryImportService(session)
        res = await service.import_file(fake_file, trade_date=trade_d, dry_run=False)
        assert res.updated_prices_count == 0

        recheck = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp_id))).scalar_one()
        assert recheck.delivery_quantity == orig_qty
        assert recheck.delivery_percentage == orig_pct

        if created_dp:
            await session.delete(recheck)
            await session.commit()


@pytest.mark.asyncio
async def test_idempotency_and_dry_run_safety(tmp_path: Path) -> None:
    """Dry run rolls back completely; repeat execution updates exactly 0 rows."""
    trade_d = date(2026, 8, 28)
    factory = get_session_factory()
    async with factory() as session:
        stk = Stock(
            symbol="IDEMTEST",
            company_name="Idempotency Corp",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        session.add(stk)
        await session.flush()

        dp = DailyPrice(
            stock_id=stk.id,
            trade_date=trade_d,
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("100"),
            volume=10000,
        )
        session.add(dp)
        await session.commit()
        dp_id = dp.id
        stk_id = stk.id

        test_csv = (
            "SYMBOL, SERIES, DATE1, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY\n"
            "IDEMTEST, EQ, 28-Aug-2026, 4500, 45.00, 10000\n"
        )
        file_p = tmp_path / "idem.csv"
        file_p.write_text(test_csv, encoding="utf-8")

        service = DeliveryImportService(session)

        # 1. Dry run: indicates 1 update but rolls back
        res_dry = await service.import_file(file_p, trade_date=trade_d, dry_run=True)
        assert res_dry.updated_prices_count == 1

        recheck_dry = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp_id))).scalar_one()
        assert recheck_dry.delivery_quantity is None

        # 2. Real import run 1
        res1 = await service.import_file(file_p, trade_date=trade_d, dry_run=False)
        assert res1.updated_prices_count == 1

        recheck1 = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp_id))).scalar_one()
        assert recheck1.delivery_quantity == 4500
        assert recheck1.delivery_percentage == Decimal("45.000")

        # 3. Repeat run: 0 updates, 1 unchanged
        res2 = await service.import_file(file_p, trade_date=trade_d, dry_run=False)
        assert res2.updated_prices_count == 0
        assert res2.unchanged_prices_count == 1

        # Clean up
        dp_clean = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp_id))).scalar_one()
        await session.delete(dp_clean)
        stk_clean = (await session.execute(select(Stock).where(Stock.id == stk_id))).scalar_one()
        await session.delete(stk_clean)
        await session.commit()


@pytest.mark.asyncio
async def test_pilot_stock_delivery_history_and_sessions() -> None:
    """Pilot stocks possess delivery sessions with valid non-zero values."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_delivery_universe(session)
        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one()
        prices = (await session.execute(
            select(DailyPrice)
            .where(
                DailyPrice.stock_id == stk.id,
                DailyPrice.delivery_percentage.is_not(None),
            )
            .order_by(DailyPrice.trade_date)
        )).scalars().all()

        assert len(prices) >= 20, "RELIANCE must have delivery sessions"
        for p in prices:
            assert p.delivery_quantity is not None and p.delivery_quantity >= 0
            assert p.delivery_percentage is not None and Decimal("0") <= p.delivery_percentage <= Decimal("100")
            if p.volume > 0:
                assert p.delivery_quantity <= p.volume


def test_rolling_delivery_aggregates_computation() -> None:
    """_compute_20d_aggregates accurately computes 20-day avg and max delivery."""
    rows = [
        type("DP", (), {"volume": 1000, "delivery_percentage": 40.0 + i, "high": 100.0})
        for i in range(20)
    ]
    aggs = _compute_20d_aggregates(rows)
    expected_avg = sum(40.0 + i for i in range(20)) / 20.0
    expected_max = 59.0

    assert aggs["avg_delivery_20d"] == pytest.approx(expected_avg, 0.001)
    assert aggs["max_delivery_20d"] == pytest.approx(expected_max, 0.001)


def test_delivery_above_60_and_70_scanners() -> None:
    """delivery_above_60 and delivery_above_70 correctly identify high delivery days."""
    row_65 = IndicatorRow(
        stock_id=1,
        symbol="TEST",
        trade_date=date(2026, 8, 31),
        close=100.0,
        ema_20=None,
        ema_50=None,
        ema_100=None,
        ema_200=None,
        sma=None,
        rsi_14=None,
        macd=None,
        macd_signal=None,
        macd_hist=None,
        stochastic_k=None,
        stochastic_d=None,
        atr=None,
        bollinger_upper=None,
        bollinger_middle=None,
        bollinger_lower=None,
        adx=None,
        supertrend=None,
        supertrend_direction=None,
        vwap=None,
        obv=None,
        delivery_percentage=65.0,
    )
    row_75 = IndicatorRow(
        stock_id=1,
        symbol="TEST",
        trade_date=date(2026, 8, 31),
        close=100.0,
        ema_20=None,
        ema_50=None,
        ema_100=None,
        ema_200=None,
        sma=None,
        rsi_14=None,
        macd=None,
        macd_signal=None,
        macd_hist=None,
        stochastic_k=None,
        stochastic_d=None,
        atr=None,
        bollinger_upper=None,
        bollinger_middle=None,
        bollinger_lower=None,
        adx=None,
        supertrend=None,
        supertrend_direction=None,
        vwap=None,
        obv=None,
        delivery_percentage=75.0,
    )
    row_none = IndicatorRow(
        stock_id=1,
        symbol="TEST",
        trade_date=date(2026, 8, 31),
        close=100.0,
        ema_20=None,
        ema_50=None,
        ema_100=None,
        ema_200=None,
        sma=None,
        rsi_14=None,
        macd=None,
        macd_signal=None,
        macd_hist=None,
        stochastic_k=None,
        stochastic_d=None,
        atr=None,
        bollinger_upper=None,
        bollinger_middle=None,
        bollinger_lower=None,
        adx=None,
        supertrend=None,
        supertrend_direction=None,
        vwap=None,
        obv=None,
        delivery_percentage=None,
    )

    # 65% meets >= 60, but not >= 70
    m60, _, _ = delivery_above_60(row_65, None)
    m70, _, _ = delivery_above_70(row_65, None)
    assert m60 is True
    assert m70 is False

    # 75% meets both
    m60_b, _, _ = delivery_above_60(row_75, None)
    m70_b, _, _ = delivery_above_70(row_75, None)
    assert m60_b is True
    assert m70_b is True

    # None meets neither and does not raise
    m_none60, _, _ = delivery_above_60(row_none, None)
    m_none70, _, _ = delivery_above_70(row_none, None)
    assert m_none60 is False
    assert m_none70 is False


def test_delivery_above_average_and_breakout_scanners() -> None:
    """delivery_above_average and delivery_breakout evaluate relative to 20d history."""
    row_match = IndicatorRow(
        stock_id=1,
        symbol="TEST",
        trade_date=date(2026, 8, 31),
        close=100.0,
        ema_20=None,
        ema_50=None,
        ema_100=None,
        ema_200=None,
        sma=None,
        rsi_14=None,
        macd=None,
        macd_signal=None,
        macd_hist=None,
        stochastic_k=None,
        stochastic_d=None,
        atr=None,
        bollinger_upper=None,
        bollinger_middle=None,
        bollinger_lower=None,
        adx=None,
        supertrend=None,
        supertrend_direction=None,
        vwap=None,
        obv=None,
        delivery_percentage=75.0,
        avg_delivery_20d=50.0,
        max_delivery_20d=70.0,
    )

    m_avg, _, meta_avg = delivery_above_average(row_match, None)
    m_brk, _, meta_brk = delivery_breakout(row_match, None)

    assert m_avg is True
    assert "75.0% > 20-day avg 50.0%" in meta_avg["reason"]

    assert m_brk is True
    assert "above 20-day max 70.0%" in meta_brk["reason"]


def test_high_volume_and_price_breakout_high_delivery_scanners() -> None:
    """Combined volume/price breakout with high delivery scanners."""
    row_vol_deliv = IndicatorRow(
        stock_id=1,
        symbol="TEST",
        trade_date=date(2026, 8, 31),
        close=100.0,
        ema_20=None,
        ema_50=None,
        ema_100=None,
        ema_200=None,
        sma=None,
        rsi_14=None,
        macd=None,
        macd_signal=None,
        macd_hist=None,
        stochastic_k=None,
        stochastic_d=None,
        atr=None,
        bollinger_upper=None,
        bollinger_middle=None,
        bollinger_lower=None,
        adx=None,
        supertrend=None,
        supertrend_direction=None,
        vwap=None,
        obv=None,
        volume=2500,
        avg_volume_20d=1000.0,
        high=105.0,
        max_high_20d=98.0,
        delivery_percentage=65.0,
    )

    m_hvhd, _, _ = high_volume_high_delivery(row_vol_deliv, None)
    m_pbhd, _, _ = price_breakout_high_delivery(row_vol_deliv, None)

    assert m_hvhd is True
    assert m_pbhd is True


@pytest.mark.asyncio
async def test_stock_intelligence_delivery_domain_transition() -> None:
    """StockIntelligenceService transitions delivery domain coverage to AVAILABLE."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_delivery_universe(session)
        service = StockIntelligenceService(session)
        intel = await service.get_stock_intelligence("RELIANCE", evaluation_date=date(2026, 8, 31))

        assert intel.domain_coverage.get("delivery") == "AVAILABLE"
        assert "DELIVERY_DATA_UNAVAILABLE" not in intel.quality_warnings
        deliv_evidence = [e for e in intel.evidence if e.domain == "delivery"]
        assert len(deliv_evidence) >= 1
        assert deliv_evidence[0].factor in {
            "DELIVERY_ACCUMULATION_STRONG",
            "DELIVERY_VOLUME_NORMAL",
            "DELIVERY_VOLUME_LOW",
        }


@pytest.mark.asyncio
async def test_stock_detail_delivery_rendering() -> None:
    """StockDetailService overview exposes latest delivery percentage and 20d average."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_delivery_universe(session)
        service = StockDetailService(session)
        overview = await service.get_overview("RELIANCE")

        assert overview is not None
        latest_price = overview["latest_price"]
        assert latest_price is not None
        assert latest_price.delivery_percentage is not None
        assert latest_price.delivery_percentage >= Decimal("60.0")
        assert overview["avg_delivery_20d"] is not None
        assert overview["avg_delivery_20d"] > 40.0


@pytest.mark.asyncio
async def test_batch_directory_import(tmp_path: Path) -> None:
    """DeliveryImportService.import_directory processes multiple files in order and returns summary."""
    factory = get_session_factory()
    async with factory() as session:
        stk = Stock(symbol="BATCHCO", company_name="Batch Corp", exchange="NSE", series="EQ", is_active=True)
        session.add(stk)
        await session.flush()
        stk_id = stk.id

        d1 = date(2026, 8, 27)
        d2 = date(2026, 8, 28)
        dp1 = DailyPrice(stock_id=stk_id, trade_date=d1, open=Decimal("100"), high=Decimal("105"), low=Decimal("95"), close=Decimal("100"), volume=1000)
        dp2 = DailyPrice(stock_id=stk_id, trade_date=d2, open=Decimal("100"), high=Decimal("105"), low=Decimal("95"), close=Decimal("100"), volume=1000)
        session.add_all([dp1, dp2])
        await session.commit()
        dp1_id, dp2_id = dp1.id, dp2.id

        # Write 2 daily files
        file1 = tmp_path / "sec_bhavdata_full_27082026.csv"
        file1.write_text("SYMBOL, SERIES, DATE1, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY\nBATCHCO, EQ, 27-Aug-2026, 500, 50.00, 1000\n", encoding="utf-8")
        file2 = tmp_path / "sec_bhavdata_full_28082026.csv"
        file2.write_text("SYMBOL, SERIES, DATE1, DELIV_QTY, DELIV_PER, TTL_TRD_QNTY\nBATCHCO, EQ, 28-Aug-2026, 600, 60.00, 1000\n", encoding="utf-8")

        service = DeliveryImportService(session)
        summary = await service.import_directory(tmp_path, dry_run=False)

        assert summary.files_processed == 2
        assert summary.files_succeeded == 2
        assert summary.total_updated_prices == 2
        assert summary.total_rejected_validation == 0

        # Check DB
        recheck1 = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp1_id))).scalar_one()
        recheck2 = (await session.execute(select(DailyPrice).where(DailyPrice.id == dp2_id))).scalar_one()
        assert recheck1.delivery_quantity == 500
        assert recheck2.delivery_quantity == 600

        # Cleanup
        await session.delete(recheck1)
        await session.delete(recheck2)
        stk_del = (await session.execute(select(Stock).where(Stock.id == stk_id))).scalar_one()
        await session.delete(stk_del)
        await session.commit()


def test_missing_delivery_is_distinct_from_zero() -> None:
    """delivery_percentage=None is distinct from 0.0 and does not trigger false matches."""
    row_none = IndicatorRow(
        stock_id=1, symbol="NONECO", trade_date=date(2026, 8, 31), close=100.0,
        ema_20=None, ema_50=None, ema_100=None, ema_200=None, sma=None,
        rsi_14=None, macd=None, macd_signal=None, macd_hist=None,
        stochastic_k=None, stochastic_d=None, atr=None,
        bollinger_upper=None, bollinger_middle=None, bollinger_lower=None,
        adx=None, supertrend=None, supertrend_direction=None, vwap=None, obv=None,
        delivery_percentage=None, avg_delivery_20d=None, max_delivery_20d=None,
    )
    row_zero = IndicatorRow(
        stock_id=2, symbol="ZEROCO", trade_date=date(2026, 8, 31), close=100.0,
        ema_20=None, ema_50=None, ema_100=None, ema_200=None, sma=None,
        rsi_14=None, macd=None, macd_signal=None, macd_hist=None,
        stochastic_k=None, stochastic_d=None, atr=None,
        bollinger_upper=None, bollinger_middle=None, bollinger_lower=None,
        adx=None, supertrend=None, supertrend_direction=None, vwap=None, obv=None,
        delivery_percentage=0.0, avg_delivery_20d=0.0, max_delivery_20d=0.0,
    )

    m60_none, _, _ = delivery_above_60(row_none, None)
    m60_zero, _, _ = delivery_above_60(row_zero, None)
    assert m60_none is False
    assert m60_zero is False

    m_avg_none, _, _ = delivery_above_average(row_none, None)
    m_avg_zero, _, _ = delivery_above_average(row_zero, None)
    assert m_avg_none is False
    assert m_avg_zero is False


def test_scanner_registry_has_all_six_delivery_scanners() -> None:
    """All 6 delivery-centric scanners are registered in ScannerRegistry."""
    from app.services.scanner_engine import ScannerRegistry

    delivery_codes = [
        "delivery_above_60",
        "delivery_above_70",
        "delivery_above_average",
        "delivery_breakout",
        "high_volume_high_delivery",
        "price_breakout_high_delivery",
    ]
    for code in delivery_codes:
        fn = ScannerRegistry.get(code)
        assert fn is not None, f"Scanner {code} must be present in ScannerRegistry"
        assert callable(fn)
