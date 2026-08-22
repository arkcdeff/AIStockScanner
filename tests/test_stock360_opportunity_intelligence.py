"""Tests for Stock 360° + Opportunity Intelligence — Sprint 19.1G."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import FinancialStatement
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.news_policy import NewsEvent
from app.models.stock import Stock
from app.services.opportunity_engine import OpportunityEngine
from app.services.opportunity_ranking_service import OpportunityRankingService
from app.services.relative_strength_service import RelativeStrengthService
from app.services.stock360_service import Stock360Service
from tests.auth_helpers import auth_client, seed_builtins


@pytest.mark.asyncio
async def test_relative_strength_service() -> None:
    """Test point-in-time relative strength calculations vs benchmark index."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="TECHM", company_name="Tech Mahindra Ltd", is_active=True)
        session.add(st)
        await session.flush()

        eval_d = date(2026, 8, 10)
        for i in range(30):
            d = eval_d - timedelta(days=i)
            p = 1000.0 * (1.0 + (30 - i) * 0.005)
            dp = DailyPrice(
                stock_id=st.id,
                trade_date=d,
                open=Decimal(str(round(p * 0.99, 2))),
                high=Decimal(str(round(p * 1.01, 2))),
                low=Decimal(str(round(p * 0.98, 2))),
                close=Decimal(str(round(p, 2))),
                volume=10000,
            )
            session.add(dp)

        idx = MarketIndex(code="NIFTY_50", name="Nifty 50")
        session.add(idx)
        await session.flush()

        for i in range(30):
            d = eval_d - timedelta(days=i)
            p = 20000.0 * (1.0 + (30 - i) * 0.001)
            mip = MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=d,
                open=Decimal(str(round(p * 0.99, 2))),
                high=Decimal(str(round(p * 1.01, 2))),
                low=Decimal(str(round(p * 0.98, 2))),
                close=Decimal(str(round(p, 2))),
            )
            session.add(mip)

        await session.commit()

        service = RelativeStrengthService(session)
        rs_metrics = await service.calculate_relative_strength(st.id, eval_d)

        assert rs_metrics is not None
        assert rs_metrics.stock_vs_nifty_20d_pct is not None
        assert rs_metrics.rs_trend in ("OUTPERFORMING", "NEUTRAL")


@pytest.mark.asyncio
async def test_stock360_snapshot_orchestration() -> None:
    """Test unified Stock 360 snapshot orchestration across technicals, volume, news, and fundamentals."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="INFY", company_name="Infosys Limited", is_active=True)
        session.add(st)
        await session.flush()

        eval_d = date(2026, 8, 10)
        dp = DailyPrice(
            stock_id=st.id,
            trade_date=eval_d,
            open=Decimal("1500.0"),
            high=Decimal("1550.0"),
            low=Decimal("1490.0"),
            close=Decimal("1540.0"),
            volume=500000,
            delivery_percentage=Decimal("55.0"),
        )
        session.add(dp)

        fs = FinancialStatement(
            stock_id=st.id,
            period_type="QUARTERLY",
            period_end=date(2026, 6, 30),
            statement_basis="CONSOLIDATED",
            source_name="NSE_FILING",
            source_reference="REF1",
            reported_at=date(2026, 7, 15),
            available_at=date(2026, 7, 15),
            revenue=Decimal("3800.00"),
            pat=Decimal("600.00"),
            idempotency_hash="hash_fs_infy_1",
        )
        session.add(fs)
        await session.commit()

        service = Stock360Service(session)
        snap = await service.get_stock360_snapshot("INFY", evaluation_date=eval_d)

        assert snap.symbol == "INFY"
        assert snap.technicals is not None
        assert snap.technicals.close == 1540.0
        assert snap.volume_delivery is not None
        assert snap.volume_delivery.volume == 500000
        assert snap.data_completeness_pct > 0.0


@pytest.mark.asyncio
async def test_opportunity_profiles_evaluation() -> None:
    """Test OpportunityEngine evaluation across profile methodologies."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="TCS", company_name="Tata Consultancy Services", is_active=True)
        session.add(st)
        await session.flush()

        eval_d = date(2026, 8, 10)
        for i in range(60):
            d = eval_d - timedelta(days=i)
            dp = DailyPrice(
                stock_id=st.id,
                trade_date=d,
                open=Decimal(str(3790.0 + i * 2.0)),
                close=Decimal(str(3800.0 + i * 2.0)),
                high=Decimal(str(3850.0 + i * 2.0)),
                low=Decimal(str(3780.0 + i * 2.0)),
                volume=200000,
                delivery_percentage=Decimal("60.0"),
            )
            session.add(dp)
        await session.commit()

        service = Stock360Service(session)
        snap = await service.get_stock360_snapshot("TCS", evaluation_date=eval_d)

        breakout_res = OpportunityEngine.evaluate_profile(snap, "BREAKOUT")
        assert breakout_res.profile == "BREAKOUT"
        assert breakout_res.score >= 0.0
        assert breakout_res.classification in ("STRONG_RESEARCH_CANDIDATE", "RESEARCH_CANDIDATE", "WATCH", "WAIT", "NO_TRADE")

        pullback_res = OpportunityEngine.evaluate_profile(snap, "QUALITY_PULLBACK")
        assert pullback_res.profile == "QUALITY_PULLBACK"


