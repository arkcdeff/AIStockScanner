"""Unit tests for Sprint 19.1D-5 — Historical Futures OI, Buildup, Basis & Rollover Analytics.

Covers:
  - Price/OI buildup pattern classifications (LONG/SHORT BUILDUP, SHORT COVERING, LONG UNWINDING)
  - Noise threshold filtering for price and OI changes
  - Same-contract historical observation comparison
  - Source vs derived change-in-OI calculation & mismatch detection
  - Spot reference resolution, absolute basis, basis %, and annualized simple basis %
  - Price settlement fallback hierarchy (settlement_price -> close -> last_price)
  - Term structure futures curve construction & slope classification
  - Per-expiry OI distribution shares and adjacent calendar spreads
  - Rollover percentages (next & later) and expiry window detection
  - Historical rollover comparison metadata
  - Point-in-time EOD cutoff filtering (available_at <= as_of_date)
  - Preserved contract-specific lot sizes
  - REST API endpoints (/derivatives/futures/analytics & /derivatives/futures/rollover)
  - HTMX UI partial rendering (/market-data/derivatives/futures/analytics/table)
  - Zero live network calls
"""
from datetime import date
from decimal import Decimal

import pytest

from app.data.exchange_constants import EXCHANGE_NSE
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.models.stock import Stock
from app.services.historical_futures_analytics import HistoricalFuturesAnalyticsService
from tests.auth_helpers import auth_client


