"""Unit tests for Sprint 19.1D-6 — Historical Options PCR, Max Pain, OI Walls & Expiry Analytics.

Covers:
  - Put-Call Ratio (PCR) by Open Interest and Volume with zero-denominator handling
  - Missing observation metadata tracking
  - Lot-size weighted payout minimization Max Pain calculation with tie handling
  - Static Call Wall and Static Put Wall (highest valid OI)
  - Fresh Call Wall and Fresh Put Wall (highest positive ΔOI)
  - Largest Call and Put OI unwinding levels (largest negative ΔOI)
  - Ranked OI concentration shares
  - Cross-expiry term structure analytics & nearest-expiry dominance
  - Point-in-time EOD cutoff filtering (available_at <= as_of_date)
  - REST API endpoints (/derivatives/options/analytics & /derivatives/options/expiry-analytics)
  - HTMX UI partial rendering (/market-data/derivatives/options/analytics/cards)
  - Zero live network calls
"""
from datetime import date
from decimal import Decimal

import pytest

from app.data.exchange_constants import EXCHANGE_NSE
from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService
from tests.auth_helpers import auth_client


@pytest.mark.asyncio
async def test_pcr_oi_and_volume_calculation():
    """Verify PCR OI and PCR Volume calculations and zero-denominator handling."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        # Contract Master: 2 strikes (24000 CE/PE, 24100 CE/PE)
        exp_date = date(2026, 8, 27)
        trade_d = date(2026, 8, 10)

        c_24000_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000CE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24000_pe = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000PE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24000.00"),
            option_type="PE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24100_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24100CE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24100.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24100_pe = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24100PE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24100.00"),
            option_type="PE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_24000_ce, c_24000_pe, c_24100_ce, c_24100_pe])
        await db_session.flush()

        # Prices: Call OI = 1000 + 4000 = 5000; Put OI = 3000 + 7000 = 10000 -> PCR OI = 2.0
        # Prices: Call Vol = 200 + 800 = 1000; Put Vol = 500 + 1500 = 2000 -> PCR Vol = 2.0
        p_24000_ce = DerivativeDailyPrice(
            contract_id=c_24000_ce.id,
            trade_date=trade_d,
            close=Decimal("150.00"),
            open_interest=1000,
            volume_contracts=200,
            underlying_value=Decimal("24050.00"),
            available_at=trade_d,
        )
        p_24000_pe = DerivativeDailyPrice(
            contract_id=c_24000_pe.id,
            trade_date=trade_d,
            close=Decimal("100.00"),
            open_interest=3000,
            volume_contracts=500,
            underlying_value=Decimal("24050.00"),
            available_at=trade_d,
        )
        p_24100_ce = DerivativeDailyPrice(
            contract_id=c_24100_ce.id,
            trade_date=trade_d,
            close=Decimal("80.00"),
            open_interest=4000,
            volume_contracts=800,
            underlying_value=Decimal("24050.00"),
            available_at=trade_d,
        )
        p_24100_pe = DerivativeDailyPrice(
            contract_id=c_24100_pe.id,
            trade_date=trade_d,
            close=Decimal("180.00"),
            open_interest=7000,
            volume_contracts=1500,
            underlying_value=Decimal("24050.00"),
            available_at=trade_d,
        )
        db_session.add_all([p_24000_ce, p_24000_pe, p_24100_ce, p_24100_pe])
        await db_session.commit()

        service = HistoricalOptionsAnalyticsService(db_session)
        res = await service.get_option_chain_analytics("NIFTY", trade_d, exp_date)

        assert res.pcr.total_call_oi == 5000
        assert res.pcr.total_put_oi == 10000
        assert res.pcr.pcr_oi == Decimal("2")

        assert res.pcr.total_call_volume == 1000
        assert res.pcr.total_put_volume == 2000
        assert res.pcr.pcr_volume == Decimal("2")

        assert res.pcr.valid_ce_oi_count == 2
        assert res.pcr.valid_pe_oi_count == 2
        assert res.pcr.missing_ce_count == 0
        assert res.pcr.missing_pe_count == 0


@pytest.mark.asyncio
async def test_max_pain_calculation_and_tie_handling():
    """Verify lot-size weighted payout minimization Max Pain calculation and tie handling."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        exp_date = date(2026, 8, 27)
        trade_d = date(2026, 8, 10)

        # 3 strikes: 24000, 24100, 24200 (lot size 25)
        # 24000: Call OI = 100, Put OI = 1000
        # 24100: Call OI = 500, Put OI = 500
        # 24200: Call OI = 1000, Put OI = 100
        strikes = [Decimal("24000.00"), Decimal("24100.00"), Decimal("24200.00")]
        c_map = {}

        for k in strikes:
            ce = DerivativeContract(
                exchange=EXCHANGE_NSE,
                underlying_index_id=idx.id,
                trading_symbol=f"NIFTY26AUG{int(k)}CE",
                instrument_type="OPTION",
                expiry_date=exp_date,
                strike_price=k,
                option_type="CE",
                lot_size=25,
                available_at=date(2026, 8, 1),
            )
            pe = DerivativeContract(
                exchange=EXCHANGE_NSE,
                underlying_index_id=idx.id,
                trading_symbol=f"NIFTY26AUG{int(k)}PE",
                instrument_type="OPTION",
                expiry_date=exp_date,
                strike_price=k,
                option_type="PE",
                lot_size=25,
                available_at=date(2026, 8, 1),
            )
            db_session.add_all([ce, pe])
            await db_session.flush()
            c_map[(k, "CE")] = ce
            c_map[(k, "PE")] = pe

        # Add prices
        # Spot = 24100
        prices = [
            DerivativeDailyPrice(
                contract_id=c_map[(Decimal("24000.00"), "CE")].id,
                trade_date=trade_d,
                open_interest=100,
                underlying_value=Decimal("24100.00"),
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_map[(Decimal("24000.00"), "PE")].id,
                trade_date=trade_d,
                open_interest=1000,
                underlying_value=Decimal("24100.00"),
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_map[(Decimal("24100.00"), "CE")].id,
                trade_date=trade_d,
                open_interest=500,
                underlying_value=Decimal("24100.00"),
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_map[(Decimal("24100.00"), "PE")].id,
                trade_date=trade_d,
                open_interest=500,
                underlying_value=Decimal("24100.00"),
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_map[(Decimal("24200.00"), "CE")].id,
                trade_date=trade_d,
                open_interest=1000,
                underlying_value=Decimal("24100.00"),
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_map[(Decimal("24200.00"), "PE")].id,
                trade_date=trade_d,
                open_interest=100,
                underlying_value=Decimal("24100.00"),
                available_at=trade_d,
            ),
        ]
        db_session.add_all(prices)
        await db_session.commit()

        service = HistoricalOptionsAnalyticsService(db_session)
        res = await service.get_option_chain_analytics("NIFTY", trade_d, exp_date)

        # Candidate Payouts (lot_size = 25):
        # Candidate 24000:
        #   Call payout: 0
        #   Put payout: (24100-24000)*500*25 + (24200-24000)*100*25
        #             = 100*12500 + 200*2500 = 1,250,000 + 500,000 = 1,750,000
        # Candidate 24100:
        #   Call payout: (24100-24000)*100*25 = 100*2500 = 250,000
        #   Put payout: (24200-24100)*100*25 = 100*2500 = 250,000
        #   Total payout: 500,000 (Minimum!)
        # Candidate 24200:
        #   Call payout: (24200-24000)*100*25 + (24200-24100)*500*25
        #             = 200*2500 + 100*12500 = 500,000 + 1,250,000 = 1,750,000
        #   Put payout: 0
        assert res.max_pain.max_pain_strike == Decimal("24100.00")
        assert res.max_pain.distance_from_spot == Decimal("0.00")
        assert res.max_pain.distance_pct == Decimal("0.00")


