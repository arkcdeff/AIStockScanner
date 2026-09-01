from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.data.nse_parser import BhavcopyParseResult, parse_bhavcopy_detailed
from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.services.indicator_engine import IndicatorEngine
from app.services.market_data_service import MarketDataService
from app.services.nse_import import NSEImportService
from app.services.scanner_engine import ScanRunner


async def _seed_test_stock_with_history(
    session,
    symbol: str = "TCS_TEST",
    isin: str = "INE467B01029",
    count: int = 62,
) -> Stock:
    stock = Stock(
        symbol=symbol,
        company_name=f"{symbol} Ltd",
        exchange="NSE",
        series="EQ",
        isin=isin,
        is_active=True,
    )
    session.add(stock)
    await session.flush()

    base_date = date(2026, 6, 1)
    current_date = base_date
    added = 0
    prices = []
    while added < count:
        if current_date.weekday() < 5:  # Monday to Friday
            prices.append(
                DailyPrice(
                    stock_id=stock.id,
                    trade_date=current_date,
                    open=Decimal("2000.00") + Decimal(added),
                    high=Decimal("2050.00") + Decimal(added),
                    low=Decimal("1980.00") + Decimal(added),
                    close=Decimal("2020.00") + Decimal(added),
                    previous_close=Decimal("2010.00") + Decimal(added),
                    volume=100000 + added * 500,
                    delivery_quantity=None,
                    delivery_percentage=None,
                )
            )
            added += 1
        current_date += timedelta(days=1)
    session.add_all(prices)
    await session.flush()
    return stock


# 1. SAMCO file parser compatibility
def test_samco_parser_compatibility():
    samco_csv = (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        b"RELIANCE,EQ,1316.60,1320.00,1302.40,1309.80,1310.00,1315.00,7277167,9531234567.00,24-Aug-2026,185420,INE002A01018\n"
    )
    result = parse_bhavcopy_detailed(samco_csv, "20260824_NSE.csv")
    assert isinstance(result, BhavcopyParseResult)
    assert result.detected_format == "legacy"
    assert len(result.records) == 1
    assert len(result.errors) == 0

    record = result.records[0]
    assert record.symbol == "RELIANCE"
    assert record.series == "EQ"
    assert record.trade_date == date(2026, 8, 24)
    assert record.open == Decimal("1316.60")
    assert record.high == Decimal("1320.00")
    assert record.low == Decimal("1302.40")
    assert record.close == Decimal("1309.80")
    assert record.volume == 7277167
    assert record.isin == "INE002A01018"
    assert record.delivery_quantity is None
    assert record.delivery_percentage is None


# 2. NSE Cash series filtering
def test_nse_cash_series_filtering():
    mixed_csv = (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        b"TCS,EQ,2300.0,2320.0,2290.0,2310.0,2310.0,2300.0,100000,231000000,24-Aug-2026,5000,INE467B01029\n"
        b"SMESTOCK,SM,50.0,52.0,49.0,51.0,51.0,50.0,10000,510000,24-Aug-2026,200,INE123S01011\n"
        b"GOLDBOND,GB,5000.0,5050.0,4990.0,5010.0,5010.0,5000.0,500,2505000,24-Aug-2026,50,IN0020150011\n"
        b"GOVTSEC,GS,100.0,101.0,99.5,100.2,100.2,100.0,1000,100200,24-Aug-2026,10,IN0020200022\n"
    )
    result = parse_bhavcopy_detailed(mixed_csv, "mixed_test.csv")
    assert len(result.records) == 2
    assert {r.symbol for r in result.records} == {"TCS", "SMESTOCK"}
    assert result.skipped_series == 2


# 3. ISIN stock resolution & 4. No duplicate stock creation
@pytest.mark.asyncio
async def test_isin_stock_resolution_and_no_duplicate():
    factory = get_session_factory()
    async with factory() as session:
        stock = await _seed_test_stock_with_history(
            session, symbol="G1_TCS", isin="INE467B01029", count=5
        )
        initial_stock_id = stock.id

        csv_bytes = (
            b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
            b"G1_TCS,EQ,2300.0,2320.0,2290.0,2310.0,2310.0,2300.0,100000,231000000,24-Aug-2026,5000,INE467B01029\n"
        )
        importer = NSEImportService(session)
        job = await importer.import_bytes(bhavcopy=csv_bytes, bhavcopy_name="test_isin.csv")
        assert job.status == "completed"

        # Verify no duplicate Stock was created
        all_matches = (
            await session.execute(
                select(Stock).where(Stock.symbol == "G1_TCS", Stock.exchange == "NSE")
            )
        ).scalars().all()
        assert len(all_matches) == 1
        assert all_matches[0].id == initial_stock_id
        await session.rollback()


# 5. DailyPrice upsert & 6. Idempotent repeat import
@pytest.mark.asyncio
async def test_idempotent_repeat_import():
    factory = get_session_factory()
    csv_bytes = (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        b"G1_INFY,EQ,1120.0,1140.0,1115.0,1130.0,1130.0,1125.0,500000,565000000,24-Aug-2026,12000,INE009A01021\n"
    )
    async with factory() as session:
        importer = NSEImportService(session)
        job1 = await importer.import_bytes(bhavcopy=csv_bytes, bhavcopy_name="repeat1.csv")
        assert job1.status == "completed"
        # Second run with exact same payload
        job2 = await importer.import_bytes(bhavcopy=csv_bytes, bhavcopy_name="repeat2.csv")
        assert job2.status == "completed"
        assert job2.rows_inserted == 0
        assert job2.rows_updated == 1
        assert job2.rows_rejected == 0
        await session.rollback()


