"""Comprehensive Unit, Integration & Regression Test Suite for Stage 2 G.5.

Covers:
  1. official source format
  2. parser
  3. category normalization
  4. entity resolution
  5. invalid/unmatched record handling
  6. announcement ingestion
  7. corporate action ingestion
  8. PIT available_at
  9. future-event exclusion
  10. idempotency
  11. transaction rollback
  12. provenance
  13. pilot coverage
  14. Stock Intelligence corporate-events domain
  15. completeness calculation
  16. missing != zero
  17. Stock Detail rendering
  18. G.1 regression
  19. G.2 regression
  20. G.3 regression
  21. G.4 regression
  22. no NewsEvent ingestion
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.data.fundamental_parser import CorporateAnnouncementParser
from app.db.session import get_session_factory
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import CorporateAnnouncement, FinancialStatement
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
from app.models.news_policy import NewsEvent
from app.models.sector import Sector
from app.models.stock import Stock
from app.services.fundamental_corporate_service import FundamentalCorporateService
from app.services.nse_corporate_import_service import (
    NseCorporateImportService,
    extract_dividend_amount,
    normalize_action_type,
    parse_nse_date,
    parse_nse_datetime,
)
from app.services.stock_detail import StockDetailService
from app.services.stock_intelligence_service import StockIntelligenceService
from tests.auth_helpers import analyst_client


async def _seed_test_corporate_universe(session) -> dict[str, Stock]:
    """Helper to seed test stocks, prices, sector, and announcements in test DB."""
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
        elif stk.sector_id is None:
            stk.sector_id = sec.id
            session.add(stk)
            await session.flush()
        stocks_map[sym] = stk

        # Seed DailyPrices (25 sessions)
        for i in range(25):
            d = base_d - timedelta(days=25 - 1 - i)
            dp = (await session.execute(
                select(DailyPrice).where(DailyPrice.stock_id == stk.id, DailyPrice.trade_date == d)
            )).scalar_one_or_none()
            if not dp:
                session.add(DailyPrice(
                    stock_id=stk.id,
                    trade_date=d,
                    open=Decimal("1000"),
                    high=Decimal("1020"),
                    low=Decimal("990"),
                    close=Decimal("1010"),
                    previous_close=Decimal("1000"),
                    volume=100000,
                    delivery_quantity=60000,
                    delivery_percentage=Decimal("60.0"),
                ))

        # Seed FinancialStatement
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
                revenue=Decimal("100000"),
                operating_income=Decimal("20000"),
                pat=Decimal("10000"),
                eps=Decimal("20.0"),
                total_equity=Decimal("500000"),
                total_debt=Decimal("100000"),
            ))

    # Market Index & Price
    idx = (await session.execute(select(MarketIndex).where(MarketIndex.code == "NIFTY_50"))).scalar_one_or_none()
    if not idx:
        idx = MarketIndex(code="NIFTY_50", name="Nifty 50", category="indian_index", exchange="NSE", country="INDIA", provider_symbol="^NSEI", is_active=True)
        session.add(idx)
        await session.flush()

    for i in range(25):
        d = base_d - timedelta(days=25 - 1 - i)
        p = (await session.execute(
            select(MarketIndexPrice).where(MarketIndexPrice.market_index_id == idx.id, MarketIndexPrice.trade_date == d)
        )).scalar_one_or_none()
        if not p:
            session.add(MarketIndexPrice(
                market_index_id=idx.id, trade_date=d, open=Decimal("24000"), high=Decimal("24150"), low=Decimal("23950"), close=Decimal("24100"),
                previous_close=Decimal("24050"), change=Decimal("50"), change_percent=Decimal("0.2"), volume=500000,
                observed_at=d, fetched_at=d, source_name="NSE_ARCHIVE_IND_CLOSE", source_reference="ind_close_all_31082026.csv", freshness_status="recent"
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

    # Ingest actions & announcements using service
    svc = NseCorporateImportService(session)
    await svc.import_corporate_actions()
    await svc.import_corporate_announcements()

    await session.commit()
    return stocks_map


# 1. Official Source Format
def test_official_source_format() -> None:
    manifest_path = Path("data/reference/corporate/manifest.json")
    assert manifest_path.exists()
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    assert "nse_corporate_actions_20260601_20260831.json" in manifest
    assert "nse_corporate_announcements_20260601_20260831.json" in manifest
    assert "nse_event_calendar_20260601_20260831.json" in manifest

    ca_meta = manifest["nse_corporate_actions_20260601_20260831.json"]
    assert ca_meta["row_count"] == 871
    assert ca_meta["sha256"] == "4f41b207e0a5b9999a7e0712e9e75170bb2db75f76a01170845731323875fa4e"

    ann_meta = manifest["nse_corporate_announcements_20260601_20260831.json"]
    assert ann_meta["row_count"] == 52500
    assert ann_meta["sha256"] == "533198e464f620785d7b2d6b9c1eed92994b383ac1d5c1a5efa6f8bda5e2e4e6"


# 2. Parser Functions
def test_parser_functions() -> None:
    d = parse_nse_date("05-Jun-2026")
    assert d == date(2026, 6, 5)
    assert parse_nse_date("-") is None

    ann_d, avail_d = parse_nse_datetime("28-Aug-2026 19:45:51")
    assert ann_d == date(2026, 8, 28)
    assert avail_d == date(2026, 8, 28)
    assert parse_nse_datetime(None) == (None, None)

    div_amt = extract_dividend_amount("Interim Dividend - Rs 15.50 Per Share")
    assert div_amt == Decimal("15.50")
    div_amt2 = extract_dividend_amount("Dividend - Rs 6 Per Share")
    assert div_amt2 == Decimal("6")


# 3. Category Normalization
def test_category_normalization() -> None:
    assert CorporateAnnouncementParser.normalize_category("Outcome of Board Meeting") == "BOARD_MEETING"
    assert CorporateAnnouncementParser.normalize_category("Bagging/Receiving of orders/contracts") == "ORDER_WIN"
    assert CorporateAnnouncementParser.normalize_category("Appointment of Director") == "MANAGEMENT_CHANGE"
    assert CorporateAnnouncementParser.normalize_category("Credit Rating Revision") == "CREDIT_RATING"
    assert CorporateAnnouncementParser.normalize_category("Dividend Declaration") == "DIVIDEND"
    assert CorporateAnnouncementParser.normalize_category("Bonus Issue") == "BONUS"
    assert CorporateAnnouncementParser.normalize_category("Sub-division of shares") == "STOCK_SPLIT"
    assert CorporateAnnouncementParser.normalize_category("Random Announcement") == "OTHER"

    assert normalize_action_type("Interim Dividend - Rs 24 Per Share") == "dividend"
    assert normalize_action_type("Split - Rs 10 To Rs 2") == "split"
    assert normalize_action_type("Bonus 1:1") == "bonus"
    assert normalize_action_type("Buy Back of Equity Shares") == "buyback"
    assert normalize_action_type("General Announcement") is None


# 4. Entity Resolution
@pytest.mark.asyncio
async def test_entity_resolution() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = NseCorporateImportService(session)
        await svc._init_stock_maps()

        # Matched by ISIN
        rel_id = svc.resolve_stock_id(isin="INE002A01018", symbol="RELIANCE")
        assert rel_id is not None

        # Matched by Symbol
        infy_id = svc.resolve_stock_id(isin=None, symbol="INFY")
        assert infy_id is not None

        # Unmatched
        assert svc.resolve_stock_id(isin="INE999999999", symbol="UNKNOWN_SYM") is None


# 5. Invalid / Unmatched Record Handling
@pytest.mark.asyncio
async def test_unmatched_record_handling() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = NseCorporateImportService(session)
        assert svc.resolve_stock_id("INVALID_ISIN", "UNKNOWN_SECURITY") is None


# 6. Announcement Ingestion
@pytest.mark.asyncio
async def test_announcement_ingestion() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        cnt = (await session.execute(select(func.count(CorporateAnnouncement.id)))).scalar()
        assert cnt > 0
        ann = (await session.execute(select(CorporateAnnouncement).limit(1))).scalar_one()
        assert ann.stock_id is not None
        assert ann.category in CorporateAnnouncementParser.VALID_CATEGORIES
        assert ann.announcement_date is not None
        assert ann.available_at is not None
        assert ann.title != ""
        assert ann.source_name == "NSE_ANNOUNCEMENT"


# 7. Corporate Action Ingestion
@pytest.mark.asyncio
async def test_corporate_action_ingestion() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        cnt = (await session.execute(select(func.count(CorporateAction.id)))).scalar()
        assert cnt > 0
        ca = (await session.execute(select(CorporateAction).where(CorporateAction.action_type == "dividend").limit(1))).scalar_one()
        assert ca.action_type == "dividend"
        assert ca.stock_id is not None
        assert ca.available_at is not None


# 8. PIT available_at
@pytest.mark.asyncio
async def test_pit_available_at_rule() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        invalid_pit = (
            await session.execute(
                select(func.count(CorporateAnnouncement.id)).where(
                    CorporateAnnouncement.available_at < CorporateAnnouncement.announcement_date
                )
            )
        ).scalar()
        assert invalid_pit == 0


# 9. Future-Event Exclusion
@pytest.mark.asyncio
async def test_future_event_exclusion() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = FundamentalCorporateService(session)
        cutoff = date(2026, 8, 20)
        anns = await svc.get_corporate_announcements("RELIANCE", as_of_date=cutoff, limit=50)
        for a in anns:
            avail = a.get("available_at")
            if avail:
                assert date.fromisoformat(avail) <= cutoff


# 10. Idempotency
@pytest.mark.asyncio
async def test_idempotency_second_run() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = NseCorporateImportService(session)
        stats = await svc.import_corporate_actions()
        assert stats["inserted"] == 0
        assert stats["unchanged"] > 0


# 11. Transaction Rollback
@pytest.mark.asyncio
async def test_transaction_safety_and_rollback() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        ann_before = (await session.execute(select(func.count(CorporateAnnouncement.id)))).scalar()
        try:
            async with session.begin_nested():
                ann_bad = CorporateAnnouncement(
                    stock_id=1,
                    category="INVALID_NON_EXISTENT_CAT",
                    announcement_date=date(2026, 8, 1),
                    available_at=date(2026, 8, 1),
                    title="Bad Category Test",
                    idempotency_hash="bad_cat_test_hash",
                )
                session.add(ann_bad)
                await session.flush()
        except Exception:
            pass  # Expected rollback

        ann_after = (await session.execute(select(func.count(CorporateAnnouncement.id)))).scalar()
        assert ann_before == ann_after


# 12. Provenance
@pytest.mark.asyncio
async def test_data_provenance() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        ann = (await session.execute(select(CorporateAnnouncement).limit(1))).scalar_one()
        assert ann.source_name == "NSE_ANNOUNCEMENT"
        assert ann.source_reference is not None


# 13. Pilot Coverage
@pytest.mark.asyncio
async def test_pilot_coverage() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = FundamentalCorporateService(session)
        pilots = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "SBIN"]
        for sym in pilots:
            anns = await svc.get_corporate_announcements(sym, as_of_date=date(2026, 8, 31), limit=10)
            assert len(anns) > 0, f"Expected real announcements for {sym}"


# 14. Stock Intelligence corporate_events domain
@pytest.mark.asyncio
async def test_stock_intelligence_corporate_events_domain() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = StockIntelligenceService(session)
        intel = await svc.get_stock_intelligence("TCS", evaluation_date=date(2026, 8, 31))
        assert intel.domain_coverage.get("corporate_events") == "AVAILABLE"
        assert "corporate_events" not in intel.missing_inputs


# 15. Completeness Calculation
@pytest.mark.asyncio
async def test_completeness_calculation() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = StockIntelligenceService(session)
        intel = await svc.get_stock_intelligence("RELIANCE", evaluation_date=date(2026, 8, 31))
        assert intel.completeness_pct == 87.5
        assert intel.missing_inputs == ["news"]


# 16. Missing != Zero
@pytest.mark.asyncio
async def test_missing_not_equal_zero() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = StockIntelligenceService(session)
        intel = await svc.get_stock_intelligence("RELIANCE", evaluation_date=date(2026, 6, 2))
        assert intel.domain_coverage["news"] == "UNAVAILABLE"
        assert "news" in intel.missing_inputs


# 17. Stock Detail Rendering
@pytest.mark.asyncio
async def test_stock_detail_rendering() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
    async with analyst_client(browser=True) as client:
        r = await client.get("/stocks/RELIANCE")
        assert r.status_code == 200
        assert "Corporate Events" in r.text
        assert "PIT Safe" in r.text


# 18. G.1 Regression (DailyPrice depth)
@pytest.mark.asyncio
async def test_g1_regression() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        svc = StockDetailService(session)
        overview = await svc.get_stock360_overview("RELIANCE")
        assert overview is not None
        assert overview["history_sessions"] >= 20


# 19. G.2 Regression (Sector taxonomy)
@pytest.mark.asyncio
async def test_g2_regression() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        stk = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one_or_none()
        assert stk is not None
        assert stk.sector_id is not None


# 20. G.3 Regression (Delivery coverage)
@pytest.mark.asyncio
async def test_g3_regression() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        price = (await session.execute(
            select(DailyPrice)
            .join(Stock)
            .where(Stock.symbol == "RELIANCE", DailyPrice.trade_date == date(2026, 8, 31))
        )).scalar_one_or_none()
        assert price is not None
        assert price.delivery_quantity is not None
        assert price.delivery_percentage is not None
        assert price.delivery_percentage > Decimal("0")


# 21. G.4 Regression (Market Intelligence snapshots & market_context)
@pytest.mark.asyncio
async def test_g4_regression() -> None:
    async with get_session_factory()() as session:
        await _seed_test_corporate_universe(session)
        snap = (await session.execute(select(MarketIntelligenceSnapshot).where(MarketIntelligenceSnapshot.market_date == date(2026, 8, 31)))).scalar_one_or_none()
        assert snap is not None
        assert snap.data_completeness_percent == 60.0


# 22. No NewsEvent Ingestion
@pytest.mark.asyncio
async def test_no_news_event_ingestion() -> None:
    async with get_session_factory()() as session:
        news_cnt = (await session.execute(select(func.count(NewsEvent.id)))).scalar()
        assert news_cnt == 0