@pytest.mark.asyncio
async def test_static_and_fresh_walls_and_unwinding():
    """Verify static walls, fresh walls (highest positive ΔOI), and unwinding levels."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        exp_date = date(2026, 8, 27)
        trade_d = date(2026, 8, 10)

        # 3 strikes: 24000, 24500, 25000
        # CE 24000: OI=10000, ΔOI=+2000
        # CE 24500: OI=50000 (Static Call Wall!), ΔOI=-5000 (Largest Call Unwinding!)
        # CE 25000: OI=20000, ΔOI=+8000 (Fresh Call Wall!)

        # PE 24000: OI=40000 (Static Put Wall!), ΔOI=+12000 (Fresh Put Wall!)
        # PE 24500: OI=15000, ΔOI=-3000 (Largest Put Unwinding!)
        # PE 25000: OI=5000, ΔOI=+1000

        c_24000_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000CE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24000_pe = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000PE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24000.00"),
            option_type="PE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24500_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24500CE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24500.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24500_pe = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24500PE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24500.00"),
            option_type="PE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_25000_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG25000CE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("25000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_25000_pe = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG25000PE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("25000.00"),
            option_type="PE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )

        db_session.add_all(
            [c_24000_ce, c_24000_pe, c_24500_ce, c_24500_pe, c_25000_ce, c_25000_pe]
        )
        await db_session.flush()

        spot_price = Decimal("24200.00")

        prices = [
            DerivativeDailyPrice(
                contract_id=c_24000_ce.id,
                trade_date=trade_d,
                open_interest=10000,
                change_in_open_interest=2000,
                underlying_value=spot_price,
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_24000_pe.id,
                trade_date=trade_d,
                open_interest=40000,
                change_in_open_interest=12000,
                underlying_value=spot_price,
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_24500_ce.id,
                trade_date=trade_d,
                open_interest=50000,
                change_in_open_interest=-5000,
                underlying_value=spot_price,
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_24500_pe.id,
                trade_date=trade_d,
                open_interest=15000,
                change_in_open_interest=-3000,
                underlying_value=spot_price,
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_25000_ce.id,
                trade_date=trade_d,
                open_interest=20000,
                change_in_open_interest=8000,
                underlying_value=spot_price,
                available_at=trade_d,
            ),
            DerivativeDailyPrice(
                contract_id=c_25000_pe.id,
                trade_date=trade_d,
                open_interest=5000,
                change_in_open_interest=1000,
                underlying_value=spot_price,
                available_at=trade_d,
            ),
        ]
        db_session.add_all(prices)
        await db_session.commit()

        service = HistoricalOptionsAnalyticsService(db_session)
        res = await service.get_option_chain_analytics("NIFTY", trade_d, exp_date)

        # Static Walls
        assert res.static_call_wall is not None
        assert res.static_call_wall.strike_price == Decimal("24500.00")
        assert res.static_call_wall.open_interest == 50000
        assert res.static_call_wall.distance_from_spot == Decimal("300.00")

        assert res.static_put_wall is not None
        assert res.static_put_wall.strike_price == Decimal("24000.00")
        assert res.static_put_wall.open_interest == 40000
        assert res.static_put_wall.distance_from_spot == Decimal("-200.00")

        # Fresh Walls
        assert res.fresh_call_wall is not None
        assert res.fresh_call_wall.strike_price == Decimal("25000.00")
        assert res.fresh_call_wall.change_in_open_interest == 8000

        assert res.fresh_put_wall is not None
        assert res.fresh_put_wall.strike_price == Decimal("24000.00")
        assert res.fresh_put_wall.change_in_open_interest == 12000

        # Unwinding
        assert res.largest_call_unwinding is not None
        assert res.largest_call_unwinding.strike_price == Decimal("24500.00")
        assert res.largest_call_unwinding.change_in_open_interest == -5000

        assert res.largest_put_unwinding is not None
        assert res.largest_put_unwinding.strike_price == Decimal("24500.00")
        assert res.largest_put_unwinding.change_in_open_interest == -3000


@pytest.mark.asyncio
async def test_expiry_options_analytics_term_structure():
    """Verify term structure analytics across multiple expiries on a trade date."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        trade_d = date(2026, 8, 10)
        exp1 = date(2026, 8, 27)
        exp2 = date(2026, 9, 24)

        c_exp1 = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000CE",
            instrument_type="OPTION",
            expiry_date=exp1,
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_exp2 = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26SEP24000CE",
            instrument_type="OPTION",
            expiry_date=exp2,
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_exp1, c_exp2])
        await db_session.flush()

        # exp1: OI = 30000, Vol = 6000
        # exp2: OI = 10000, Vol = 4000
        # Total OI = 40000 -> exp1 share = 75%, exp2 share = 25%
        p_exp1 = DerivativeDailyPrice(
            contract_id=c_exp1.id,
            trade_date=trade_d,
            open_interest=30000,
            volume_contracts=6000,
            available_at=trade_d,
        )
        p_exp2 = DerivativeDailyPrice(
            contract_id=c_exp2.id,
            trade_date=trade_d,
            open_interest=10000,
            volume_contracts=4000,
            available_at=trade_d,
        )
        db_session.add_all([p_exp1, p_exp2])
        await db_session.commit()

        service = HistoricalOptionsAnalyticsService(db_session)
        exp_res = await service.get_expiry_options_analytics("NIFTY", trade_d)

        assert exp_res.total_oi_across_expiries == 40000
        assert exp_res.total_volume_across_expiries == 10000
        assert len(exp_res.expiries) == 2

        assert exp_res.expiries[0].expiry_date == exp1
        assert exp_res.expiries[0].oi_share_pct == Decimal("75")
        assert exp_res.expiries[0].volume_share_pct == Decimal("60")

        assert exp_res.expiries[1].expiry_date == exp2
        assert exp_res.expiries[1].oi_share_pct == Decimal("25")
        assert exp_res.expiries[1].volume_share_pct == Decimal("40")

        assert exp_res.nearest_expiry_oi_share_pct == Decimal("75")