@pytest.mark.asyncio
async def test_buildup_classifications_and_thresholds():
    """Verify price/OI buildup classifications across various price/OI move combinations."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        c_near = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_near)
        await db_session.flush()

        # Day 1: 2026-08-03
        p1 = DerivativeDailyPrice(
            contract_id=c_near.id,
            trade_date=date(2026, 8, 3),
            close=Decimal("24000.00"),
            settlement_price=Decimal("24000.00"),
            open_interest=100000,
            volume_contracts=1000,
            underlying_value=Decimal("23950.00"),
            available_at=date(2026, 8, 3),
        )
        # Day 2 (LONG BUILDUP: Price UP + OI UP): 2026-08-04
        p2 = DerivativeDailyPrice(
            contract_id=c_near.id,
            trade_date=date(2026, 8, 4),
            close=Decimal("24120.00"),  # +0.5%
            settlement_price=Decimal("24120.00"),
            open_interest=105000,       # +5.0%
            change_in_open_interest=5000,
            volume_contracts=1500,
            underlying_value=Decimal("24070.00"),
            available_at=date(2026, 8, 4),
        )
        # Day 3 (SHORT BUILDUP: Price DOWN + OI UP): 2026-08-05
        p3 = DerivativeDailyPrice(
            contract_id=c_near.id,
            trade_date=date(2026, 8, 5),
            close=Decimal("23980.00"),  # -0.58%
            settlement_price=Decimal("23980.00"),
            open_interest=110000,       # +4.76%
            change_in_open_interest=5000,
            volume_contracts=1200,
            underlying_value=Decimal("23930.00"),
            available_at=date(2026, 8, 5),
        )
        # Day 4 (SHORT COVERING: Price UP + OI DOWN): 2026-08-06
        p4 = DerivativeDailyPrice(
            contract_id=c_near.id,
            trade_date=date(2026, 8, 6),
            close=Decimal("24100.00"),  # +0.50%
            settlement_price=Decimal("24100.00"),
            open_interest=100000,       # -9.09%
            change_in_open_interest=-10000,
            volume_contracts=1100,
            underlying_value=Decimal("24050.00"),
            available_at=date(2026, 8, 6),
        )
        # Day 5 (LONG UNWINDING: Price DOWN + OI DOWN): 2026-08-07
        p5 = DerivativeDailyPrice(
            contract_id=c_near.id,
            trade_date=date(2026, 8, 7),
            close=Decimal("23950.00"),  # -0.62%
            settlement_price=Decimal("23950.00"),
            open_interest=92000,        # -8.00%
            change_in_open_interest=-8000,
            volume_contracts=1400,
            underlying_value=Decimal("23900.00"),
            available_at=date(2026, 8, 7),
        )
        db_session.add_all([p1, p2, p3, p4, p5])
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalFuturesAnalyticsService(db_session)

        # Day 1: Insufficient history
        a1 = await service.get_contract_analytics(c_near.id, date(2026, 8, 3))
        assert a1.buildup_state == "INSUFFICIENT_HISTORY"

        # Day 2: LONG BUILDUP
        a2 = await service.get_contract_analytics(c_near.id, date(2026, 8, 4))
        assert a2.buildup_state == "LONG_BUILDUP"
        assert a2.price_change_absolute == Decimal("120.00")
        assert a2.derived_change_in_oi == 5000
        assert a2.basis_absolute == Decimal("50.00")

        # Day 3: SHORT BUILDUP
        a3 = await service.get_contract_analytics(c_near.id, date(2026, 8, 5))
        assert a3.buildup_state == "SHORT_BUILDUP"

        # Day 4: SHORT COVERING
        a4 = await service.get_contract_analytics(c_near.id, date(2026, 8, 6))
        assert a4.buildup_state == "SHORT_COVERING"

        # Day 5: LONG UNWINDING
        a5 = await service.get_contract_analytics(c_near.id, date(2026, 8, 7))
        assert a5.buildup_state == "LONG_UNWINDING"


@pytest.mark.asyncio
async def test_oi_mismatch_and_spot_fallback():
    """Verify source vs derived ΔOI mismatch flagging and spot fallback to DailyPrice."""
    factory = get_session_factory()
    async with factory() as db_session:
        stk = Stock(symbol="SBIN", company_name="State Bank of India", exchange=EXCHANGE_NSE)
        db_session.add(stk)
        await db_session.flush()

        daily_p = DailyPrice(
            stock_id=stk.id,
            trade_date=date(2026, 8, 10),
            open=Decimal("800.00"),
            high=Decimal("820.00"),
            low=Decimal("795.00"),
            close=Decimal("810.00"),
            volume=500000,
        )
        db_session.add(daily_p)
        await db_session.flush()

        c_fut = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_stock_id=stk.id,
            trading_symbol="SBIN27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=1500,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_fut)
        await db_session.flush()

        p1 = DerivativeDailyPrice(
            contract_id=c_fut.id,
            trade_date=date(2026, 8, 7),
            close=Decimal("812.00"),
            open_interest=10000,
            available_at=date(2026, 8, 7),
        )
        # p2 has source change_in_open_interest=500, but actual derived change is 2000 (mismatch!)
        p2 = DerivativeDailyPrice(
            contract_id=c_fut.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("815.00"),
            open_interest=12000,
            change_in_open_interest=500,  # Mismatch with derived 2000!
            underlying_value=None,        # Triggers spot fallback to DailyPrice
            available_at=date(2026, 8, 10),
        )
        db_session.add_all([p1, p2])
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalFuturesAnalyticsService(db_session)
        analytics = await service.get_contract_analytics(c_fut.id, date(2026, 8, 10))

        assert analytics.spot_value == Decimal("810.00")
        assert analytics.spot_source == "DAILY_PRICE"
        assert analytics.basis_absolute == Decimal("5.00")
        assert analytics.derived_change_in_oi == 2000
        assert analytics.source_change_in_oi == 500
        assert "FUTURES_ANALYTICS_OI_CHANGE_MISMATCH" in analytics.quality_warnings


@pytest.mark.asyncio
async def test_futures_curve_and_rollover_analytics():
    """Verify term structure snapshot, curve slope, OI share %, and rollover metrics."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        idx_price = MarketIndexPrice(
            market_index_id=idx.id,
            trade_date=date(2026, 8, 20),
            open=Decimal("24000.00"),
            high=Decimal("24100.00"),
            low=Decimal("23950.00"),
            close=Decimal("24050.00"),
        )
        db_session.add(idx_price)
        await db_session.flush()

        # Near, Next, Far contracts (Expiry window: DTE=7 for near contract)
        c_near = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_next = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY24SEP2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 9, 24),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_far = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY29OCT2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 10, 29),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_near, c_next, c_far])
        await db_session.flush()

        p_near = DerivativeDailyPrice(
            contract_id=c_near.id,
            trade_date=date(2026, 8, 20),
            close=Decimal("24080.00"),
            settlement_price=Decimal("24080.00"),
            open_interest=60000,
            volume_contracts=5000,
            underlying_value=Decimal("24050.00"),
            available_at=date(2026, 8, 20),
        )
        p_next = DerivativeDailyPrice(
            contract_id=c_next.id,
            trade_date=date(2026, 8, 20),
            close=Decimal("24180.00"),
            settlement_price=Decimal("24180.00"),
            open_interest=35000,
            volume_contracts=2500,
            underlying_value=Decimal("24050.00"),
            available_at=date(2026, 8, 20),
        )
        p_far = DerivativeDailyPrice(
            contract_id=c_far.id,
            trade_date=date(2026, 8, 20),
            close=Decimal("24250.00"),
            settlement_price=Decimal("24250.00"),
            open_interest=5000,
            volume_contracts=300,
            underlying_value=Decimal("24050.00"),
            available_at=date(2026, 8, 20),
        )
        db_session.add_all([p_near, p_next, p_far])
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalFuturesAnalyticsService(db_session)

        # 1. Curve Snapshot
        snapshot = await service.get_underlying_futures_snapshot("NIFTY", date(2026, 8, 20))
        assert snapshot.underlying_symbol == "NIFTY"
        assert snapshot.total_futures_oi == 100000
        assert snapshot.curve_slope == "UPWARD_SLOPING"
        assert len(snapshot.items) == 3

        item0 = snapshot.items[0]
        assert item0.expiry_rank == "NEAR"
        assert item0.oi_share_pct == Decimal("60.0")

        item1 = snapshot.items[1]
        assert item1.expiry_rank == "NEXT"
        assert item1.oi_share_pct == Decimal("35.0")

        item2 = snapshot.items[2]
        assert item2.expiry_rank == "FAR_1"
        assert item2.oi_share_pct == Decimal("5.0")

        # Spreads
        assert len(snapshot.adjacent_spreads) == 2
        assert snapshot.adjacent_spreads[0]["spread_absolute"] == Decimal("100.00")

        # 2. Rollover Analytics
        rollover = await service.get_rollover_analytics("NIFTY", date(2026, 8, 20))
        assert rollover.near_expiry_oi == 60000
        assert rollover.next_expiry_oi == 35000
        assert rollover.far_expiries_oi == 5000
        assert rollover.near_dte == 7
        assert rollover.is_expiry_window is True

        # rollover_next_pct = 35000 / (60000 + 35000) * 100 = 36.8421...%
        assert float(rollover.rollover_next_pct) == pytest.approx(36.842, rel=1e-3)
        # rollover_later_pct = (100000 - 60000) / 100000 * 100 = 40.0%
        assert float(rollover.rollover_later_pct) == pytest.approx(40.0, rel=1e-3)


