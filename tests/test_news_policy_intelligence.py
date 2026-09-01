"""Comprehensive test suite for News + Government Policy Intelligence — Sprint 19.1F.

Tests:
  - Model invariants, SHA-256 fingerprinting, idempotency
  - Point-in-time (PIT) lookahead bias protection
  - Entity resolution (Symbol / SecurityAlias / Sector)
  - Government policy, RBI, and SEBI regulatory event classification
  - Event risk flags, overnight risk, policy headwinds/tailwinds
  - REST API endpoints and HTMX UI partial
  - Phase C fabrication-hazard regressions:
      * Market snapshot UNKNOWN (not NEUTRAL) when zero events
      * Coverage status NOT_AVAILABLE (not PARTIAL) when zero events
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.db.session import get_session_factory
from app.models.fundamental_corporate import CorporateAnnouncement
from app.models.news_policy import NewsEntityLink, NewsEvent
from app.models.sector import Sector
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock
from app.repositories.news_policy import NewsPolicyRepository
from app.services.news_policy_service import NewsPolicyService


@pytest.mark.asyncio
async def test_news_event_model_and_idempotency():
    """Verify NewsEvent model invariants and SHA-256 idempotency hash."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="INFY", company_name="Infosys Ltd", is_active=True)
        session.add(st)
        await session.flush()

        evt1 = NewsEvent(
            headline="Infosys Wins $1.5B Digital Transformation Deal",
            summary="Multi-year IT services contract signed with European bank.",
            source_name="NSE_CORPORATE",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 10, 10, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 10, 10, 5, tzinfo=UTC),
            event_type="COMPANY",
            category="ORDER_WIN",
            scope_type="STOCK",
            deterministic_context="POSITIVE_CONTEXT",
            event_severity="HIGH",
            stock_id=st.id,
            idempotency_hash="",
        )
        evt1.idempotency_hash = evt1.compute_fingerprint()
        session.add(evt1)
        await session.commit()

        repo = NewsPolicyRepository(session)
        summary = await repo.get_coverage_summary()
        assert summary["total_events_count"] == 1
        assert summary["company_events_count"] == 1

        # Attempt duplicate insertion
        evt2 = NewsEvent(
            headline="Infosys Wins $1.5B Digital Transformation Deal",
            summary="Duplicate payload",
            source_name="NSE_CORPORATE",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 10, 10, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 10, 10, 5, tzinfo=UTC),
            event_type="COMPANY",
            category="ORDER_WIN",
            stock_id=st.id,
            idempotency_hash="",
        )
        evt2.idempotency_hash = evt2.compute_fingerprint()
        inserted = await repo.bulk_upsert_news_events([evt2])
        assert inserted == 0