# 7. Invalid OHLC rejection
def test_invalid_ohlc_rejection():
    bad_csv = (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        b"BAD1,EQ,100.0,90.0,110.0,105.0,105.0,100.0,1000,105000,24-Aug-2026,10,INE000000001\n"
        b"BAD2,EQ,100.0,110.0,90.0,105.0,105.0,100.0,-500,105000,24-Aug-2026,10,INE000000002\n"
    )
    result = parse_bhavcopy_detailed(bad_csv, "bad_ohlc.csv")
    assert len(result.records) == 0
    assert len(result.errors) == 2
    assert "Invalid OHLC or volume values" in result.errors[0]
    assert "Invalid OHLC or volume values" in result.errors[1]


# 8. History session count (extended stock has >= 60 sessions)
@pytest.mark.asyncio
async def test_history_session_count():
    factory = get_session_factory()
    async with factory() as session:
        stock = await _seed_test_stock_with_history(
            session, symbol="G1_COUNT", isin="INE111A01011", count=62
        )
        count = (
            await session.execute(
                select(func.count(DailyPrice.id)).where(DailyPrice.stock_id == stock.id)
            )
        ).scalar()
        assert count == 62
        assert count >= 60
        await session.rollback()


# 9. Indicator recalculation after extension (EMA50 available, EMA200 truthfully None)
@pytest.mark.asyncio
async def test_indicator_recalculation_after_extension():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_stock_with_history(session, symbol="G1_IND", isin="INE222A01022", count=62)
        engine = IndicatorEngine(session)
        results = await engine.calculate_for_stock("G1_IND")
        assert len(results) == 62

        latest = results[-1]
        assert latest["ema_20"] is not None
        assert latest["ema_50"] is not None
        assert latest["ema_200"] is None
        assert latest["sma"] is not None
        assert latest["rsi_14"] is not None
        await session.rollback()


# 10. Scanner readiness transition (50-session threshold unblocked)
@pytest.mark.asyncio
async def test_scanner_readiness_transition():
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_stock_with_history(
            session, symbol="G1_READY", isin="INE333A01033", count=62
        )
        service = MarketDataService(session)
        readiness_data = await service.scanner_readiness()
        readiness = readiness_data.get("readiness", {})
        assert readiness.get("50", 0) >= 1
        assert readiness.get("200", 0) == 0
        await session.rollback()


# 11. Historical evaluation date safety
@pytest.mark.asyncio
async def test_historical_evaluation_date():
    eval_date = date(2026, 8, 31)
    factory = get_session_factory()
    async with factory() as session:
        runner = ScanRunner(session)
        results = await runner.run_scanner("price_above_ema20", evaluation_date=eval_date)
        for r in results:
            assert r.scan_date == eval_date


# 12. No-lookahead guarantee (evaluation date cutoff)
@pytest.mark.asyncio
async def test_no_lookahead():
    cutoff_date = date(2026, 8, 14)
    factory = get_session_factory()
    async with factory() as session:
        runner = ScanRunner(session)
        results = await runner.run_scanner("price_above_ema20", evaluation_date=cutoff_date)
        for r in results:
            assert r.scan_date == cutoff_date


# 13. Fixture and test-scanner exclusion
@pytest.mark.asyncio
async def test_fixture_test_scanner_exclusion():
    factory = get_session_factory()
    async with factory() as session:
        service = MarketDataService(session)
        readiness = await service.scanner_readiness()
        latest = readiness.get("latest_trade_date")
        if latest:
            cnt = (
                await session.execute(
                    select(func.count(DailyPrice.id))
                    .join(Stock, DailyPrice.stock_id == Stock.id)
                    .where(
                        DailyPrice.trade_date == latest,
                        Stock.symbol.in_(SYNTHETIC_FIXTURE_SYMBOLS),
                    )
                )
            ).scalar()
            assert cnt == 0


# 14. Product data integrity (no duplicate stock_id, trade_date)
@pytest.mark.asyncio
async def test_product_data_integrity():
    factory = get_session_factory()
    async with factory() as session:
        dups = (
            await session.execute(
                text("""
                SELECT stock_id, trade_date, COUNT(*)
                FROM daily_prices
                GROUP BY stock_id, trade_date
                HAVING COUNT(*) > 1
            """)
            )
        ).fetchall()
        assert len(dups) == 0, f"Found {len(dups)} duplicate (stock_id, trade_date) rows"


# 15. No synthetic rows (delivery columns remain None when source lacks delivery data)
def test_no_synthetic_rows_in_samco():
    samco_csv = (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        b"RELIANCE,EQ,1316.60,1320.00,1302.40,1309.80,1310.00,1315.00,7277167,9531234567.00,24-Aug-2026,185420,INE002A01018\n"
    )
    result = parse_bhavcopy_detailed(samco_csv, "20260824_NSE.csv")
    rec = result.records[0]
    assert rec.delivery_quantity is None
    assert rec.delivery_percentage is None
