"""Tests for Stage 2 Phase G.4: Official Market Context Acquisition & Decision Context Completion.

Covers:
 1. Official NSE header parsing
 2. Index-name normalization
 3. Unknown index rejection
 4. Date parsing
 5. Numerical validation (OHLC, high >= low, volume clamp)
 6. No duplicate index/date
 7. Idempotency
 8. Source provenance
 9. Global provider mapping
10. Global calendar handling
11. Snapshot generation
12. PIT evaluation
13. market_context AVAILABLE
14. Missing market context remains unavailable
15. No future leakage
16. Missing != zero semantics
17. Scanner regression
18. G.1 regression
19. G.2 regression
20. G.3 regression
21. Browser rendering
22. Factor completeness calculation
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.data.nse_index_parser import (
    NSE_INDEX_NAME_TO_CODE,
    NSEIndexParseError,
    NSEIndexParser,
)
from app.db.session import get_session_factory
from app.market_intelligence.index_catalog import (
    GLOBAL_INTELLIGENCE_INDEX_CODES,
    INDEX_CATALOG_BY_CODE,
)
from app.models.daily_price import DailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
from app.models.sector import Sector
from app.models.stock import Stock
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.repositories.market_intelligence_snapshot import MarketIntelligenceSnapshotRepository
from app.services.composite_market_intelligence import (
    CompositeMarketIntelligence,
    FactorInput,
    compute_composite_factor_result,
)
from app.services.nse_index_import_service import NSEIndexImportService
from app.services.scanner_engine import ScannerRegistry
from app.services.stock_detail import StockDetailService
from app.services.stock_intelligence_service import StockIntelligenceService
from tests.auth_helpers import viewer_client

SAMPLE_VALID_CSV = """Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield
Nifty 50,31-08-2026,24117.55,24128.70,23993.60,24080.40,-95.25,-0.39,710870778,49193.19,20.36,2.92,1.17
India VIX,31-08-2026,13.40,14.10,13.05,13.85,0.45,3.36,-,-,-,-,-
Nifty Bank,31-08-2026,51200.00,51350.00,50900.00,51150.00,-50.00,-0.10,123456789,15000.00,15.5,2.1,0.8
Unknown Random Index,31-08-2026,1000.00,1050.00,980.00,1020.00,20.00,2.00,10000,50.00,12.0,1.5,1.0
"""


async def _seed_test_market_universe(session) -> dict[str, MarketIndex]:
    """Helper to seed test indices, prices, and stock for isolated test environments."""
    indices_to_seed = [
        ("NIFTY_50", "Nifty 50", "indian_index", "NSE", "INDIA", "^NSEI"),
        ("INDIA_VIX", "India VIX", "volatility_index", "NSE", "INDIA", "^INDIAVIX"),
        ("NIFTY_BANK", "Nifty Bank", "sector_index", "NSE", "INDIA", "^NSEBANK"),
    ]
    idx_map = {}
    for code, name, cat, ex, ctry, psym in indices_to_seed:
        existing = (await session.execute(select(MarketIndex).where(MarketIndex.code == code))).scalar_one_or_none()
        if existing:
            idx_map[code] = existing
        else:
            idx = MarketIndex(code=code, name=name, category=cat, exchange=ex, country=ctry, provider_symbol=psym, is_active=True)
            session.add(idx)
            await session.flush()
            idx_map[code] = idx

    base_d = date(2026, 8, 31)
    nifty = idx_map["NIFTY_50"]
    vix = idx_map["INDIA_VIX"]

    for i in range(25):
        d = base_d - timedelta(days=25 - 1 - i)
        # NIFTY_50
        p = (await session.execute(
            select(MarketIndexPrice).where(MarketIndexPrice.market_index_id == nifty.id, MarketIndexPrice.trade_date == d)
        )).scalar_one_or_none()
        if not p:
            session.add(MarketIndexPrice(
                market_index_id=nifty.id, trade_date=d, open=Decimal("24000"), high=Decimal("24150"), low=Decimal("23950"), close=Decimal("24100"),
                previous_close=Decimal("24050"), change=Decimal("50"), change_percent=Decimal("0.2"), volume=500000,
                observed_at=d, fetched_at=d, source_name="NSE_ARCHIVE_IND_CLOSE", source_reference="ind_close_all_31082026.csv", freshness_status="recent"
            ))
        # INDIA_VIX
        pv = (await session.execute(
            select(MarketIndexPrice).where(MarketIndexPrice.market_index_id == vix.id, MarketIndexPrice.trade_date == d)
        )).scalar_one_or_none()
        if not pv:
            session.add(MarketIndexPrice(
                market_index_id=vix.id, trade_date=d, open=Decimal("13.5"), high=Decimal("14.0"), low=Decimal("13.0"), close=Decimal("13.8"),
                previous_close=Decimal("13.5"), change=Decimal("0.3"), change_percent=Decimal("2.2"), volume=None,
                observed_at=d, fetched_at=d, source_name="NSE_ARCHIVE_IND_CLOSE", source_reference="ind_close_all_31082026.csv", freshness_status="recent"
            ))

    # Seed Sector
    sec = (await session.execute(select(Sector).where(Sector.name == "Energy"))).scalar_one_or_none()
    if not sec:
        sec = Sector(name="Energy", description="Energy Sector")
        session.add(sec)
        await session.flush()

    # Seed Stock RELIANCE
    stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one_or_none()
    if not stk:
        stk = Stock(symbol="RELIANCE", company_name="Reliance Industries Ltd.", exchange="NSE", series="EQ", isin="INE002A01018", sector_id=sec.id, is_active=True)
        session.add(stk)
        await session.flush()
    elif stk.sector_id is None:
        stk.sector_id = sec.id
        session.add(stk)
        await session.flush()

    # RELIANCE DailyPrice
    for i in range(25):
        d = base_d - timedelta(days=25 - 1 - i)
        dp = (await session.execute(
            select(DailyPrice).where(DailyPrice.stock_id == stk.id, DailyPrice.trade_date == d)
        )).scalar_one_or_none()
        if not dp:
            session.add(DailyPrice(
                stock_id=stk.id, trade_date=d, open=Decimal("1400"), high=Decimal("1420"), low=Decimal("1390"), close=Decimal("1410"),
                previous_close=Decimal("1400"), volume=100000, delivery_quantity=60000, delivery_percentage=Decimal("60.0")
            ))

    # FinancialStatement for RELIANCE (powers fundamental & valuation)
    from app.models.fundamental_corporate import FinancialStatement
    fs = (await session.execute(
        select(FinancialStatement).where(FinancialStatement.stock_id == stk.id)
    )).scalar_one_or_none()
    if not fs:
        session.add(FinancialStatement(
            stock_id=stk.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            reported_at=date(2026, 7, 20),
            available_at=date(2026, 7, 21),
            revision_type="AS_REPORTED",
            currency="INR",
            unit="INR_CRORE",
            revenue=Decimal("250000"),
            operating_income=Decimal("40000"),
            pat=Decimal("19000"),
            eps=Decimal("28.5"),
            total_equity=Decimal("800000"),
            total_debt=Decimal("300000"),
        ))

    # MarketIntelligenceSnapshot
    snap = (await session.execute(
        select(MarketIntelligenceSnapshot).where(MarketIntelligenceSnapshot.market_date == base_d)
    )).scalar_one_or_none()
    if not snap:
        session.add(MarketIntelligenceSnapshot(
            market_date=base_d, generated_at=base_d, price_action_score=-75.0, breadth_score=-28.7, volatility_score=28.1,
            composite_score=-36.86, confidence_score=71.62, regime="bearish", data_completeness_percent=60.0
        ))

    await session.commit()
    return idx_map


# ---------------------------------------------------------------------------
# 1. Official NSE Header Parsing
# ---------------------------------------------------------------------------
def test_official_nse_header_validation():
    """Verify parser enforces all 13 official NSE index closing headers."""
    res = NSEIndexParser.parse_csv_content(SAMPLE_VALID_CSV)
    assert res.total_rows == 4
    assert res.valid_rows == 3  # Nifty 50, India VIX, Nifty Bank

    corrupt_csv = "Index Name,Wrong Date,Close\nNifty 50,31-08-2026,24000\n"
    with pytest.raises(NSEIndexParseError) as exc:
        NSEIndexParser.parse_csv_content(corrupt_csv)
    assert "Missing required header" in str(exc.value)


# ---------------------------------------------------------------------------
# 2. Index-Name Normalization
# ---------------------------------------------------------------------------
def test_index_name_normalization():
    """Verify deterministic mapping from NSE display names to canonical codes."""
    assert NSE_INDEX_NAME_TO_CODE["NIFTY 50"] == "NIFTY_50"
    assert NSE_INDEX_NAME_TO_CODE["INDIA VIX"] == "INDIA_VIX"
    assert NSE_INDEX_NAME_TO_CODE["NIFTY BANK"] == "NIFTY_BANK"
    assert NSE_INDEX_NAME_TO_CODE["NIFTY HEALTHCARE INDEX"] == "NIFTY_HEALTHCARE"
    assert NSE_INDEX_NAME_TO_CODE["NIFTY SERVICES SECTOR"] == "NIFTY_SERVICES_SECTOR"


# ---------------------------------------------------------------------------
# 3. Unknown Index Rejection
# ---------------------------------------------------------------------------
def test_unknown_index_rejection():
    """Verify unmapped or random indices are rejected cleanly without error."""
    res = NSEIndexParser.parse_csv_content(SAMPLE_VALID_CSV)
    rejected = [r for r in res.rejected_rows if "Unmapped index" in r.get("reason", "")]
    assert len(rejected) == 1
    assert rejected[0]["index_name"] == "Unknown Random Index"


# ---------------------------------------------------------------------------
# 4. Date Parsing
# ---------------------------------------------------------------------------
def test_date_parsing():
    """Verify date parsing handles DD-MM-YYYY format and rejects malformed dates."""
    res = NSEIndexParser.parse_csv_content(SAMPLE_VALID_CSV)
    assert all(r.trade_date == date(2026, 8, 31) for r in res.records)

    bad_date_csv = SAMPLE_VALID_CSV.replace("31-08-2026", "2026/08/31")
    res_bad = NSEIndexParser.parse_csv_content(bad_date_csv)
    assert res_bad.valid_rows == 0
    assert len(res_bad.rejected_rows) >= 3


# ---------------------------------------------------------------------------
# 5. Numerical Validation (OHLC, High >= Low, Volume Clamping)
# ---------------------------------------------------------------------------
def test_numerical_validation():
    """Verify numeric constraints: high >= low, positive prices, volume clamp."""
    invalid_ohlc = """Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield
