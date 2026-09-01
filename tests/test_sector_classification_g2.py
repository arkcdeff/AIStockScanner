"""Comprehensive tests for Stage 2 — Phase G.2: Sector and Industry Classification.

Covers:
  1. Parser headers, types, and strict invariants.
  2. Official file parsing against ind_niftytotalmarket_list.csv.
  3. Dummy row rejection and isolation.
  4. Missing field handling and malformed CSV rejection.
  5. Primary key ISIN matching priority.
  6. Symbol fallback matching for corporate action rollovers.
  7. Guarantee of zero duplicate stock creation.
  8. Sector entity upsert and deduplication.
  9. Industry entity upsert and deduplication.
  10. Sector-Industry relational foreign key linkage.
  11. Legacy Stock.industry synchronization with Industry.name.
  12. Hierarchy consistency (Stock.sector_id == Industry.sector_id).
  13. Idempotent repeat import verification.
  14. Strict exclusion of SYNTHETIC_FIXTURE_SYMBOLS.
  15. Unmatched row and conflict reporting.
  16. Stock Intelligence sector domain status: AVAILABLE & SECTOR_MAPPING_ESTABLISHED.
  17. Relative valuation peer group resolution via normalized Industry.
  18. Scanner workspace sector filter dropdown options.
  19. Stock Explorer summary metric sector_mapped.
  20. Stock Detail service overview sector_name.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.data.sector_classification_parser import (
    SectorClassificationParseError,
    SectorClassificationParser,
)
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import FinancialStatement
from app.models.industry import Industry
from app.models.stock import Stock
from app.services.fundamental_corporate_service import FundamentalCorporateService
from app.services.scanner_workspace import ScannerWorkspaceService
from app.services.sector_classification_service import SectorClassificationService
from app.services.stock import StockService
from app.services.stock_detail import StockDetailService
from app.services.stock_intelligence_service import StockIntelligenceService


def test_parser_header_validation():
    """Parser rejects CSV missing expected official headers."""
    bad_csv = "Symbol,Name,WrongColumn\nTCS,Tata,IT"
    with pytest.raises(SectorClassificationParseError, match="Invalid CSV headers"):
        SectorClassificationParser.parse_csv_content(bad_csv)


def test_parser_empty_content_rejection():
    """Parser rejects empty or whitespace-only CSV."""
    with pytest.raises(SectorClassificationParseError, match="empty"):
        SectorClassificationParser.parse_csv_content("   \n\n  ")


def test_parser_dummy_row_isolation():
    """Parser identifies and isolates exchange test dummy records."""
    csv_text = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Real Corp Ltd.,Information Technology,REALCORP,EQ,INE123A01010\n"
        "Dummy Test Ltd.,Construction,DUMMYCO,EQ,DUM999W01014\n"
    )
    result = SectorClassificationParser.parse_csv_content(csv_text)
    assert result.total_rows == 2
    assert result.valid_rows == 1
    assert len(result.records) == 1
    assert result.records[0].symbol == "REALCORP"
    assert len(result.dummy_records) == 1
    assert result.dummy_records[0].symbol == "DUMMYCO"
    assert result.dummy_records[0].is_dummy is True


def test_parser_missing_fields_reporting():
    """Parser reports rows with missing required columns without crashing."""
    csv_text = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        ",Information Technology,NOCMPNY,EQ,INE123A01010\n"
        "No Ind Ltd.,,NOIND,EQ,INE123A01011\n"
        "No Sym Ltd.,Capital Goods,,EQ,INE123A01012\n"
        "No Isin Ltd.,Capital Goods,NOISIN,EQ,\n"
    )
    result = SectorClassificationParser.parse_csv_content(csv_text)
    assert result.valid_rows == 0
    assert len(result.errors) == 4


def test_parser_official_file():
    """Parser parses official NIFTY Total Market CSV with exact counts."""
    fpath = Path("data/reference/ind_niftytotalmarket_list.csv")
    assert fpath.is_file(), "Official reference file missing from data/reference/"
    result = SectorClassificationParser.parse_file(fpath)
    assert result.total_rows == 752
    assert result.valid_rows == 750
    assert len(result.dummy_records) == 2
    assert len(result.errors) == 0


async def _seed_test_classification_universe(session) -> dict[str, Stock]:
    """Helper to seed representative stocks for test environment."""
    stocks_to_seed = [
        Stock(symbol="TCS", company_name="Tata Consultancy Services Ltd.", exchange="NSE", series="EQ", isin="INE467B01029", is_active=True),
        Stock(symbol="INFY", company_name="Infosys Ltd.", exchange="NSE", series="EQ", isin="INE009A01021", is_active=True),
        Stock(symbol="RELIANCE", company_name="Reliance Industries Ltd.", exchange="NSE", series="EQ", isin="INE002A01018", industry="Oil & Gas", is_active=True),
        Stock(symbol="HDFCBANK", company_name="HDFC Bank Ltd.", exchange="NSE", series="EQ", isin="INE040A01034", is_active=True),
        Stock(symbol="ICICIBANK", company_name="ICICI Bank Ltd.", exchange="NSE", series="EQ", isin="INE090A01021", is_active=True),
        Stock(symbol="KIRLPNU", company_name="Kirloskar Pneumatic Company Ltd.", exchange="NSE", series="EQ", isin="INE811A01020", is_active=True),
        Stock(symbol="TDPOWERSYS", company_name="TD Power Systems Ltd.", exchange="NSE", series="EQ", isin="INE419M01027", is_active=True),
        Stock(symbol="UNMATCHED1", company_name="Unmatched Corp Ltd.", exchange="NSE", series="EQ", isin="INE999X01019", is_active=True),
        Stock(symbol="ANALYTX", company_name="Analytics Test", exchange="NSE", series="EQ", isin="INE000A01010", is_active=True),
        Stock(symbol="REPRO1", company_name="Repro Test", exchange="NSE", series="EQ", isin="INE000B01011", is_active=True),
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

    # Seed price for TCS and INFY if missing
    tcs = stock_map["TCS"]
    existing_price = (await session.execute(select(DailyPrice).where(DailyPrice.stock_id == tcs.id))).scalar_one_or_none()
    if not existing_price:
        session.add(DailyPrice(
            stock_id=tcs.id,
            trade_date=date(2026, 8, 31),
            open=Decimal("3200.00"),
            high=Decimal("3250.00"),
            low=Decimal("3180.00"),
            close=Decimal("3220.00"),
            previous_close=Decimal("3200.00"),
            volume=100000,
        ))

    infy = stock_map["INFY"]
    existing_price_infy = (await session.execute(select(DailyPrice).where(DailyPrice.stock_id == infy.id))).scalar_one_or_none()
    if not existing_price_infy:
        session.add(DailyPrice(
            stock_id=infy.id,
            trade_date=date(2026, 8, 31),
            open=Decimal("1500.00"),
            high=Decimal("1520.00"),
            low=Decimal("1490.00"),
            close=Decimal("1510.00"),
            previous_close=Decimal("1500.00"),
            volume=50000,
        ))

    # Seed financial statement for TCS and INFY for relative valuation
    existing_fin = (await session.execute(select(FinancialStatement).where(FinancialStatement.stock_id == tcs.id))).scalar_one_or_none()
    if not existing_fin:
        session.add(FinancialStatement(
            stock_id=tcs.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            reported_at=date(2026, 8, 5),
            available_at=date(2026, 8, 5),
            eps=Decimal("40.00"),
            revenue=Decimal("60000.00"),
            total_equity=Decimal("90000.00"),
            shares_outstanding=Decimal("3600000000"),
        ))

    existing_fin_infy = (await session.execute(select(FinancialStatement).where(FinancialStatement.stock_id == infy.id))).scalar_one_or_none()
    if not existing_fin_infy:
        session.add(FinancialStatement(
            stock_id=infy.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            reported_at=date(2026, 8, 5),
            available_at=date(2026, 8, 5),
            eps=Decimal("20.00"),
            revenue=Decimal("40000.00"),
            total_equity=Decimal("70000.00"),
            shares_outstanding=Decimal("4100000000"),
        ))

    await session.commit()
    return stock_map


@pytest.mark.asyncio
async def test_classification_service_execution_and_matching():
    """Import matches primarily by ISIN, using symbol as fallback for rollovers."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        res = await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)
        assert res.success is True
        assert res.total_stocks_matched >= 5

        # Check ISIN match
        tcs = (await session.execute(select(Stock).where(Stock.symbol == "TCS"))).scalar_one()
        assert tcs.sector_id is not None
        assert tcs.industry_id is not None
        assert tcs.industry == "Information Technology"

        # Check Symbol fallback matches (KIRLPNU, TDPOWERSYS)
        kirlpnu = (await session.execute(select(Stock).where(Stock.symbol == "KIRLPNU"))).scalar_one()
        assert kirlpnu.sector_id is not None
        assert kirlpnu.industry_id is not None
        assert kirlpnu.industry == "Capital Goods"

        # Check legacy industry string updated
        rel = (await session.execute(select(Stock).where(Stock.symbol == "RELIANCE"))).scalar_one()
        assert rel.industry == "Oil Gas & Consumable Fuels"


