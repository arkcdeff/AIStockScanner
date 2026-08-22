"""Comprehensive Unit & Integration Test Suite for Fundamental + Corporate Intelligence — Sprint 19.1E."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.corporate_action import CorporateAction
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import (
    CorporateAnnouncement,
    FinancialStatement,
    ShareholdingPattern,
)
from app.models.stock import Stock
from app.repositories.fundamental_corporate import FundamentalCorporateRepository
from app.services.fundamental_corporate_service import FundamentalCorporateService


@pytest.mark.asyncio
async def test_fundamental_statement_model_and_idempotency():
    """Verify FinancialStatement constraints, MISSING != ZERO, and SHA-256 fingerprint."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="TATASTEEL", company_name="Tata Steel Ltd", is_active=True)
        session.add(st)
        await session.flush()

        stmt = FinancialStatement(
            stock_id=st.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            reported_at=date(2026, 8, 5),
            available_at=date(2026, 8, 5),
            revenue=Decimal("25000.5000"),
            pat=Decimal("2100.2500"),
            ebit=Decimal("3500.0000"),
            total_equity=Decimal("50000.0000"),
            total_debt=Decimal("15000.0000"),
            operating_cash_flow=None,  # MISSING != ZERO
        )
        session.add(stmt)
        await session.commit()

        assert stmt.id is not None
        assert stmt.unit == "INR_CRORE"
        assert stmt.currency == "INR"
        assert stmt.operating_cash_flow is None  # Preserved as None
        assert stmt.idempotency_hash is not None


@pytest.mark.asyncio
async def test_point_in_time_lookahead_protection():
    """Verify Q1 2026 results announced on 2026-08-05 are hidden when querying as of 2026-07-15."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="INFY", company_name="Infosys Ltd", is_active=True)
        session.add(st)
        await session.flush()

        # Statement available on 2026-08-05
        stmt_q1 = FinancialStatement(
            stock_id=st.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            reported_at=date(2026, 8, 5),
            available_at=date(2026, 8, 5),
            revenue=Decimal("38000.0000"),
            pat=Decimal("6000.0000"),
        )
        session.add(stmt_q1)
        await session.commit()

        repo = FundamentalCorporateRepository(session)

        # Query as of 2026-07-15 (before release) -> Should return Empty
        history_before = await repo.get_financial_history(
            stock_id=st.id, as_of_date=date(2026, 7, 15)
        )
        assert len(history_before) == 0

        # Query as of 2026-08-10 (after release) -> Should return statement
        history_after = await repo.get_financial_history(
            stock_id=st.id, as_of_date=date(2026, 8, 10)
        )
        assert len(history_after) == 1
        assert history_after[0].revenue == Decimal("38000.0000")


@pytest.mark.asyncio
async def test_financial_metrics_and_pit_valuation():
    """Verify YoY growth, margins, solvency, and PIT valuation calculation."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="RELIANCE", company_name="Reliance Industries Ltd", is_active=True)
        session.add(st)
        await session.flush()

        # Add PIT daily price
        p_row = DailyPrice(
            stock_id=st.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("2500.00"),
            open=Decimal("2480.00"),
            high=Decimal("2520.00"),
            low=Decimal("2470.00"),
            volume=1000000,
        )
        session.add(p_row)

        # Q1 2026
        stmt_curr = FinancialStatement(
            stock_id=st.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            available_at=date(2026, 8, 5),
            revenue=Decimal("220000.0000"),
            operating_income=Decimal("35000.0000"),
            pat=Decimal("18000.0000"),
            eps=Decimal("100.0000"),
            total_equity=Decimal("400000.0000"),
            total_debt=Decimal("200000.0000"),
            operating_cash_flow=Decimal("20000.0000"),
            shares_outstanding=Decimal("6700000000"),
        )
        # Q1 2025 (4 quarters ago for YoY)
        stmt_yoy = FinancialStatement(
            stock_id=st.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2025, 6, 30),
            available_at=date(2025, 8, 5),
            revenue=Decimal("200000.0000"),
            pat=Decimal("15000.0000"),
            eps=Decimal("80.0000"),
        )
        session.add_all([stmt_curr, stmt_yoy])
        await session.commit()

        service = FundamentalCorporateService(session)
        snapshot = await service.get_company_fundamental_snapshot(
            symbol="RELIANCE",
            evaluation_date=date(2026, 8, 10),
            as_of_date=date(2026, 8, 10),
        )

        assert snapshot.symbol == "RELIANCE"
        assert snapshot.growth is not None
        assert snapshot.growth.revenue_growth_qoq == Decimal("10.0")
        assert snapshot.growth.profit_growth_qoq == Decimal("20.0")
        assert snapshot.margins is not None
        assert snapshot.margins.operating_margin_pct is not None
        assert snapshot.valuation is not None
        assert snapshot.valuation.pe_ratio == Decimal("25.0")