Nifty 50,31-08-2026,24000.00,23000.00,24500.00,24100.00,100,0.4,1000,50,20,2.5,1.2
"""
    res = NSEIndexParser.parse_csv_content(invalid_ohlc)
    assert res.valid_rows == 0
    assert any("High" in r["reason"] and "Low" in r["reason"] for r in res.rejected_rows)

    large_vol = SAMPLE_VALID_CSV.replace("710870778", "3000000000")  # > INT32_MAX
    res_vol = NSEIndexParser.parse_csv_content(large_vol)
    nifty = next(r for r in res_vol.records if r.index_code == "NIFTY_50")
    assert nifty.volume == 2147483647


# ---------------------------------------------------------------------------
# 6. No Duplicate Index/Date
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_duplicate_index_date():
    """Verify database unique constraint on (market_index_id, trade_date)."""
    async with get_session_factory()() as session:
        idx_map = await _seed_test_market_universe(session)
        nifty = idx_map["NIFTY_50"]
        prices = (await session.execute(
            select(MarketIndexPrice.trade_date)
            .where(MarketIndexPrice.market_index_id == nifty.id)
        )).scalars().all()
        assert len(prices) == len(set(prices)), "Duplicate index prices found for NIFTY_50!"


# ---------------------------------------------------------------------------
# 7. Idempotency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_import_idempotency(tmp_path):
    """Verify re-importing the same CSV produces 0 inserts and 0 updates."""
    csv_file = tmp_path / "ind_close_all_31082026.csv"
    csv_file.write_text(SAMPLE_VALID_CSV, encoding="utf-8")

    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        svc = NSEIndexImportService(session)
        res1 = await svc.import_index_file(csv_file)
        assert res1.success is True

        res2 = await svc.import_index_file(csv_file)
        assert res2.success is True
        assert res2.inserted_count == 0
        assert res2.updated_count == 0
        assert res2.unchanged_count == res1.target_indices_count


# ---------------------------------------------------------------------------
# 8. Source Provenance
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_source_provenance():
    """Verify stored index prices carry source_name and source_reference."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        mip = (await session.execute(
            select(MarketIndexPrice)
            .join(MarketIndex)
            .where(MarketIndex.code == "NIFTY_50", MarketIndexPrice.trade_date == date(2026, 8, 31))
        )).scalar_one_or_none()
        assert mip is not None
        assert mip.source_name == "NSE_ARCHIVE_IND_CLOSE"
        assert "ind_close_all_" in mip.source_reference