@pytest.mark.asyncio
async def test_no_duplicate_stock_creation():
    """Classification service never creates new Stock rows."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        cnt_before = (await session.execute(select(func.count(Stock.id)))).scalar()

        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        cnt_after = (await session.execute(select(func.count(Stock.id)))).scalar()
        assert cnt_before == cnt_after, f"Stock count changed from {cnt_before} to {cnt_after}"


@pytest.mark.asyncio
async def test_hierarchy_and_legacy_synchronization():
    """Stock.industry equals Industry.name and Stock.sector_id equals Industry.sector_id."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        stmt = (
            select(Stock.symbol, Stock.sector_id, Stock.industry_id, Stock.industry, Industry.sector_id, Industry.name)
            .join(Industry, Stock.industry_id == Industry.id)
            .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
        )
        rows = (await session.execute(stmt)).all()
        assert len(rows) >= 5

        for r in rows:
            sym, s_sec_id, s_ind_id, s_ind_name, ind_sec_id, ind_name = r
            assert s_sec_id == ind_sec_id, f"Hierarchy mismatch for {sym}: {s_sec_id} != {ind_sec_id}"
            assert s_ind_name == ind_name, f"Legacy string mismatch for {sym}: {s_ind_name} != {ind_name}"


@pytest.mark.asyncio
async def test_synthetic_fixture_stocks_exclusion():
    """Synthetic fixture symbols are never modified by classification import, preserving fixture state."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)

        # Capture pre-existing state of ANALYTX fixture
        analytx_before = (await session.execute(select(Stock).where(Stock.symbol == "ANALYTX"))).scalar_one()
        before_sec_id = analytx_before.sector_id
        before_ind_id = analytx_before.industry_id
        before_ind_str = analytx_before.industry

        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        # 1. Verify ANALYTX is recognized as synthetic fixture and not mutated by G.2
        assert "ANALYTX" in SYNTHETIC_FIXTURE_SYMBOLS, "ANALYTX must be registered in SYNTHETIC_FIXTURE_SYMBOLS"
        analytx_after = (await session.execute(select(Stock).where(Stock.symbol == "ANALYTX"))).scalar_one()
        assert analytx_after.sector_id == before_sec_id, "ANALYTX sector_id was mutated by G.2 import!"
        assert analytx_after.industry_id == before_ind_id, "ANALYTX industry_id was mutated by G.2 import!"
        assert analytx_after.industry == before_ind_str, "ANALYTX industry string was mutated by G.2 import!"

        # 2. Verify all other synthetic fixtures remain strictly unmapped
        stmt = select(Stock.symbol, Stock.sector_id, Stock.industry_id).where(
            Stock.symbol.in_(SYNTHETIC_FIXTURE_SYMBOLS), Stock.symbol != "ANALYTX"
        )
        fixtures = (await session.execute(stmt)).all()
        for f in fixtures:
            assert f[1] is None, f"Synthetic stock {f[0]} sector_id was unexpectedly set"
            assert f[2] is None, f"Synthetic stock {f[0]} industry_id was unexpectedly set"


@pytest.mark.asyncio
async def test_stock_intelligence_sector_evidence_available():
    """StockIntelligenceService recognizes mapped canonical sector as AVAILABLE with runtime provenance."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        intel_service = StockIntelligenceService(session)
        intel = await intel_service.get_stock_intelligence("TCS", evaluation_date=date(2026, 8, 31))

        assert intel.domain_coverage.get("sector") == "AVAILABLE"
        sec_items = [e for e in intel.evidence if e.domain == "sector"]
        assert len(sec_items) >= 1
        assert sec_items[0].factor == "SECTOR_MAPPING_ESTABLISHED"
        assert sec_items[0].status == "AVAILABLE"
        assert "Information Technology" in sec_items[0].description
        # Verify runtime provenance
        assert intel.provenance.get("sector_source") == "ind_niftytotalmarket_list.csv#69ee01618f0a6a9ca57f63522329d03f4c179cf8cf0da29b5b80804f008dc1ac"
        assert sec_items[0].source == "ind_niftytotalmarket_list.csv#69ee01618f0a6a9ca57f63522329d03f4c179cf8cf0da29b5b80804f008dc1ac"


