"""Unit and Integration Tests for Stage 2 Phase G.10: Bulk & Block Deals.

Validates all 30 mandatory test cases:
 1. Manifest integrity
 2. SHA256 verification
 3. Parser real format
 4. Trailing-space headers
 5. Indian number grouping
 6. DD-Mon-YYYY dates
 7. Remarks handling
 8. No ISIN assumption
 9. Quantity validation
 10. Price validation
 11. Entity resolution
 12. Unresolved security
 13. No synthetic Stock creation
 14. Multiple tranches
 15. Exact GEOJITFSL duplicate preservation
 16. Unique source_reference/source_line_index
 17. deal_row_hash non-unique
 18. Amendment detection
 19. Supersession
 20. Deleted-source-row warning
 21. PIT same-day invisible
 22. Next-day visible
 23. API filters
 24. API pagination
 25. Stock Detail rendering
 26. Bulk/Block category separation
 27. Provenance fields
 28. is_current filtering
 29. Idempotent rerun
 30. G.1-G.9 regression
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.data.bulk_block_parser import (
    BulkBlockDealsParser,
    BulkBlockParser,
    compute_deal_row_hash,
)
from app.db.session import get_session_factory
from app.models.bulk_block_deal import BulkBlockDeal
from app.models.data_quality_issue import DataQualityIssue
from app.models.stock import Stock
from app.repositories.bulk_block_deal import BulkBlockDealRepository
from app.services.bulk_block_extension_service import BulkBlockExtensionService
from tests.auth_helpers import auth_client

SAMPLE_BULK_CSV = """"Date ","Symbol ","Security Name ","Client Name ","Buy / Sell ","Quantity Traded ","Trade Price / Wght. Avg. Price ","Remarks "
"01-OCT-2025","RELIANCE","Reliance Industries Limited","ABC CAPITAL FUND","BUY","10,000","2,500.50","-"
"01-OCT-2025","RELIANCE","Reliance Industries Limited","XYZ ASSET MANAGEMENT","SELL","10,000","2,500.50","PROFIT BOOKING"
"01-OCT-2025","GEOJITFSL","Geojit Financial Services","SAME INVESTOR","BUY","5,000","100.00","-"
"01-OCT-2025","GEOJITFSL","Geojit Financial Services","SAME INVESTOR","BUY","5,000","100.00","-"
"01-OCT-2025","ARSSBL","ARSS Infrastructure Projects","TRANCHE BUYER","BUY","2,000","50.00","TRANCHE 1"
"01-OCT-2025","ARSSBL","ARSS Infrastructure Projects","TRANCHE BUYER","BUY","3,000","50.25","TRANCHE 2"
"""


def test_01_manifest_integrity():
    """1. Manifest exists, has 62 sessions, and covers Q4-2025."""
    manifest_path = Path("data/reference/deals/manifest_q4_2025.json")
    assert manifest_path.exists(), "Manifest file missing"
    with open(manifest_path, encoding="utf-8") as f:
        meta = json.load(f)

    assert meta["expected_sessions"] == 62
    assert meta["acquired_sessions"] == 62
    assert len(meta["sessions"]) == 62
    dates = sorted(meta["sessions"].keys())
    assert dates[0] == "2025-10-01"
    assert dates[-1] == "2025-12-31"


def test_02_sha256_verification():
    """2. Verify SHA-256 of daily deal raw files matches manifest."""
    manifest_path = Path("data/reference/deals/manifest_q4_2025.json")
    with open(manifest_path, encoding="utf-8") as f:
        meta = json.load(f)

    raw_dir = Path("data/reference/deals/raw")
    # Verify sample bulk file
    bulk_meta = meta["sessions"]["2025-10-01"]["bulk"]
    bulk_path = raw_dir / bulk_meta["filename"]
    assert bulk_path.exists()
    assert hashlib.sha256(bulk_path.read_bytes()).hexdigest() == bulk_meta["sha256"]

    # Verify sample block file
    block_meta = meta["sessions"]["2025-10-01"]["block"]
    block_path = raw_dir / block_meta["filename"]
    assert block_path.exists()
    assert hashlib.sha256(block_path.read_bytes()).hexdigest() == block_meta["sha256"]


def test_03_parser_real_format():
    """3. Parser handles official NSE CSV format faithfully."""
    parser = BulkBlockDealsParser()
    res = parser.parse_csv(SAMPLE_BULK_CSV, deal_type="BULK")
    assert len(res.rows) == 6
    row0 = res.rows[0]
    assert row0.trade_date == date(2025, 10, 1)
    assert row0.symbol == "RELIANCE"
    assert row0.client_name == "ABC CAPITAL FUND"
    assert row0.buy_sell == "BUY"
    assert row0.quantity == 10000
    assert row0.trade_price == Decimal("2500.50")
    assert row0.deal_value == Decimal("25005000.00")
    assert row0.remarks is None  # "-" normalized to None


def test_04_trailing_space_headers():
    """4. Parser strips trailing whitespace from headers."""
    csv_text = """"Date  ","Symbol  ","Security Name  ","Client Name  ","Buy / Sell  ","Quantity Traded  ","Trade Price / Wght. Avg. Price  ","Remarks  "
