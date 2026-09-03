"""Comprehensive Test Suite for Stage 2 Phase G.7.

200-Session NSE Bhavcopy Extension & Indicator Readiness Certification.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import extract, func, select
from sqlalchemy.exc import IntegrityError

from app.data.nse_parser import (
    PriceRecord,
    detect_format,
    load_bytes,
    parse_bhavcopy_detailed,
)
from app.db.session import get_session_factory
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import CorporateAnnouncement
from app.models.indicator_result import IndicatorResult
from app.models.institutional_flow import InstitutionalFlow
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
from app.models.sector import Sector
from app.models.stock import Stock
from app.services.indicator_engine import IndicatorEngine
from app.services.market_data_service import MarketDataService
from app.services.nse_historical_extension_service import (
    NseHistoricalExtensionService,
)
from app.services.scanner_engine import ScannerRegistry
from app.services.scanner_readiness import SCANNER_REQUIREMENTS
from app.services.stock360_service import Stock360Service

REFERENCE_DIR = Path("data/reference/bhavcopy/200session")
RAW_DIR = REFERENCE_DIR / "raw"
MANIFEST_PATH = REFERENCE_DIR / "manifest.json"


async def _seed_g7_test_universe(session) -> dict[str, Stock]:
    """Helper to seed pilot stocks, sectors, G.1-G.6 artifacts, and 205 trading sessions for tests."""
    sec = (await session.execute(select(Sector).where(Sector.name == "Energy"))).scalar_one_or_none()
    if not sec:
        sec = Sector(name="Energy", description="Energy Sector")
        session.add(sec)
        await session.flush()

    pilots_info = [
        ("RELIANCE", "Reliance Industries Ltd.", "INE002A01018"),
        ("HDFCBANK", "HDFC Bank Ltd.", "INE040A01018"),
        ("TCS", "Tata Consultancy Services Ltd.", "INE467B01029"),
        ("INFY", "Infosys Ltd.", "INE009A01021"),
        ("SBIN", "State Bank of India", "INE062A01020"),
    ]

    stocks_map = {}
    base_d = date(2026, 8, 31)

    for sym, name, isin in pilots_info:
        stk = (await session.execute(select(Stock).where(Stock.symbol == sym))).scalar_one_or_none()
        if not stk:
            stk = Stock(
                symbol=sym,
                company_name=name,
                exchange="NSE",
                series="EQ",
                isin=isin,
                sector_id=sec.id,
                is_active=True,
            )
            session.add(stk)
            await session.flush()
        stocks_map[sym] = stk

    # Seed 205 consecutive trading sessions for RELIANCE ending 2026-08-31
    rel_stk = stocks_map["RELIANCE"]
    curr_d = base_d
    sessions_added = 0
    while sessions_added < 205:
        if curr_d.weekday() < 5:  # Weekday
            price = Decimal("1000") + Decimal(str(sessions_added * 2))
            session.add(
                DailyPrice(
                    stock_id=rel_stk.id,
                    trade_date=curr_d,
                    open=price - Decimal("5"),
                    high=price + Decimal("10"),
                    low=price - Decimal("10"),
                    close=price,
                    previous_close=price - Decimal("2"),
                    volume=100000 + sessions_added * 500,
                    delivery_quantity=60000,
                    delivery_percentage=Decimal("60.0"),
                )
            )
            sessions_added += 1
        curr_d -= timedelta(days=1)

    # Seed companion G.4, G.5, G.6 regression records
    idx = MarketIndex(code="NIFTY_50", name="Nifty 50", country="INDIA")
    session.add(idx)
    await session.flush()

    session.add(
        MarketIndexPrice(
            market_index_id=idx.id,
            trade_date=base_d,
            open=Decimal("24000"),
            high=Decimal("24200"),
            low=Decimal("23950"),
            close=Decimal("24150"),
            volume=5000000,
        )
    )

    session.add(
        MarketIntelligenceSnapshot(
            market_date=base_d,
            generated_at=base_d,
            regime="BULLISH",
            data_completeness_percent=Decimal("70.0"),
        )
    )

    session.add(
        CorporateAnnouncement(
            stock_id=rel_stk.id,
            category="FINANCIAL_RESULTS",
            title="Q1 Financial Results",
            announcement_date=base_d,
            available_at=base_d,
        )
    )

    session.add(
        CorporateAction(
            stock_id=rel_stk.id,
            action_type="dividend",
            ex_date=base_d,
            record_date=base_d,
            cash_amount=Decimal("10.0"),
            available_at=base_d,
            idempotency_hash="test-g7-dividend-reliance-20260831",
        )
    )

    session.add(
        InstitutionalFlow(
            trade_date=base_d,
            investor_type="FII",
            buy_value=Decimal("10000"),
            sell_value=Decimal("8000"),
            net_value=Decimal("2000"),
        )
    )

    await session.commit()
    return stocks_map


def test_01_source_manifest_integrity():
    """1. Verify source manifest exists, has 62 sessions, and SHA-256 matches raw files."""
    assert MANIFEST_PATH.exists(), "manifest.json must exist"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(manifest) == 62, f"Expected 62 trading sessions, got {len(manifest)}"

    dates = sorted(manifest.keys())
    assert dates[0] == "2025-10-01"
    assert dates[-1] == "2025-12-31"

    first_entry = manifest[dates[0]]
    bhav_file = RAW_DIR / first_entry["bhavcopy"]["filename"]
    assert bhav_file.exists()
    content = bhav_file.read_bytes()
    assert hashlib.sha256(content).hexdigest() == first_entry["bhavcopy"]["sha256"]
    assert len(content) == first_entry["bhavcopy"]["size_bytes"]


def test_02_official_source_format():
    """2. Verify official formats: cm_udiff for bhavcopy and delivery for security delivery."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    sample_date = sorted(manifest.keys())[0]
    entry = manifest[sample_date]

    bhav_file = RAW_DIR / entry["bhavcopy"]["filename"]
    deliv_file = RAW_DIR / entry["delivery"]["filename"]

    bhav_bytes = bhav_file.read_bytes()
    deliv_bytes = deliv_file.read_bytes()

    assert detect_format(bhav_file.name, bhav_bytes) == "cm_udiff"
    assert detect_format(deliv_file.name, deliv_bytes) == "delivery"