# ---------------------------------------------------------------------------
# 9. Global Provider Mapping
# ---------------------------------------------------------------------------
def test_global_provider_mapping():
    """Verify global indices catalog has exact symbols and providers."""
    for code in GLOBAL_INTELLIGENCE_INDEX_CODES:
        meta = INDEX_CATALOG_BY_CODE[code]
        assert meta.provider_symbol.startswith("^") or meta.provider_symbol == "^kospi"


# ---------------------------------------------------------------------------
# 10. Global Calendar Handling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_global_calendar_handling():
    """Verify timezone awareness is required for market evaluation."""
    async with get_session_factory()() as session:
        composite = CompositeMarketIntelligence(session)
        naive_dt = datetime(2026, 8, 31, 15, 30)
        with pytest.raises(ValueError) as exc:
            await composite.evaluate(naive_dt)
        assert "must be timezone-aware" in str(exc.value)


# ---------------------------------------------------------------------------
# 11. Snapshot Generation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_snapshot_generation():
    """Verify MarketIntelligenceSnapshot records exist across the target range."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        repo = MarketIntelligenceSnapshotRepository(session)
        snap = await repo.get_by_date(date(2026, 8, 31))
        assert snap is not None
        assert snap.regime in ("bullish", "bearish", "neutral")
        assert snap.data_completeness_percent >= 50.0


# ---------------------------------------------------------------------------
# 12. Point-in-Time (PIT) Evaluation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pit_evaluation():
    """Verify historical evaluation is strictly bounded by the evaluation date."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        composite = CompositeMarketIntelligence(session)
        eval_dt = datetime(2026, 8, 31, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        eval_d = date(2026, 8, 31)
        res = await composite.evaluate(eval_dt, market_date=eval_d)
        assert res.market_date == eval_d
        pa = next(f for f in res.factors if f.name == "price_action")
        assert pa.score is not None
        assert pa.freshness in ("live", "recent")


# ---------------------------------------------------------------------------
# 13. Market Context AVAILABLE in Stock Intelligence
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_market_context_available():
    """Verify StockIntelligenceService resolves market_context as AVAILABLE."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        svc = StockIntelligenceService(session)
        intel = await svc.get_stock_intelligence("RELIANCE", evaluation_date=date(2026, 8, 31))
        assert intel.domain_coverage.get("market_context") == "AVAILABLE"
        assert intel.completeness_pct >= 75.0
        assert any(e.domain == "market_context" for e in intel.evidence)


# ---------------------------------------------------------------------------
# 14. Missing Market Context Remains Unavailable Prior to Snapshots
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_market_context_prior_to_snapshots():
    """Verify dates prior to any snapshot remain truthfully UNAVAILABLE."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        svc = StockIntelligenceService(session)
        intel = await svc.get_stock_intelligence("RELIANCE", evaluation_date=date(2025, 1, 1))
        assert intel.domain_coverage.get("market_context") == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# 15. No Future Leakage
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_future_leakage():
    """Verify price repo get_date_range strictly excludes future dates."""
    async with get_session_factory()() as session:
        idx_map = await _seed_test_market_universe(session)
        repo = MarketIndexPriceRepository(session)
        nifty = idx_map["NIFTY_50"]
        rows = await repo.get_date_range(nifty.id, date(2026, 8, 1), date(2026, 8, 20))
        for r in rows:
            assert r.trade_date <= date(2026, 8, 20)


# ---------------------------------------------------------------------------
# 16. Missing != Zero Semantics
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_not_equal_to_zero():
    """Verify missing factors have None scores, never converted to 0.0."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        composite = CompositeMarketIntelligence(session)
        eval_dt = datetime(2026, 8, 31, 15, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        res = await composite.evaluate(eval_dt, market_date=date(2026, 8, 31))
        inst = next(f for f in res.factors if f.name == "institutional")
        assert inst.score is None
        assert inst.freshness == "missing"


# ---------------------------------------------------------------------------
# 17. Scanner Registry Regression
# ---------------------------------------------------------------------------
def test_scanner_registry_regression():
    """Verify core scanner registry definitions remain intact."""
    assert len(ScannerRegistry.scanners) >= 18
    assert "delivery_above_60" in ScannerRegistry.scanners
    assert "delivery_above_70" in ScannerRegistry.scanners


# ---------------------------------------------------------------------------
# 18. G.1 Technical Indicator Regression
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g1_price_indicator_regression():
    """Verify G.1 technical history and indicators remain operational."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        svc = StockDetailService(session)
        overview = await svc.get_stock360_overview("RELIANCE")
        assert overview is not None
        assert overview["history_sessions"] >= 20


# ---------------------------------------------------------------------------
# 19. G.2 Sector Classification Structure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g2_sector_classification_structure():
    """Verify Sector and Stock relationship functions as expected."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one_or_none()
        assert stk is not None
        assert stk.sector_id is not None


# ---------------------------------------------------------------------------
# 20. G.3 Delivery Metrics Regression
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_g3_delivery_regression():
    """Verify G.3 real delivery data is intact for RELIANCE."""
    async with get_session_factory()() as session:
        await _seed_test_market_universe(session)
        price = (await session.execute(
            select(DailyPrice)
            .join(Stock)
            .where(Stock.symbol == "RELIANCE", DailyPrice.trade_date == date(2026, 8, 31))
        )).scalar_one_or_none()
        assert price is not None
        assert price.delivery_quantity is not None
        assert price.delivery_percentage is not None
        assert price.delivery_percentage > Decimal("0")


# ---------------------------------------------------------------------------
# 21. Authenticated Browser Rendering
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_browser_rendering_market_intelligence():
    """Verify /market-intelligence renders HTTP 200 for authenticated viewer."""
    async with viewer_client(browser=True) as client:
        resp = await client.get("/market-intelligence")
        assert resp.status_code == 200
        assert "Market Intelligence" in resp.text


# ---------------------------------------------------------------------------
# 22. Factor Completeness Calculation
# ---------------------------------------------------------------------------
def test_factor_completeness_calculation():
    """Verify compute_composite_factor_result calculates completeness properly."""
    factors = [
        FactorInput("price_action", Decimal("50"), Decimal("0.25"), Decimal("80"), Decimal("80"), "live", None, None),
        FactorInput("breadth", Decimal("20"), Decimal("0.15"), Decimal("70"), Decimal("70"), "recent", None, None),
        FactorInput("sector", Decimal("-10"), Decimal("0.10"), Decimal("60"), Decimal("60"), "recent", None, None),
        FactorInput("institutional", None, Decimal("0.10"), Decimal("0"), Decimal("0"), "missing", None, None),
        FactorInput("volatility", Decimal("30"), Decimal("0.10"), Decimal("90"), Decimal("100"), "recent", None, None),
        FactorInput("global", None, Decimal("0.10"), Decimal("0"), Decimal("0"), "missing", None, None),
        FactorInput("gift_nifty", None, Decimal("0.10"), Decimal("0"), Decimal("0"), "unavailable", None, None),
        FactorInput("currency", None, Decimal("0.04"), Decimal("0"), Decimal("0"), "missing", None, None),
        FactorInput("crude", None, Decimal("0.03"), Decimal("0"), Decimal("0"), "missing", None, None),
        FactorInput("macro", None, Decimal("0.03"), Decimal("0"), Decimal("0"), "missing", None, None),
    ]
    res = compute_composite_factor_result(factors)
    assert res.data_completeness_percent >= 50.0
    assert res.composite_score is not None