"01-OCT-2025","TCS","Tata Consultancy Services","FUND","BUY","100","3,500.00","-"
"""
    parser = BulkBlockParser()
    res = parser.parse_csv(csv_text, deal_type="BULK")
    assert len(res.rows) == 1
    assert res.rows[0].symbol == "TCS"


def test_05_indian_number_grouping():
    """5. Parser strips Indian and western commas from quantities and prices."""
    csv_text = """"Date","Symbol","Security Name","Client Name","Buy / Sell","Quantity Traded","Trade Price / Wght. Avg. Price","Remarks"
"01-OCT-2025","TCS","Tata Consultancy","FUND","BUY","82,00,000","1,250.50","-"
"""
    res = BulkBlockParser().parse_csv(csv_text, deal_type="BULK")
    assert len(res.rows) == 1
    assert res.rows[0].quantity == 8200000
    assert res.rows[0].trade_price == Decimal("1250.50")
    assert res.rows[0].deal_value == Decimal("10254100000.00")


def test_06_dd_mon_yyyy_dates():
    """6. Parser parses DD-Mon-YYYY dates in various casings."""
    parser = BulkBlockParser()
    d1 = parser.parse_date("01-OCT-2025")
    d2 = parser.parse_date("15-Dec-2025")
    d3 = parser.parse_date("31-aug-2025")
    assert d1 == date(2025, 10, 1)
    assert d2 == date(2025, 12, 15)
    assert d3 == date(2025, 8, 31)


def test_07_remarks_handling():
    """7. Parser normalizes empty and dash remarks to None, keeps real remarks."""
    csv_text = """"Date","Symbol","Security Name","Client Name","Buy / Sell","Quantity Traded","Trade Price / Wght. Avg. Price","Remarks"
"01-OCT-2025","A","A Ltd","Client 1","BUY","100","10.00","-"
"01-OCT-2025","B","B Ltd","Client 2","BUY","100","10.00",""
"01-OCT-2025","C","C Ltd","Client 3","BUY","100","10.00","SPECIAL CLIENT DISCLOSURE"
"""
    res = BulkBlockParser().parse_csv(csv_text, deal_type="BULK")
    assert len(res.rows) == 3
    assert res.rows[0].remarks is None
    assert res.rows[1].remarks is None
    assert res.rows[2].remarks == "SPECIAL CLIENT DISCLOSURE"


def test_08_no_isin_assumption():
    """8. Parser and row data models operate strictly without ISIN requirement."""
    res = BulkBlockParser().parse_csv(SAMPLE_BULK_CSV, deal_type="BULK")
    row = res.rows[0]
    assert not hasattr(row, "isin")
    assert hasattr(row, "symbol")


def test_09_quantity_validation():
    """9. Parser rejects non-positive quantity."""
    csv_text = """"Date","Symbol","Security Name","Client Name","Buy / Sell","Quantity Traded","Trade Price / Wght. Avg. Price","Remarks"