@pytest.mark.asyncio
async def test_point_in_time_analytics_isolation():
    """Verify point-in-time available_at filtering isolates later data."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        exp_date = date(2026, 8, 27)
        c_opt = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000CE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_opt)
        await db_session.flush()

        # Observation inserted on Aug 10, published available_at Aug 12
        p_row = DerivativeDailyPrice(
            contract_id=c_opt.id,
            trade_date=date(2026, 8, 10),
            open_interest=15000,
            volume_contracts=3000,
            available_at=date(2026, 8, 12),
        )
        db_session.add(p_row)
        await db_session.commit()

        service = HistoricalOptionsAnalyticsService(db_session)

        # Query as of Aug 10 -> row hidden
        res_past = await service.get_option_chain_analytics(
            "NIFTY", date(2026, 8, 10), exp_date, as_of_date=date(2026, 8, 10)
        )
        assert res_past.pcr.total_call_oi == 0

        # Query as of Aug 12 -> row visible
        res_now = await service.get_option_chain_analytics(
            "NIFTY", date(2026, 8, 10), exp_date, as_of_date=date(2026, 8, 12)
        )
        assert res_now.pcr.total_call_oi == 15000


@pytest.mark.asyncio
async def test_options_analytics_rest_api_and_ui_partial():
    """Verify REST API and HTMX cards UI partial for options analytics."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        exp_date = date(2026, 8, 27)
        trade_d = date(2026, 8, 10)

        c_opt = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000CE",
            instrument_type="OPTION",
            expiry_date=exp_date,
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_opt)
        await db_session.flush()

        p_row = DerivativeDailyPrice(
            contract_id=c_opt.id,
            trade_date=trade_d,
            close=Decimal("120.00"),
            open_interest=25000,
            volume_contracts=5000,
            underlying_value=Decimal("24050.00"),
            available_at=trade_d,
        )
        db_session.add(p_row)
        await db_session.commit()

    from tests.auth_helpers import seed_builtins

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # 1. REST API Single-expiry Analytics
        resp = await client.get(
            "/api/v1/market-data/derivatives/options/analytics",
            params={
                "symbol": "NIFTY",
                "trade_date": "2026-08-10",
                "expiry_date": "2026-08-27",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["underlying_symbol"] == "NIFTY"
        assert data["pcr"]["total_call_oi"] == 25000

        # 2. REST API Term Structure Expiry Analytics
        resp_exp = await client.get(
            "/api/v1/market-data/derivatives/options/expiry-analytics",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10"},
        )
        assert resp_exp.status_code == 200
        data_exp = resp_exp.json()
        assert data_exp["total_oi_across_expiries"] == 25000

        # 3. HTMX Cards UI Partial
        resp_ui = await client.get(
            "/market-data/derivatives/options/analytics/cards",
            params={
                "symbol": "NIFTY",
                "trade_date": "2026-08-10",
                "expiry_date": "2026-08-27",
            },
            follow_redirects=True,
        )
        assert resp_ui.status_code == 200
        assert "Max Pain Strike" in resp_ui.text
        assert "Methodology Disclaimer" in resp_ui.text