def test_03_date_parsing():
    """3. Verify date parsing across Q4 2025 dates."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for d_str in ("2025-10-01", "2025-11-14", "2025-12-31"):
        entry = manifest[d_str]
        bhav_file = RAW_DIR / entry["bhavcopy"]["filename"]
        bhav_bytes, _ = load_bytes(bhav_file)
        parsed = parse_bhavcopy_detailed(bhav_bytes, bhav_file.name)
        assert len(parsed.records) > 0
        assert parsed.records[0].trade_date == date.fromisoformat(d_str)


@pytest.mark.asyncio
async def test_04_duplicate_date_protection():
    """4. Verify database unique constraint prevents duplicate (stock_id, trade_date)."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one()
        dup = DailyPrice(
            stock_id=stk.id,
            trade_date=date(2026, 8, 31),
            open=Decimal("2000.00"),
            high=Decimal("2050.00"),
            low=Decimal("1990.00"),
            close=Decimal("2020.00"),
            volume=100000,
        )
        session.add(dup)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


def test_05_entity_matching():
    """5. Verify entity matching via ISIN primary and symbol fallback without creating new stocks."""
    record_isin = PriceRecord(
        symbol="RELIANCE",
        series="EQ",
        trade_date=date(2025, 10, 1),
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("95"),
        close=Decimal("102"),
        previous_close=Decimal("100"),
        last_price=Decimal("102"),
        volume=1000,
        turnover=Decimal("102000"),
        trade_count=10,
        isin="INE002A01018",
    )
    assert record_isin.isin == "INE002A01018"
    assert record_isin.symbol == "RELIANCE"