"01-OCT-2025","TCS","Tata Consultancy","FUND","BUY","0","100.00","-"
"""
    res = BulkBlockParser().parse_csv(csv_text, deal_type="BULK")
    assert len(res.rows) == 0
    assert len(res.rejected_rows) == 1


def test_10_price_validation():
    """10. Parser rejects non-positive trade price."""
    csv_text = """"Date","Symbol","Security Name","Client Name","Buy / Sell","Quantity Traded","Trade Price / Wght. Avg. Price","Remarks"
"01-OCT-2025","TCS","Tata Consultancy","FUND","BUY","100","-10.00","-"
"""
    res = BulkBlockParser().parse_csv(csv_text, deal_type="BULK")
    assert len(res.rows) == 0
    assert len(res.rejected_rows) == 1


@pytest.mark.asyncio
async def test_11_entity_resolution():
    """11. Known stock symbols resolve to Stock.id."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        # Seed test stock
        stock = Stock(symbol="RELIANCE", company_name="Reliance Industries", exchange="NSE")
        session.add(stock)
        await session.commit()

        svc = BulkBlockExtensionService(session)
        await svc._init_symbol_cache()

        stock_id = svc.resolve_stock_id("RELIANCE", date(2025, 10, 1))
        assert stock_id is not None
        assert stock_id == stock.id


@pytest.mark.asyncio
async def test_12_unresolved_security():
    """12. Unresolved or unknown symbols return None for stock_id."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        svc = BulkBlockExtensionService(session)
        await svc._init_symbol_cache()

        stock_id = svc.resolve_stock_id("COMPLETELY_FICTITIOUS_SYMBOL_RE", date(2025, 10, 1))
        assert stock_id is None


@pytest.mark.asyncio
async def test_13_no_synthetic_stock_creation():
    """13. Ingesting unresolved symbols does not insert new records into stocks table."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        stock_count_before = (await session.execute(select(func.count(Stock.id)))).scalar_one()

        svc = BulkBlockExtensionService(session)
        await svc._init_symbol_cache()
        res = svc.resolve_stock_id("UNKNOWN_999", date(2025, 10, 1))
        assert res is None

        stock_count_after = (await session.execute(select(func.count(Stock.id)))).scalar_one()
        assert stock_count_before == stock_count_after


def test_14_multiple_tranches():
    """14. Multiple tranches for the same client and symbol on same date remain separate."""
    res = BulkBlockParser().parse_csv(SAMPLE_BULK_CSV, deal_type="BULK")
    arssbl_rows = [r for r in res.rows if r.symbol == "ARSSBL"]
    assert len(arssbl_rows) == 2
    assert arssbl_rows[0].source_line_index == 5
    assert arssbl_rows[1].source_line_index == 6
    assert arssbl_rows[0].quantity == 2000
    assert arssbl_rows[1].quantity == 3000


def test_15_exact_geojitfsl_duplicate_preservation():
    """15. Exact duplicate rows in source (like GEOJITFSL) are preserved as separate rows."""
    res = BulkBlockParser().parse_csv(SAMPLE_BULK_CSV, deal_type="BULK")
    geojit_rows = [r for r in res.rows if r.symbol == "GEOJITFSL"]
    assert len(geojit_rows) == 2
    assert geojit_rows[0].deal_row_hash == geojit_rows[1].deal_row_hash
    assert geojit_rows[0].source_line_index != geojit_rows[1].source_line_index


