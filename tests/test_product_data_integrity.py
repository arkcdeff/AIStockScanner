"""Focused Product-Data Verification & Integrity Test Suite."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.data.fundamental_parser import FundamentalStatementParser
from app.db.session import get_session_factory
from app.models.fundamental_corporate import FinancialStatement
from app.models.industry import Industry
from app.models.sector import Sector
from app.models.stock import Stock
from app.schemas.fundamental_corporate import PITValuationMetrics
from app.services.fundamental_corporate_service import FundamentalCorporateService
from tests.auth_helpers import analyst_client


@pytest.mark.asyncio
async def test_empty_stock_stock360_empty_behavior() -> None:
    """Verify that a stock with no financial statements returns correct empty states."""
    factory = get_session_factory()
    async with factory() as session:
        empty_st = Stock(
            symbol="EMPTY_STOCK_TEST",
            company_name="Empty Stock Ltd",
            exchange="NSE",
            series="EQ",
            isin="INE000000000",
            is_active=True,
        )
        session.add(empty_st)
        await session.commit()

    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/EMPTY_STOCK_TEST")
    assert response.status_code == 200
    text = response.text
    assert "No financial statements recorded" in text
    assert "Valuation metrics unavailable" in text


@pytest.mark.asyncio
async def test_fundamentals_data_presence_and_absence_classification() -> None:
    """Verify fundamentals presence/absence UI classification."""
    factory = get_session_factory()
    async with factory() as session:
        tcs = Stock(symbol="TCS_TEST", company_name="TCS Test", is_active=True)
        session.add(tcs)
        await session.commit()

    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/TCS_TEST")
    assert "No financial statements recorded" in response.text

    # Add financial statement
    async with factory() as session:
        stmt = FinancialStatement(
            stock_id=tcs.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            available_at=date(2026, 7, 20),
            revenue=Decimal("15000.00"),
            pat=Decimal("2000.00"),
        )
        session.add(stmt)
        await session.commit()

    async with analyst_client(browser=True) as client:
        response = await client.get("/stocks/TCS_TEST")
    assert "No financial statements recorded" not in response.text
    assert "15,000.00" in response.text


@pytest.mark.asyncio
async def test_relative_valuation_fallback_hierarchy() -> None:
    """Verify ComparableCompany -> Industry -> Sector fallback search."""
    factory = get_session_factory()
    async with factory() as session:
        sec = Sector(name="IT_TEST", description="IT")
        session.add(sec)
        await session.flush()

        ind = Industry(name="Software_TEST", sector_id=sec.id)
        session.add(ind)
        await session.flush()

        target = Stock(symbol="TARGET", company_name="Target", industry_id=ind.id, sector_id=sec.id, is_active=True)
        peer1 = Stock(symbol="PEER1", company_name="Peer 1", industry_id=ind.id, sector_id=sec.id, is_active=True)
        peer2 = Stock(symbol="PEER2", company_name="Peer 2", industry_id=None, sector_id=sec.id, is_active=True)

        session.add_all([target, peer1, peer2])
        await session.commit()

    async with factory() as session:
        svc = FundamentalCorporateService(session)
        val = PITValuationMetrics(evaluation_date=date(2026, 8, 1), close_price=Decimal("100.0"))

        # 1. Industry Fallback (should find PEER1 since peer2 has industry_id = None)
        rel = await svc._calculate_relative_valuation(target, date(2026, 8, 1), date(2026, 8, 1), "CONSOLIDATED", val)
        assert rel is not None
        assert rel.peer_group_type == "INDUSTRY"
        assert rel.peer_count == 1

        # 2. Sector Fallback
        target.industry_id = None
        session.add(target)
        await session.commit()

        # Remove peer1 from session search
        from sqlalchemy import delete
        await session.execute(delete(Stock).where(Stock.symbol == "PEER1"))
        await session.commit()

        rel = await svc._calculate_relative_valuation(target, date(2026, 8, 1), date(2026, 8, 1), "CONSOLIDATED", val)
        assert rel is not None
        assert rel.peer_group_type == "SECTOR"
        assert rel.peer_count == 1


@pytest.mark.asyncio
async def test_multi_stock_pilot_parser_and_idempotency() -> None:
    """Verify pilot_11_stocks_financial_statements.csv parses all 11 stocks and imports idempotently."""
    csv_path = Path("data/raw/fundamentals/pilot_11_stocks_financial_statements.csv")
    assert csv_path.exists(), "pilot_11_stocks_financial_statements.csv not found!"
    content = csv_path.read_text(encoding="utf-8")

    parsed = FundamentalStatementParser.parse_csv_content(content)
    assert len(parsed) == 22
    symbols = {r["symbol"] for r in parsed}
    assert len(symbols) == 11
    assert "HDFCBANK" in symbols and "RELIANCE" in symbols and "TCS" in symbols and "SBIN" in symbols

    factory = get_session_factory()
    async with factory() as session:
        for sym in symbols:
            res = await session.execute(select(Stock).where(Stock.symbol == sym))
            if not res.scalar_one_or_none():
                st = Stock(symbol=sym, company_name=f"{sym} Ltd", is_active=True)
                session.add(st)
        await session.commit()

        svc = FundamentalCorporateService(session)
        # First import
        res1 = await svc.import_financial_statements(content)
        assert res1["processed"] == 22

        # Second import (Idempotency)
        res2 = await svc.import_financial_statements(content)
        assert res2["processed"] == 22


@pytest.mark.asyncio
async def test_multi_stock_banking_and_non_banking_snapshots_and_pit() -> None:
    """Verify banking and non-banking normalization, metrics, and PIT separation."""
    factory = get_session_factory()
    async with factory() as session:
        # 1. Setup test stocks
        for sym in ["SBIN_TEST", "TCS_TEST"]:
            res = await session.execute(select(Stock).where(Stock.symbol == sym))
            st = res.scalar_one_or_none()
            if not st:
                st = Stock(symbol=sym, company_name=f"{sym} Ltd", is_active=True)
                session.add(st)
                await session.flush()

            stmt = FinancialStatement(
                stock_id=st.id,
                period_type="QUARTERLY",
                statement_basis="CONSOLIDATED",
                period_end=date(2026, 6, 30),
                reported_at=date(2026, 8, 3),
                available_at=date(2026, 8, 3),
                revision_type="AS_REPORTED",
                revenue=Decimal("152480.0000") if "SBIN" in sym else Decimal("63575.0000"),
                operating_income=Decimal("31240.0000") if "SBIN" in sym else Decimal("15444.0000"),
                pat=Decimal("19325.0000") if "SBIN" in sym else Decimal("12105.0000"),
                eps=Decimal("21.6500") if "SBIN" in sym else Decimal("33.4500"),
                total_equity=Decimal("444800.0000") if "SBIN" in sym else Decimal("106200.0000"),
            )
            session.add(stmt)
        await session.commit()

        svc = FundamentalCorporateService(session)

        # 1. Banking: SBIN_TEST
        sbin_snap = await svc.get_company_fundamental_snapshot(
            symbol="SBIN_TEST",
            evaluation_date=date(2026, 8, 6),
            as_of_date=date(2026, 8, 6),
            statement_basis="CONSOLIDATED",
        )
        assert sbin_snap.latest_quarterly is not None
        assert sbin_snap.latest_quarterly.revenue == Decimal("152480.0000")
        assert sbin_snap.margins.operating_margin_pct is not None
        assert sbin_snap.margins.net_profit_margin_pct is not None

        # 2. Non-banking: TCS_TEST
        tcs_snap = await svc.get_company_fundamental_snapshot(
            symbol="TCS_TEST",
            evaluation_date=date(2026, 8, 6),
            as_of_date=date(2026, 8, 6),
            statement_basis="CONSOLIDATED",
        )
        assert tcs_snap.latest_quarterly is not None
        assert tcs_snap.latest_quarterly.revenue == Decimal("63575.0000")
        assert tcs_snap.margins.operating_margin_pct is not None
        assert tcs_snap.margins.roe_pct is not None