@pytest.mark.asyncio
async def test_corporate_action_reuse_and_announcements():
    """Verify reuse of existing 19.1C CorporateAction and CorporateAnnouncement parsing."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="HDFCBANK", company_name="HDFC Bank Ltd", is_active=True)
        session.add(st)
        await session.flush()

        # Existing 19.1C CorporateAction
        ca = CorporateAction(
            stock_id=st.id,
            action_type="dividend",
            ex_date=date(2026, 7, 15),
            available_at=date(2026, 7, 1),
            notes="Interim Dividend ₹15.50 per share",
            cash_amount=Decimal("15.50"),
        )
        session.add(ca)
        await session.flush()

        # CorporateAnnouncement linked to CorporateAction
        ann = CorporateAnnouncement(
            stock_id=st.id,
            category="DIVIDEND",
            announcement_date=date(2026, 7, 1),
            available_at=date(2026, 7, 1),
            title="Board approves Interim Dividend",
            summary="Interim dividend of ₹15.50 per equity share approved.",
            corporate_action_id=ca.id,
        )
        session.add(ann)
        await session.commit()

        repo = FundamentalCorporateRepository(session)
        actions = await repo.get_recent_corporate_actions(st.id, as_of_date=date(2026, 8, 1))
        anns = await repo.get_corporate_announcements(st.id, as_of_date=date(2026, 8, 1))

        assert len(actions) == 1
        assert actions[0].action_type == "dividend"
        assert len(anns) == 1
        assert anns[0].corporate_action_id == ca.id


@pytest.mark.asyncio
async def test_shareholding_pattern_and_promoter_pledge():
    """Verify shareholding pattern persistence and promoter pledge tracking."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="ADANIENT", company_name="Adani Enterprises Ltd", is_active=True)
        session.add(st)
        await session.flush()

        sh = ShareholdingPattern(
            stock_id=st.id,
            period_end=date(2026, 6, 30),
            available_at=date(2026, 7, 15),
            promoter_holding_pct=Decimal("72.600"),
            fii_holding_pct=Decimal("14.200"),
            dii_holding_pct=Decimal("5.800"),
            public_holding_pct=Decimal("7.400"),
            promoter_pledged_pct=Decimal("4.500"),
            promoter_holding_change=Decimal("-0.200"),
        )
        session.add(sh)
        await session.commit()

        repo = FundamentalCorporateRepository(session)
        patterns = await repo.get_shareholding_history(st.id, as_of_date=date(2026, 7, 20))
        assert len(patterns) == 1
        assert patterns[0].promoter_holding_pct == Decimal("72.600")
        assert patterns[0].promoter_pledged_pct == Decimal("4.500")


@pytest.mark.asyncio
async def test_fundamental_rest_api_and_ui_partial():
    """Verify REST API endpoints and HTMX UI partial rendering for fundamentals."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="TCS", company_name="Tata Consultancy Services Ltd", is_active=True)
        session.add(st)
        await session.flush()

        stmt = FinancialStatement(
            stock_id=st.id,
            period_type="QUARTERLY",
            statement_basis="CONSOLIDATED",
            period_end=date(2026, 6, 30),
            available_at=date(2026, 7, 12),
            revenue=Decimal("62500.0000"),
            pat=Decimal("12000.0000"),
        )
        session.add(stmt)
        await session.commit()

    from tests.auth_helpers import auth_client, seed_builtins

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # 1. REST API Snapshot
        resp = await client.get(
            "/api/v1/market-data/fundamentals/TCS",
            params={"evaluation_date": "2026-08-10"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TCS"
        assert float(data["latest_quarterly"]["revenue"]) == 62500.0

        # 2. REST API History
        resp_h = await client.get("/api/v1/market-data/fundamentals/TCS/history")
        assert resp_h.status_code == 200
        assert len(resp_h.json()) == 1

        # 3. REST API Coverage
        resp_c = await client.get("/api/v1/market-data/fundamentals/coverage")
        assert resp_c.status_code == 200
        assert resp_c.json()["coverage_status"] in ("ROBUST", "PARTIAL")

        # 4. HTMX UI Partial
        resp_ui = await client.get(
            "/market-data/fundamentals/cards",
            params={"symbol": "TCS", "evaluation_date": "2026-08-10"},
        )
        assert resp_ui.status_code == 200
        assert "TCS" in resp_ui.text
        assert "Quarterly Revenue" in resp_ui.text