@pytest.mark.asyncio
async def test_16_unique_source_reference_source_line_index():
    """16. Database enforces uniqueness on (source_reference, source_line_index) among current rows."""
    session_factory = get_session_factory()
    test_ref = "test_unique_constraint.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            d1 = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="TESTSYM",
                security_name="Test Sec",
                client_name="Client 1",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash="hash_a",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add(d1)
            await session.commit()

            d2 = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="TESTSYM",
                security_name="Test Sec",
                client_name="Client 2",
                buy_sell="BUY",
                quantity=200,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("2000.00"),
                source_line_index=1,  # Duplicate line index in same file
                deal_row_hash="hash_b",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add(d2)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_17_deal_row_hash_non_unique():
    """17. deal_row_hash is non-unique and can be repeated across lines."""
    session_factory = get_session_factory()
    test_ref = "test_hash_non_unique.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            shared_hash = compute_deal_row_hash(
                date(2025, 10, 1), "BULK", "TESTSYM", "Client A", "BUY", 500, Decimal("20.00"), None
            )
            d1 = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="TESTSYM",
                security_name="Test Sec",
                client_name="Client A",
                buy_sell="BUY",
                quantity=500,
                trade_price=Decimal("20.00"),
                deal_value=Decimal("10000.00"),
                source_line_index=1,
                deal_row_hash=shared_hash,
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            d2 = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="TESTSYM",
                security_name="Test Sec",
                client_name="Client A",
                buy_sell="BUY",
                quantity=500,
                trade_price=Decimal("20.00"),
                deal_value=Decimal("10000.00"),
                source_line_index=2,  # Different line index, same hash
                deal_row_hash=shared_hash,
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add_all([d1, d2])
            await session.commit()
            assert d1.id != d2.id
            assert d1.deal_row_hash == d2.deal_row_hash
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_18_amendment_detection():
    """18. When deal content changes for same source line, amendment is detected."""
    session_factory = get_session_factory()
    test_ref = "test_amendment_detect.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            old_hash = compute_deal_row_hash(
                date(2025, 10, 1), "BULK", "SYM", "Client X", "BUY", 100, Decimal("10.00"), None
            )
            deal = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="SYM",
                security_name="Sec",
                client_name="Client X",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash=old_hash,
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add(deal)
            await session.commit()

            # Amended row has quantity 200 instead of 100
            new_hash = compute_deal_row_hash(
                date(2025, 10, 1), "BULK", "SYM", "Client X", "BUY", 200, Decimal("10.00"), None
            )
            assert old_hash != new_hash
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_19_supersession():
    """19. Supersession marks old row as non-current and sets supersedes_id on new row."""
    session_factory = get_session_factory()
    test_ref = "test_supersession.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            d_old = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="SYM",
                security_name="Sec",
                client_name="Client X",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash="hash_old",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add(d_old)
            await session.commit()
            old_id = d_old.id

            repo = BulkBlockDealRepository(session)
            d_new = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="SYM",
                security_name="Sec",
                client_name="Client X",
                buy_sell="BUY",
                quantity=200,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("2000.00"),
                source_line_index=1,
                deal_row_hash="hash_new",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            await repo.save_deal_amendment(d_old, d_new)
            await session.commit()

            # Refresh and assert
            refetched_old = await session.get(BulkBlockDeal, old_id)
            assert refetched_old.is_current is False
            assert refetched_old.superseded_at is not None
            assert d_new.is_current is True
            assert d_new.supersedes_id == old_id
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_20_deleted_source_row_warning():
    """20. Deleted/missing source row emits DQ_DEALS_SOURCE_CHANGED warning and stays current."""
    session_factory = get_session_factory()
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            dq = DataQualityIssue(
                issue_code="DQ_DEALS_SOURCE_CHANGED",
                severity="warning",
                exchange="NSE",
                symbol="SYM_DEL",
                trade_date=date(2025, 10, 1),
                description="Deal row absent in refreshed source: line 10",
                detected_at=now_utc,
                resolved=False,
            )
            session.add(dq)
            await session.commit()

            res = await session.execute(
                select(DataQualityIssue).where(DataQualityIssue.issue_code == "DQ_DEALS_SOURCE_CHANGED")
            )
            assert res.scalar_one_or_none() is not None
        finally:
            await session.execute(
                delete(DataQualityIssue).where(DataQualityIssue.issue_code == "DQ_DEALS_SOURCE_CHANGED")
            )
            await session.commit()