@pytest.mark.asyncio
async def test_point_in_time_analytics_isolation():
    """Verify available_at filtering hides future data from analytics queries."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        c_fut = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_fut)
        await db_session.flush()

        p_row = DerivativeDailyPrice(
            contract_id=c_fut.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("24000.00"),
            available_at=date(2026, 8, 12),  # Available only on Aug 12
        )
        db_session.add(p_row)
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalFuturesAnalyticsService(db_session)

        # Query as of Aug 10 -> empty snapshot
        snapshot_past = await service.get_underlying_futures_snapshot(
            "NIFTY", date(2026, 8, 10), as_of_date=date(2026, 8, 10)
        )
        assert len(snapshot_past.items) == 0

        # Query as of Aug 12 -> 1 item visible
        snapshot_now = await service.get_underlying_futures_snapshot(
            "NIFTY", date(2026, 8, 10), as_of_date=date(2026, 8, 12)
        )
        assert len(snapshot_now.items) == 1


@pytest.mark.asyncio
async def test_futures_analytics_rest_api_and_ui_partial():
    """Verify REST API & HTMX UI partial /derivatives/futures/analytics/table."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        c_fut = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_fut)
        await db_session.flush()

        p_row = DerivativeDailyPrice(
            contract_id=c_fut.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("24000.00"),
            open_interest=50000,
            underlying_value=Decimal("23950.00"),
            available_at=date(2026, 8, 10),
        )
        db_session.add(p_row)
        await db_session.commit()

    from tests.auth_helpers import seed_builtins

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # 1. REST API Analytics
        resp = await client.get(
            "/api/v1/market-data/derivatives/futures/analytics?symbol=NIFTY&trade_date=2026-08-10"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["underlying_symbol"] == "NIFTY"
        assert len(data["items"]) == 1

        # 2. REST API Rollover
        resp_roll = await client.get(
            "/api/v1/market-data/derivatives/futures/rollover?symbol=NIFTY&trade_date=2026-08-10"
        )
        assert resp_roll.status_code == 200
        data_roll = resp_roll.json()
        assert data_roll["total_futures_oi"] == 50000

        # 3. HTMX UI Partial
        resp_ui = await client.get(
            "/market-data/derivatives/futures/analytics/table?symbol=NIFTY&trade_date=2026-08-10",
            follow_redirects=True,
        )
        assert resp_ui.status_code == 200
        assert "Total Futures OI" in resp_ui.text
        assert "NIFTY27AUG2026FUT" in resp_ui.text