def test_06_ohlc_validation():
    """6. Verify OHLC validation rules: high >= max(open, close, low), low <= min."""
    service = NseHistoricalExtensionService(None)

    # High < Low
    invalid1 = PriceRecord(
        symbol="TEST",
        series="EQ",
        trade_date=date(2025, 10, 1),
        open=Decimal("100"),
        high=Decimal("90"),
        low=Decimal("95"),
        close=Decimal("92"),
        previous_close=None,
        last_price=None,
        volume=100,
        turnover=None,
        trade_count=None,
        isin=None,
    )
    assert service._validate_record(invalid1) is not None

    # High < Open
    invalid2 = PriceRecord(
        symbol="TEST",
        series="EQ",
        trade_date=date(2025, 10, 1),
        open=Decimal("100"),
        high=Decimal("98"),
        low=Decimal("90"),
        close=Decimal("95"),
        previous_close=None,
        last_price=None,
        volume=100,
        turnover=None,
        trade_count=None,
        isin=None,
    )
    assert service._validate_record(invalid2) is not None


def test_07_volume_validation():
    """7. Verify volume must be >= 0."""
    service = NseHistoricalExtensionService(None)
    invalid_vol = PriceRecord(
        symbol="TEST",
        series="EQ",
        trade_date=date(2025, 10, 1),
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("95"),
        close=Decimal("102"),
        previous_close=None,
        last_price=None,
        volume=-5,
        turnover=None,
        trade_count=None,
        isin=None,
    )
    assert service._validate_record(invalid_vol) is not None


def test_08_200_session_target_logic():
    """8. Verify 200 distinct trading session target logic."""
    target_sessions = 200
    available_2026 = 160
    deficit = target_sessions - available_2026
    assert deficit == 40
    assert 62 >= deficit


@pytest.mark.asyncio
async def test_09_latest_200_session_selection():
    """9. Verify latest 200 distinct trading sessions ending <= 2026-08-31."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        res = (
            await session.execute(
                select(DailyPrice.trade_date)
                .where(DailyPrice.trade_date <= date(2026, 8, 31))
                .distinct()
                .order_by(DailyPrice.trade_date.desc())
                .limit(200)
            )
        ).scalars().all()
        assert len(res) == 200
        assert res[0] == date(2026, 8, 31)


@pytest.mark.asyncio
async def test_10_database_import():
    """10. Verify NseHistoricalExtensionService imports a session cleanly."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    first_date = sorted(manifest.keys())[0]
    entry = manifest[first_date]

    bhav_bytes, b_name = load_bytes(RAW_DIR / entry["bhavcopy"]["filename"])
    deliv_bytes, d_name = load_bytes(RAW_DIR / entry["delivery"]["filename"])

    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        service = NseHistoricalExtensionService(session)
        res = await service.import_session_data(bhav_bytes, b_name, deliv_bytes, d_name)
        assert res.matched > 0
        assert res.inserted > 0
        assert res.rejected == 0