@pytest.mark.asyncio
async def test_relative_valuation_peer_group_resolution():
    """FundamentalCorporateService resolves peers under INDUSTRY peer group."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        fc_service = FundamentalCorporateService(session)
        snap = await fc_service.get_company_fundamental_snapshot("TCS", evaluation_date=date(2026, 8, 31))

        assert snap.relative_valuation is not None
        assert snap.relative_valuation.peer_group_type == "INDUSTRY"
        assert snap.relative_valuation.peer_count > 0
        assert snap.relative_valuation.median_pe is not None


@pytest.mark.asyncio
async def test_scanner_workspace_sector_filter_options():
    """ScannerWorkspaceService filter options contain canonical sectors."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        ws_service = ScannerWorkspaceService(session)
        opts = await ws_service.get_filter_options()
        sectors = opts.get("sectors", [])

        assert len(sectors) >= 1
        assert "Information Technology" in sectors


@pytest.mark.asyncio
async def test_stock_explorer_summary_sector_mapped():
    """StockService list_explorer summary reports sector mapped count."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        stock_service = StockService(session)
        res = await stock_service.list_explorer(page=1, page_size=10)
        summary = res.get("summary", {})

        assert summary.get("sector_mapped") >= 5


@pytest.mark.asyncio
async def test_stock_detail_service_overview_sector_name():
    """StockDetailService get_overview returns canonical sector name."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=False)

        detail_service = StockDetailService(session)
        overview = await detail_service.get_overview("RELIANCE")

        assert overview is not None
        assert overview.get("sector_name") == "Oil Gas & Consumable Fuels"
        assert overview.get("stock").industry == "Oil Gas & Consumable Fuels"