@pytest.mark.asyncio
async def test_point_in_time_lookahead_protection():
    """Verify events with available_at > cutoff are hidden from historical evaluation."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="TATASTEEL", company_name="Tata Steel Ltd", is_active=True)
        session.add(st)
        await session.flush()

        # Event 1: Published & available on 2026-08-01
        e1 = NewsEvent(
            headline="Tata Steel Q1 Results Announced",
            source_name="NSE_INDIA",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 1, 14, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 1, 14, 0, tzinfo=UTC),
            event_type="COMPANY",
            category="RESULTS",
            deterministic_context="POSITIVE_CONTEXT",
            stock_id=st.id,
            idempotency_hash="hash_e1",
        )
        # Event 2: Published & available on 2026-08-15
        e2 = NewsEvent(
            headline="Import Duty on Steel Increased by 15%",
            source_name="MINISTRY_OF_FINANCE",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
            event_type="GOVERNMENT_POLICY",
            category="CUSTOMS",
            deterministic_context="POSITIVE_CONTEXT",
            stock_id=st.id,
            idempotency_hash="hash_e2",
        )
        session.add_all([e1, e2])
        await session.commit()

        service = NewsPolicyService(session)

        # Evaluation at 2026-08-10 (e2 MUST BE HIDDEN)
        snap_early = await service.get_company_news_policy_snapshot(
            symbol="TATASTEEL",
            evaluation_datetime=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        )
        assert len(snap_early.recent_events) == 1
        assert snap_early.recent_events[0].headline == "Tata Steel Q1 Results Announced"

        # Evaluation at 2026-08-20 (Both e1 & e2 visible)
        snap_late = await service.get_company_news_policy_snapshot(
            symbol="TATASTEEL",
            evaluation_datetime=datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
        )
        assert len(snap_late.recent_events) == 2


@pytest.mark.asyncio
async def test_entity_resolution_and_multi_stock():
    """Verify text resolution using Stock symbol and SecurityAlias, and NewsEntityLink."""
    factory = get_session_factory()
    async with factory() as session:
        sec = Sector(name="Automobile")
        session.add(sec)
        await session.flush()

        st = Stock(symbol="TATAMOTORS", company_name="Tata Motors Ltd", sector_id=sec.id, is_active=True)
        session.add(st)
        await session.flush()

        alias = SecurityAlias(
            stock_id=st.id,
            exchange="NSE",
            symbol="TMPL",
            valid_from=date(2026, 1, 1),
            available_at=date(2026, 1, 1),
        )
        session.add(alias)
        await session.commit()

        service = NewsPolicyService(session)
        res1 = await service.resolve_stock_from_text("Breakout in TATAMOTORS after EV policy update")
        assert res1 == st.id

        res2 = await service.resolve_stock_from_text("High volume in TMPL today")
        assert res2 == st.id

        # Multi-stock entity link
        evt = NewsEvent(
            headline="PLI Scheme for Automobile Sector Extended by 2 Years",
            source_name="PIB_INDIA",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
            event_type="GOVERNMENT_POLICY",
            category="PLI",
            scope_type="MULTI_STOCK",
            sector_id=sec.id,
            deterministic_context="POSITIVE_CONTEXT",
            idempotency_hash="hash_pli_auto",
        )
        session.add(evt)
        await session.flush()

        link = NewsEntityLink(
            news_event_id=evt.id,
            stock_id=st.id,
            sector_id=sec.id,
            impact_relation="DIRECT",
            deterministic_context="POSITIVE_CONTEXT",
        )
        session.add(link)
        await session.commit()

        snap_sec = await service.get_sector_news_policy_snapshot(
            sector_identifier="Automobile",
            evaluation_datetime=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
        )
        assert snap_sec.sector_name == "Automobile"
        assert snap_sec.policy_changes_count == 1


@pytest.mark.asyncio
async def test_government_policy_rbi_and_sebi_events():
    """Verify RBI monetary, SEBI regulatory, and market policy snapshots."""
    factory = get_session_factory()
    async with factory() as session:
        e_rbi = NewsEvent(
            headline="RBI MPC Keeps Repo Rate Unchanged at 6.50%",
            source_name="RBI",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 5, 11, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 5, 11, 0, tzinfo=UTC),
            event_type="RBI_MONETARY",
            category="REPO_RATE",
            scope_type="MARKET",
            government_body="RBI",
            regulator="RBI",
            deterministic_context="NEUTRAL",
            event_severity="HIGH",
            idempotency_hash="hash_rbi_mpc",
        )
        e_sebi = NewsEvent(
            headline="SEBI Framework for Index Derivatives Position Limits",
            source_name="SEBI",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 8, 17, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 8, 17, 0, tzinfo=UTC),
            event_type="SEBI_REGULATORY",
            category="REGULATORY",
            scope_type="MARKET",
            regulator="SEBI",
            deterministic_context="NEGATIVE_CONTEXT",
            event_severity="CRITICAL",
            event_risk_flag="REGULATORY_EVENT",
            idempotency_hash="hash_sebi_deriv",
        )
        session.add_all([e_rbi, e_sebi])
        await session.commit()

        service = NewsPolicyService(session)
        market_snap = await service.get_market_news_policy_snapshot(
            evaluation_datetime=datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
        )

        assert len(market_snap.rbi_monetary_events) == 1
        assert len(market_snap.sebi_regulatory_events) == 1
        assert market_snap.rbi_monetary_events[0].headline == "RBI MPC Keeps Repo Rate Unchanged at 6.50%"


@pytest.mark.asyncio
async def test_event_risk_calculation_and_upcoming_risks():
    """Verify overnight risk calculation and pending corporate announcement risks."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="HDFCBANK", company_name="HDFC Bank Ltd", is_active=True)
        session.add(st)
        await session.flush()

        # Corporate board meeting announcement
        ca = CorporateAnnouncement(
            stock_id=st.id,
            category="BOARD_MEETING",
            announcement_date=date(2026, 8, 15),
            available_at=date(2026, 8, 1),
            title="Board Meeting scheduled for Q1 Financial Results",
            idempotency_hash="hash_hdfc_board",
        )
        # News event with overnight risk flag
        ne = NewsEvent(
            headline="HDFC Bank Board Meeting for Capital Raise",
            source_name="NSE_ANNOUNCEMENT",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 2, 9, 0, tzinfo=UTC),
            event_type="COMPANY",
            category="BOARD_MEETING",
            event_risk_flag="OVERNIGHT_EVENT_RISK",
            stock_id=st.id,
            idempotency_hash="hash_hdfc_ne",
        )
        session.add_all([ca, ne])
        await session.commit()

        service = NewsPolicyService(session)
        snap = await service.get_company_news_policy_snapshot(
            symbol="HDFCBANK",
            evaluation_datetime=datetime(2026, 8, 10, 0, 0, tzinfo=UTC),
            as_of_date=date(2026, 8, 10),
        )

        assert snap.overnight_event_risk is True
        assert len(snap.upcoming_event_risks) == 1
        assert snap.upcoming_event_risks[0].event_risk_flag == "BOARD_MEETING_PENDING"