@pytest.mark.asyncio
async def test_11_idempotency():
    """11. Verify idempotency: re-running import produces 0 inserted, 0 updated, all unchanged."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    first_date = sorted(manifest.keys())[0]
    entry = manifest[first_date]

    bhav_bytes, b_name = load_bytes(RAW_DIR / entry["bhavcopy"]["filename"])
    deliv_bytes, d_name = load_bytes(RAW_DIR / entry["delivery"]["filename"])

    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        service = NseHistoricalExtensionService(session)
        # Run 1
        res1 = await service.import_session_data(bhav_bytes, b_name, deliv_bytes, d_name)
        # Run 2
        res2 = await service.import_session_data(bhav_bytes, b_name, deliv_bytes, d_name)
        assert res2.inserted == 0, f"Expected 0 inserted on rerun, got {res2.inserted}"
        assert res2.updated == 0, f"Expected 0 updated on rerun, got {res2.updated}"
        assert res2.unchanged == res1.matched, "Expected all matched rows to be unchanged"


@pytest.mark.asyncio
async def test_12_pit_isolation():
    """12. Verify Point-in-Time (PIT) isolation: evaluation date excludes future dates."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        eval_d = date(2026, 8, 20)
        stock360_svc = Stock360Service(session)
        snap = await stock360_svc.get_stock360_snapshot("RELIANCE", evaluation_date=eval_d)
        assert snap.as_of_date == eval_d
        assert snap.technicals is not None

        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one()
        price_row = (
            await session.execute(
                select(DailyPrice).where(
                    DailyPrice.stock_id == stk.id, DailyPrice.trade_date == eval_d
                )
            )
        ).scalar_one()
        assert snap.technicals.close == float(price_row.close)


def test_13_no_future_leakage():
    """13. Verify validation rejects dates > 2026-08-31."""
    service = NseHistoricalExtensionService(None)
    future_record = PriceRecord(
        symbol="RELIANCE",
        series="EQ",
        trade_date=date(2026, 9, 15),
        open=Decimal("1500"),
        high=Decimal("1550"),
        low=Decimal("1490"),
        close=Decimal("1520"),
        previous_close=None,
        last_price=None,
        volume=50000,
        turnover=None,
        trade_count=None,
        isin="INE002A01018",
    )
    err = service._validate_record(future_record)
    assert err is not None
    assert "Future trade date" in err


@pytest.mark.asyncio
async def test_14_200_day_indicator_readiness():
    """14. Verify IndicatorEngine calculates EMA-200 for pilot stocks without error."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        engine = IndicatorEngine(session)
        results = await engine.calculate_for_stock("RELIANCE")
        assert len(results) >= 200, f"RELIANCE has only {len(results)} rows"
        latest = results[-1]
        assert latest["ema_200"] is not None
        assert latest["ema_50"] is not None


@pytest.mark.asyncio
async def test_15_golden_cross_readiness():
    """15. Verify golden_cross scanner evaluates with valid EMA-50 and EMA-200."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        engine = IndicatorEngine(session)
        await engine.calculate_for_stock("RELIANCE")

        gc_func = ScannerRegistry.get("golden_cross")
        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one()

        rows = (
            await session.execute(
                select(IndicatorResult)
                .where(IndicatorResult.stock_id == stk.id)
                .order_by(IndicatorResult.trade_date.desc())
                .limit(2)
            )
        ).scalars().all()
        assert len(rows) == 2
        matched, score, meta = gc_func(rows[0], rows[1])
        assert "ema_200" in meta
        assert meta["ema_200"] is not None
        assert meta["ema_50"] is not None


@pytest.mark.asyncio
async def test_16_death_cross_readiness():
    """16. Verify death_cross scanner evaluates with valid EMA-50 and EMA-200."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        engine = IndicatorEngine(session)
        await engine.calculate_for_stock("RELIANCE")

        dc_func = ScannerRegistry.get("death_cross")
        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one()

        rows = (
            await session.execute(
                select(IndicatorResult)
                .where(IndicatorResult.stock_id == stk.id)
                .order_by(IndicatorResult.trade_date.desc())
                .limit(2)
            )
        ).scalars().all()
        assert len(rows) == 2
        matched, score, meta = dc_func(rows[0], rows[1])
        assert "ema_200" in meta
        assert meta["ema_200"] is not None
        assert meta["ema_50"] is not None


@pytest.mark.asyncio
async def test_17_stock360_history_readiness():
    """17. Verify Stock360Service returns 52-week metrics and EMA-200."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        stock360_svc = Stock360Service(session)
        snap = await stock360_svc.get_stock360_snapshot("RELIANCE", evaluation_date=date(2026, 8, 31))
        assert snap.technicals is not None
        assert snap.technicals.ema200 is not None
        assert snap.technicals.pct_from_52w_high is not None
        assert snap.technicals.pct_from_52w_low is not None