@pytest.mark.asyncio
async def test_idempotent_import_service():
    """Running SectorClassificationService repeat import preserves 100% idempotency."""
    factory = get_session_factory()
    async with factory() as session:
        await _seed_test_classification_universe(session)
        service = SectorClassificationService(session)
        # First import
        await service.import_classification(
            "data/reference/ind_niftytotalmarket_list.csv", dry_run=False
        )

        # Second import: must create 0 new sectors and 0 new industries
        res2 = await service.import_classification(
            "data/reference/ind_niftytotalmarket_list.csv", dry_run=False
        )

        assert res2.success is True
        assert res2.sectors_created == 0
        assert res2.industries_created == 0
        assert res2.stocks_updated == 0


def test_parser_duplicate_source_rows_handling():
    """Parser accepts duplicate CSV rows without crashing."""
    csv_text = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Real Corp Ltd.,Information Technology,REALCORP,EQ,INE123A01010\n"
        "Real Corp Ltd.,Information Technology,REALCORP,EQ,INE123A01010\n"
    )
    result = SectorClassificationParser.parse_csv_content(csv_text)
    assert result.total_rows == 2
    assert result.valid_rows == 2


def test_parser_invalid_isin_flagged_as_dummy():
    """Parser flags malformed/dummy ISINs as dummy records."""
    csv_text = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Dummy Test,Construction,DUMMYCO,EQ,INVALIDISIN\n"
    )
    result = SectorClassificationParser.parse_csv_content(csv_text)
    assert result.valid_rows == 0
    assert len(result.dummy_records) == 1
    assert result.dummy_records[0].is_dummy is True