@pytest.mark.asyncio
async def test_news_policy_rest_api_and_ui_partial():
    """Verify REST API endpoints and HTMX UI partial rendering for news & policy."""
    factory = get_session_factory()
    async with factory() as session:
        st = Stock(symbol="TCS", company_name="Tata Consultancy Services Ltd", is_active=True)
        session.add(st)
        await session.flush()

        ne = NewsEvent(
            headline="TCS Signs Mega $2B Cloud Deal with European Retailer",
            source_name="NSE_INDIA",
            source_type="OFFICIAL_PRIMARY",
            published_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
            available_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
            event_type="COMPANY",
            category="ORDER_WIN",
            deterministic_context="POSITIVE_CONTEXT",
            stock_id=st.id,
            idempotency_hash="hash_tcs_deal",
        )
        session.add(ne)
        await session.commit()

    from tests.auth_helpers import auth_client, seed_builtins

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # 1. REST API Snapshot
        resp = await client.get(
            "/api/v1/market-data/news/TCS/snapshot",
            params={"as_of_date": "2026-08-10"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "TCS"
        assert len(data["recent_events"]) == 1
        assert data["positive_context_count"] == 1

        # 2. REST API Events List
        resp_events = await client.get(
            "/api/v1/market-data/news/TCS",
            params={"as_of_date": "2026-08-10"},
        )
        assert resp_events.status_code == 200
        assert len(resp_events.json()) == 1

        # 3. REST API Market Policy Snapshot
        resp_mkt = await client.get("/api/v1/market-data/policy/market-snapshot")
        assert resp_mkt.status_code == 200

        # 4. REST API Coverage
        resp_cov = await client.get("/api/v1/market-data/news/coverage")
        assert resp_cov.status_code == 200

        # 5. HTMX UI Partial
        resp_ui = await client.get(
            "/market-data/news/cards",
            params={"symbol": "TCS", "as_of_date": "2026-08-10"},
        )
        assert resp_ui.status_code == 200
        assert "TCS — News &amp; Policy Intelligence" in resp_ui.text or "TCS — News & Policy Intelligence" in resp_ui.text
        assert "TCS Signs Mega $2B Cloud Deal" in resp_ui.text


# ---------------------------------------------------------------------------
# Phase C — Fabrication-hazard regression tests (Stage 2.6.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_market_snapshot_unknown_sentiment_when_no_events():
    """Regression: market snapshot must return UNKNOWN (not NEUTRAL) when zero events.

    When news_events is empty, pos=0 and neg=0 are equal, but that does NOT mean
    the market sentiment is NEUTRAL — it means the sentiment is UNKNOWN (no data).
    Fabricating NEUTRAL from an empty dataset is a PIT / data-quality violation.
    """
    factory = get_session_factory()
    async with factory() as session:
        service = NewsPolicyService(session)
        from datetime import UTC, datetime

        snap = await service.get_market_news_policy_snapshot(
            evaluation_datetime=datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
        )
        # Zero events → sentiment must be UNKNOWN, never NEUTRAL
        assert snap.overall_sentiment_context == "UNKNOWN", (
            f"Expected UNKNOWN when no events, got {snap.overall_sentiment_context!r}"
        )
        assert "NO_MARKET_POLICY_EVENTS" in snap.quality_warnings


@pytest.mark.asyncio
async def test_coverage_status_not_available_when_no_events():
    """Regression: coverage_status must be NOT_AVAILABLE (not PARTIAL) when zero events.

    PARTIAL implies data is flowing in but incompletely. When no events have ever been
    loaded, the correct truthful status is NOT_AVAILABLE.
    """
    factory = get_session_factory()
    async with factory() as session:
        service = NewsPolicyService(session)
        cov = await service.get_news_coverage()
        if cov.total_events_count == 0:
            assert cov.coverage_status == "NOT_AVAILABLE", (
                f"Expected NOT_AVAILABLE when zero events, got {cov.coverage_status!r}"
            )