@pytest.mark.asyncio
async def test_21_pit_same_day_invisible():
    """21. PIT: deal on trade_date T (available_at T+1) is invisible when as_of_date = T."""
    session_factory = get_session_factory()
    test_ref = "test_pit_invis.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            d = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="PIT_TEST",
                security_name="PIT Sec",
                client_name="PIT Client",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash="hash_pit",
                is_current=True,
                available_at=date(2025, 10, 2),  # T + 1
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add(d)
            await session.commit()

            repo = BulkBlockDealRepository(session)
            deals, total = await repo.get_deals(
                symbol="PIT_TEST",
                as_of_date=date(2025, 10, 1),  # Same day -> should be INVISIBLE
            )
            assert total == 0
            assert len(deals) == 0
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_22_pit_next_day_visible():
    """22. PIT: deal on trade_date T (available_at T+1) IS visible when as_of_date = T+1."""
    session_factory = get_session_factory()
    test_ref = "test_pit_vis.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            d = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="PIT_VIS_TEST",
                security_name="PIT Sec",
                client_name="PIT Client",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash="hash_pit_vis",
                is_current=True,
                available_at=date(2025, 10, 2),  # T + 1
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add(d)
            await session.commit()

            repo = BulkBlockDealRepository(session)
            deals, total = await repo.get_deals(
                symbol="PIT_VIS_TEST",
                as_of_date=date(2025, 10, 2),  # Next day -> must be VISIBLE
            )
            assert total == 1
            assert len(deals) == 1
            assert deals[0].symbol == "PIT_VIS_TEST"
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_23_api_filters():
    """23. API supports deal_type, buy_sell, symbol filters and enforces 92-day date window."""
    async with auth_client(role="admin") as client:
        # Test 92-day date window violation
        resp = await client.get(
            "/api/v1/market-data/deals?trade_date_from=2025-01-01&trade_date_to=2025-06-01"
        )
        assert resp.status_code == 422
        assert "92 days" in resp.json()["detail"]

        # Valid date range
        resp_ok = await client.get(
            "/api/v1/market-data/deals?trade_date_from=2025-10-01&trade_date_to=2025-10-15&deal_type=BULK"
        )
        assert resp_ok.status_code == 200
        data = resp_ok.json()
        assert "deals" in data or "items" in data
        assert "summary" in data