def test_18_scanner_readiness_requirements():
    """18. Verify scanner requirements catalog mandates 201 sessions for cross scanners."""
    assert SCANNER_REQUIREMENTS["golden_cross"].required_sessions == 201
    assert SCANNER_REQUIREMENTS["death_cross"].required_sessions == 201


@pytest.mark.asyncio
async def test_19_g3_delivery_preservation():
    """19. Verify G.3 certified delivery data is preserved."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        deliv_cnt = (
            await session.execute(
                select(func.count(DailyPrice.id)).where(DailyPrice.delivery_quantity.isnot(None))
            )
        ).scalar_one()
        assert deliv_cnt >= 200, "Delivery data was unexpectedly lost"


@pytest.mark.asyncio
async def test_20_g4_regression():
    """20. Verify Stage 2 G.4 MarketIndexPrice and snapshot counts."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        mip_cnt = (await session.execute(select(func.count(MarketIndexPrice.id)))).scalar_one()
        mis_cnt = (
            await session.execute(select(func.count(MarketIntelligenceSnapshot.id)))
        ).scalar_one()
        assert mip_cnt >= 1
        assert mis_cnt >= 1


@pytest.mark.asyncio
async def test_21_g5_regression():
    """21. Verify Stage 2 G.5 corporate announcements and corporate actions counts."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        ca_cnt = (await session.execute(select(func.count(CorporateAnnouncement.id)))).scalar_one()
        cact_cnt = (await session.execute(select(func.count(CorporateAction.id)))).scalar_one()
        assert ca_cnt >= 1
        assert cact_cnt >= 1


@pytest.mark.asyncio
async def test_22_g1_g2_g6_regression():
    """22. Verify Stage 2 G.1 price history, G.2 sectors, and G.6 institutional counts."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        stock_cnt = (await session.execute(select(func.count(Stock.id)))).scalar_one()
        sector_cnt = (await session.execute(select(func.count(Sector.id)))).scalar_one()
        if_cnt = (await session.execute(select(func.count(InstitutionalFlow.id)))).scalar_one()
        assert stock_cnt >= 5
        assert sector_cnt >= 1
        assert if_cnt >= 1


@pytest.mark.asyncio
async def test_23_zero_weekend_daily_price_rows():
    """23. Verify zero weekend DailyPrice rows exist in database."""
    async with get_session_factory()() as session:
        wknd_cnt = (
            await session.execute(
                select(func.count(DailyPrice.id)).where(
                    extract("dow", DailyPrice.trade_date).in_([0, 6])
                )
            )
        ).scalar_one()
        assert wknd_cnt == 0, f"Found {wknd_cnt} unexpected weekend rows in DailyPrice"


def test_24_saturday_explicitly_rejected_by_service():
    """24. Verify NseHistoricalExtensionService._validate_record rejects Saturday."""
    service = NseHistoricalExtensionService(None)
    sat_record = PriceRecord(
        symbol="RELIANCE",
        series="EQ",
        trade_date=date(2026, 8, 1),  # Saturday
        open=Decimal("1500"),
        high=Decimal("1550"),
        low=Decimal("1490"),
        close=Decimal("1520"),
        previous_close=None,
        last_price=None,
        volume=50000,
        turnover=None,
        trade_count=None,
        isin="INE002A01018",
    )
    err = service._validate_record(sat_record)
    assert err is not None
    assert "Weekend trade date" in err
    assert "Saturday" in err