@pytest.mark.asyncio
async def test_dry_run_leaves_database_untouched():
    """Classification service with dry_run=True rolls back all changes."""
    factory = get_session_factory()
    async with factory() as session:
        # Create an unmapped stock
        stk = Stock(symbol="DRYRUN_TEST", company_name="Dry Run Test Ltd", exchange="NSE", series="EQ", isin="INE467B01029", is_active=True)
        session.add(stk)
        await session.commit()

        service = SectorClassificationService(session)
        res = await service.import_classification("data/reference/ind_niftytotalmarket_list.csv", dry_run=True)
        assert res.success is True
        assert res.dry_run is True

        # Check that stock was not modified in DB
        recheck = (await session.execute(select(Stock).where(Stock.symbol == "DRYRUN_TEST"))).scalar_one()
        assert recheck.sector_id is None
        assert recheck.industry_id is None

        # Clean up test stock
        await session.delete(recheck)
        await session.commit()


@pytest.mark.asyncio
async def test_unmatched_source_records_tracking(tmp_path):
    """Classification service deterministically records source rows that do not match product DB."""
    matched_isin = "INE888M01011"
    unmatched_isin_1 = "INE777U01012"
    unmatched_isin_2 = "INE666V01013"
    csv_content = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        f"Matched Co Ltd.,Information Technology,MATCHED1,EQ,{matched_isin}\n"
        f"Unmatched Alpha Ltd.,Healthcare,UNMATCH1,EQ,{unmatched_isin_1}\n"
        f"Unmatched Beta Ltd.,Metals & Mining,UNMATCH2,EQ,{unmatched_isin_2}\n"
    )
    test_csv_file = tmp_path / "unmatched_test.csv"
    test_csv_file.write_text(csv_content, encoding="utf-8")

    factory = get_session_factory()
    async with factory() as session:
        stk = Stock(
            symbol="MATCHED1",
            company_name="Matched Co Ltd.",
            exchange="NSE",
            series="EQ",
            isin=matched_isin,
            is_active=True,
        )
        session.add(stk)
        await session.commit()

        service = SectorClassificationService(session)
        res = await service.import_classification(test_csv_file, dry_run=True)
        assert res.success is True
        assert res.stocks_matched_isin == 1
        assert res.total_stocks_matched == 1
        assert len(res.unmatched_source_records) == 2
        unmatched_symbols = {r["symbol"] for r in res.unmatched_source_records}
        assert unmatched_symbols == {"UNMATCH1", "UNMATCH2"}
        unmatched_isins = {r["isin"] for r in res.unmatched_source_records}
        assert unmatched_isins == {unmatched_isin_1, unmatched_isin_2}

        await session.delete(stk)
        await session.commit()


@pytest.mark.asyncio
async def test_sme_series_support(tmp_path):
    """Legitimate SME series (SM) are mapped without artificial EQ-only restriction using isolated test ISIN."""
    unique_sme_isin = "INE999S01099"
    sme_csv_content = (
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        f"SME Sample Corp Ltd.,Capital Goods,SMETEST,SM,{unique_sme_isin}\n"
    )
    sme_csv_file = tmp_path / "sme_classification_test.csv"
    sme_csv_file.write_text(sme_csv_content, encoding="utf-8")

    factory = get_session_factory()
    async with factory() as session:
        sme_stk = Stock(
            symbol="SMETEST",
            company_name="SME Sample Corp Ltd.",
            exchange="NSE",
            series="SM",
            isin=unique_sme_isin,
            is_active=True,
        )
        session.add(sme_stk)
        await session.commit()

        service = SectorClassificationService(session)
        res = await service.import_classification(sme_csv_file, dry_run=False)
        assert res.success is True
        assert res.stocks_matched_isin == 1

        recheck = (await session.execute(select(Stock).where(Stock.symbol == "SMETEST"))).scalar_one()
        assert recheck.sector_id is not None
        assert recheck.industry == "Capital Goods"
        assert recheck.series == "SM"

        await session.delete(recheck)
        await session.commit()