@pytest.mark.asyncio
async def test_btst_readiness_and_overnight_veto() -> None:
    """Test BTST readiness evaluation and overnight event risk VETO logic."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="RELIANCE", company_name="Reliance Industries", is_active=True)
        session.add(st)
        await session.flush()

        eval_d = date(2026, 8, 10)
        dp = DailyPrice(
            stock_id=st.id,
            trade_date=eval_d,
            open=Decimal("2480.0"),
            high=Decimal("2510.0"),
            low=Decimal("2475.0"),
            close=Decimal("2500.0"),
            volume=1000000,
            delivery_percentage=Decimal("50.0"),
        )
        session.add(dp)

        ne = NewsEvent(
            source_name="NSE_FILING",
            source_type="OFFICIAL_PRIMARY",
            source_reference="ANN1",
            published_at=datetime(2026, 8, 9, 16, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 9, 16, 0, tzinfo=UTC),
            effective_at=date(2026, 8, 11),
            fetched_at=datetime(2026, 8, 9, 16, 5, tzinfo=UTC),
            headline="Board meeting tomorrow for Q1 Financial Results",
            summary="Scheduled Q1 earnings release",
            event_type="COMPANY",
            category="FINANCIAL_RESULTS",
            deterministic_context="NEUTRAL",
            event_severity="HIGH",
            event_risk_flag="OVERNIGHT_EVENT_RISK",
            stock_id=st.id,
            idempotency_hash="hash_reliance_results_1",
        )
        session.add(ne)
        await session.commit()

        from app.services.news_policy_service import NewsPolicyService
        eval_dt = datetime.combine(eval_d, datetime.min.time(), tzinfo=UTC)
        ns = NewsPolicyService(session)
        n_snap = await ns.get_company_news_policy_snapshot(symbol="RELIANCE", evaluation_datetime=eval_dt)
        assert n_snap is not None

        service = Stock360Service(session)
        snap = await service.get_stock360_snapshot("RELIANCE", evaluation_date=eval_d)

        btst_assessment = OpportunityEngine.evaluate_profile(snap, "BTST_READINESS")
        assert btst_assessment.classification == "REJECTED"
        assert any("OVERNIGHT_EVENT_RISK_ACTIVE" in v for v in btst_assessment.veto_reasons)


@pytest.mark.asyncio
async def test_opportunity_ranking_service() -> None:
    """Test OpportunityRankingService ranking candidates across bounded stock universe."""
    factory = get_session_factory()
    async with factory() as session:
        st1 = Stock(symbol="HDFCBANK", company_name="HDFC Bank", is_active=True)
        st2 = Stock(symbol="ICICIBANK", company_name="ICICI Bank", is_active=True)
        session.add_all([st1, st2])
        await session.flush()

        eval_d = date(2026, 8, 10)
        for st in (st1, st2):
            dp = DailyPrice(
                stock_id=st.id,
                trade_date=eval_d,
                open=Decimal("1580.0"),
                high=Decimal("1610.0"),
                low=Decimal("1575.0"),
                close=Decimal("1600.0"),
                volume=500000,
            )
            session.add(dp)
        await session.commit()

        service = OpportunityRankingService(session)
        summary = await service.rank_opportunities(profile="BREAKOUT", evaluation_date=eval_d, limit=10)

        assert summary.profile == "BREAKOUT"
        assert summary.total_evaluated >= 2
        assert len(summary.candidates) <= 10


@pytest.mark.asyncio
async def test_stock360_and_opportunity_api_and_ui() -> None:
    """Test Stock 360 and Opportunity Intelligence REST API and HTMX UI partials."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="SBIN", company_name="State Bank of India", is_active=True)
        session.add(st)
        await session.flush()

        eval_d = date(2026, 8, 10)
        dp = DailyPrice(
            stock_id=st.id,
            trade_date=eval_d,
            open=Decimal("790.0"),
            high=Decimal("805.0"),
            low=Decimal("785.0"),
            close=Decimal("800.0"),
            volume=300000,
        )
        session.add(dp)
        await session.commit()

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # 1. REST API: Stock 360
        r_360 = await client.get(f"/api/v1/market-data/stocks/SBIN/360?evaluation_date={eval_d.isoformat()}")
        assert r_360.status_code == 200
        data_360 = r_360.json()
        assert data_360["symbol"] == "SBIN"

        # 2. REST API: Opportunities
        r_opp = await client.get(f"/api/v1/market-data/opportunities?profile=BREAKOUT&evaluation_date={eval_d.isoformat()}")
        assert r_opp.status_code == 200
        data_opp = r_opp.json()
        assert data_opp["profile"] == "BREAKOUT"

        # 3. UI Partial: Stock 360 cards
        r_ui_360 = await client.get(f"/market-data/stock360/cards?symbol=SBIN&evaluation_date={eval_d.isoformat()}")
        assert r_ui_360.status_code == 200
        assert "SBIN" in r_ui_360.text

        # 4. UI Partial: Opportunity Board
        r_ui_board = await client.get(f"/market-data/opportunities/board?profile=BREAKOUT&evaluation_date={eval_d.isoformat()}")
        assert r_ui_board.status_code == 200
        assert "Total Evaluated" in r_ui_board.text