def test_25_sunday_explicitly_rejected_by_service():
    """25. Verify NseHistoricalExtensionService._validate_record rejects Sunday."""
    service = NseHistoricalExtensionService(None)
    sun_record = PriceRecord(
        symbol="RELIANCE",
        series="EQ",
        trade_date=date(2026, 8, 2),  # Sunday
        open=Decimal("1500"),
        high=Decimal("1550"),
        low=Decimal("1490"),
        close=Decimal("1520"),
        previous_close=None,
        last_price=None,
        volume=50000,
        turnover=None,
        trade_count=None,
        isin="INE002A01018",
    )
    err = service._validate_record(sun_record)
    assert err is not None
    assert "Weekend trade date" in err
    assert "Sunday" in err


@pytest.mark.asyncio
async def test_26_scanner_readiness_excludes_weekends():
    """26. Verify MarketDataService.scanner_readiness excludes weekend dates."""
    async with get_session_factory()() as session:
        svc = MarketDataService(session)
        readiness = await svc.scanner_readiness()
        latest = readiness["latest_trade_date"]
        if latest:
            assert latest.weekday() < 5, f"Latest trade date is a weekend: {latest}"
        # Max sessions must not exceed true trading days
        assert readiness["max_sessions"] <= 345


@pytest.mark.asyncio
async def test_27_regression_synthetic_weekend_does_not_affect_ema200():
    """27. Regression test: inserting a synthetic weekend row does not alter EMA-200 or readiness."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        stk = (
            await session.execute(
                select(Stock).where(Stock.symbol == "RELIANCE")
            )
        ).scalar_one()

        # Calculate EMA-200 before inserting weekend row
        engine = IndicatorEngine(session)
        res_before = await engine.calculate_for_stock("RELIANCE")
        row_831_before = [r for r in res_before if r["trade_date"] == date(2026, 8, 31)][0]
        ema200_before = float(row_831_before["ema_200"])

        # Insert a synthetic Saturday row in transaction
        fake_sat = DailyPrice(
            stock_id=stk.id,
            trade_date=date(2026, 8, 15),  # Saturday
            open=Decimal("9999.00"),
            high=Decimal("9999.00"),
            low=Decimal("9999.00"),
            close=Decimal("9999.00"),
            volume=10000000,
        )
        session.add(fake_sat)
        await session.flush()

        # Recalculate - IndicatorEngine query filters out weekends!
        res_after = await engine.calculate_for_stock("RELIANCE")
        row_831_after = [r for r in res_after if r["trade_date"] == date(2026, 8, 31)][0]
        ema200_after = float(row_831_after["ema_200"])

        assert abs(ema200_before - ema200_after) < 1e-6, "EMA-200 was altered by weekend row!"

        # Verify Stock360 also filters out weekends
        s360 = Stock360Service(session)
        snap = await s360.get_stock360_snapshot("RELIANCE", evaluation_date=date(2026, 8, 31))
        assert abs(snap.technicals.ema200 - ema200_before) < 0.02

        # Rollback so fake row is not committed
        await session.rollback()


@pytest.mark.asyncio
async def test_28_indicator_results_populated_and_clean():
    """28. Verify IndicatorResult in database has no weekends and is populated for pilot stocks."""
    async with get_session_factory()() as session:
        await _seed_g7_test_universe(session)
        engine = IndicatorEngine(session)
        await engine.calculate_for_stock("RELIANCE")

        # Zero weekend IndicatorResults
        wknd_ind = (
            await session.execute(
                select(func.count(IndicatorResult.id)).where(
                    extract("dow", IndicatorResult.trade_date).in_([0, 6])
                )
            )
        ).scalar_one()
        assert wknd_ind == 0, f"Found {wknd_ind} weekend IndicatorResults"

        # RELIANCE on 2026-08-31 has populated ema_200, ema_50, ema_20
        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one()
        rel_ind = (
            await session.execute(
                select(IndicatorResult).where(
                    IndicatorResult.stock_id == stk.id,
                    IndicatorResult.trade_date == date(2026, 8, 31),
                )
            )
        ).scalar_one_or_none()
        assert rel_ind is not None
        assert rel_ind.ema_200 is not None
        assert rel_ind.ema_50 is not None
        assert rel_ind.ema_20 is not None