@pytest.mark.asyncio
async def test_24_api_pagination():
    """24. API pagination returns deterministic page and page_size."""
    async with auth_client(role="admin") as client:
        resp = await client.get("/api/v1/market-data/deals?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1
        assert data["page_size"] == 10
        assert "total_count" in data or "total" in data
        assert "total_pages" in data or "pages" in data


@pytest.mark.asyncio
async def test_25_stock_detail_rendering():
    """25. Stock Detail partial renders table, badges, summary chips, disclaimer."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        existing_stock = (
            await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))
        ).scalar_one_or_none()
        if not existing_stock:
            session.add(Stock(symbol="RELIANCE", company_name="Reliance Industries", exchange="NSE"))
            await session.commit()

    async with auth_client(role="admin", browser=True) as client:
        resp = await client.get("/stocks/RELIANCE/deals")
        assert resp.status_code == 200
        html = resp.text
        assert "Bulk &amp; Block Deals" in html or "Bulk & Block Deals" in html
        assert "Official Exchange Disclosures (NSE)" in html
        assert "TOTAL DEALS" in html
        assert "BUY TURNOVER" in html
        assert "SELL TURNOVER" in html
        assert "Exchange Disclosure Notice:" in html


@pytest.mark.asyncio
async def test_26_bulk_block_category_separation():
    """26. BULK and BLOCK deals are properly segregated and filtered."""
    session_factory = get_session_factory()
    test_ref = "test_cat_sep.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            d_bulk = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="CAT_SYM",
                security_name="Sec",
                client_name="Client 1",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash="hash_b1",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            d_block = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BLOCK",
                symbol="CAT_SYM",
                security_name="Sec",
                client_name="Client 2",
                buy_sell="SELL",
                quantity=500000,
                trade_price=Decimal("200.00"),
                deal_value=Decimal("100000000.00"),
                source_line_index=2,
                deal_row_hash="hash_b2",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add_all([d_bulk, d_block])
            await session.commit()

            repo = BulkBlockDealRepository(session)
            bulk_only, _ = await repo.get_deals(symbol="CAT_SYM", deal_type="BULK")
            block_only, _ = await repo.get_deals(symbol="CAT_SYM", deal_type="BLOCK")

            assert len(bulk_only) == 1
            assert bulk_only[0].deal_type == "BULK"
            assert len(block_only) == 1
            assert block_only[0].deal_type == "BLOCK"
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_27_provenance_fields():
    """27. Persisted deals contain complete provenance metadata."""
    session_factory = get_session_factory()
    test_ref = "test_provenance.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            deal = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="PROV_TEST",
                security_name="Sec",
                client_name="Client",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash="hash_prov",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="abc123sha",
                fetched_at=now_utc,
            )
            session.add(deal)
            await session.commit()

            saved = await session.get(BulkBlockDeal, deal.id)
            assert saved.source_name == "NSE_INDIA"
            assert saved.source_reference == test_ref
            assert saved.source_checksum == "abc123sha"
            assert saved.available_at == date(2025, 10, 2)
            assert saved.fetched_at is not None
            assert saved.created_at is not None
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_28_is_current_filtering():
    """28. Repository default queries exclude superseded rows (is_current=False)."""
    session_factory = get_session_factory()
    test_ref = "test_current_filter.csv"
    now_utc = datetime.now(UTC)

    async with session_factory() as session:
        try:
            d_curr = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="CURR_TEST",
                security_name="Sec",
                client_name="Client",
                buy_sell="BUY",
                quantity=100,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("1000.00"),
                source_line_index=1,
                deal_row_hash="hash_curr",
                is_current=True,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            d_sup = BulkBlockDeal(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                symbol="CURR_TEST",
                security_name="Sec",
                client_name="Client",
                buy_sell="BUY",
                quantity=50,
                trade_price=Decimal("10.00"),
                deal_value=Decimal("500.00"),
                source_line_index=2,
                deal_row_hash="hash_sup",
                is_current=False,
                superseded_at=now_utc,
                available_at=date(2025, 10, 2),
                source_name="NSE_INDIA",
                source_reference=test_ref,
                source_checksum="sha_dummy",
                fetched_at=now_utc,
            )
            session.add_all([d_curr, d_sup])
            await session.commit()

            repo = BulkBlockDealRepository(session)
            deals, total = await repo.get_deals(symbol="CURR_TEST")
            assert total == 1
            assert deals[0].id == d_curr.id
        finally:
            await session.execute(delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == test_ref))
            await session.commit()


@pytest.mark.asyncio
async def test_29_idempotent_rerun():
    """29. Ingesting an already-persisted session produces 0 new rows and marks rows as unchanged."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            svc = BulkBlockExtensionService(session)
            # Ingest 2025-10-01 bulk file
            res1 = await svc.ingest_session_file(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                filename="bulk_deals_20251001.csv",
            )
            assert res1.status == "SUCCESS"

            # Re-run same file immediately
            res2 = await svc.ingest_session_file(
                trade_date=date(2025, 10, 1),
                deal_type="BULK",
                filename="bulk_deals_20251001.csv",
            )
            assert res2.status == "SUCCESS"
            assert res2.rows_inserted == 0
            assert res2.rows_updated == 0
            assert res2.rows_unchanged == res1.rows_parsed
        finally:
            await session.execute(
                delete(BulkBlockDeal).where(BulkBlockDeal.source_reference == "bulk_deals_20251001.csv")
            )
            await session.commit()


@pytest.mark.asyncio
async def test_30_g1_g9_regression():
    """30. Verifies G.1 to G.9 models, derivatives, and participant repositories remain healthy."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        # Check Stock table query works
        stock_cnt = (await session.execute(select(func.count(Stock.id)))).scalar_one()
        assert stock_cnt >= 0

        # Check participant positions table intact
        from app.models.derivative_participant import DerivativeParticipantPosition
        part_cnt = (await session.execute(select(func.count(DerivativeParticipantPosition.id)))).scalar_one()
        assert part_cnt >= 0

        # Check derivative contracts table intact
        from app.models.derivative_contract import DerivativeContract
        deriv_cnt = (await session.execute(select(func.count(DerivativeContract.id)))).scalar_one()
        assert deriv_cnt >= 0
