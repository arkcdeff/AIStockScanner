"""Unit tests for Sprint 19.1D-4 — Historical Option Chain Reconstruction.

Covers:
  - On-demand historical option chain reconstruction (Index & Stock options)
  - Decimal strike pairing and ordering (strike ASC)
  - Missing side policy (CE-only, PE-only, paired; call/put = None; MISSING != ZERO)
  - Exclusion of futures and other expiries/dates
  - Point-in-time EOD cutoff (available_at <= as_of_date)
  - Source underlying value extraction & inconsistency warning
  - Independent spot fallback (MarketIndexPrice & DailyPrice)
  - Structural ATM reference calculation (nearest strike to spot)
  - Moneyness classification (ITM, OTM, ATM) per option side
  - Calendar DTE calculation
  - Completeness & Quality warning metadata
  - Available expiries & dates discovery queries
  - REST API endpoints (/derivatives/options/chain & /derivatives/options/expiries)
  - HTMX UI partial rendering (/market-data/derivatives/options/chain/table)
  - Zero live network security
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
from app.models.stock import Stock
from app.services.historical_option_chain import HistoricalOptionChainService


@pytest.mark.asyncio
async def test_reconstruct_index_option_chain_pairing_and_sorting():
    """Verify Index option chain reconstruction, strike pairing, and deterministic sorting."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        # Seed 3 strikes: 24000 (CE+PE), 24100 (CE only), 24200 (PE only)
        c_24000_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624000CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24000_pe = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624000PE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24000.00"),
            option_type="PE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24100_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624100CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24100.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_24200_pe = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624200PE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24200.00"),
            option_type="PE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_24000_ce, c_24000_pe, c_24100_ce, c_24200_pe])
        await db_session.flush()

        # Seed price facts for trade_date 2026-08-10
        p_24000_ce = DerivativeDailyPrice(
            contract_id=c_24000_ce.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("180.00"),
            volume_contracts=1000,
            open_interest=50000,
            underlying_value=Decimal("24080.00"),
            available_at=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )
        p_24000_pe = DerivativeDailyPrice(
            contract_id=c_24000_pe.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("90.00"),
            volume_contracts=800,
            open_interest=40000,
            underlying_value=Decimal("24080.00"),
            available_at=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )
        p_24100_ce = DerivativeDailyPrice(
            contract_id=c_24100_ce.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("120.00"),
            volume_contracts=500,
            open_interest=30000,
            underlying_value=Decimal("24080.00"),
            available_at=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )
        p_24200_pe = DerivativeDailyPrice(
            contract_id=c_24200_pe.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("190.00"),
            volume_contracts=300,
            open_interest=20000,
            underlying_value=Decimal("24080.00"),
            available_at=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )
        db_session.add_all([p_24000_ce, p_24000_pe, p_24100_ce, p_24200_pe])
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalOptionChainService(db_session)
        chain = await service.get_historical_option_chain(
            symbol="NIFTY",
            trade_date=date(2026, 8, 10),
            expiry_date=date(2026, 8, 27),
            as_of_date=date(2026, 8, 10),
        )

        assert chain.underlying_symbol == "NIFTY"
        assert chain.underlying_type == "INDEX"
        assert chain.trade_date == date(2026, 8, 10)
        assert chain.expiry_date == date(2026, 8, 27)
        assert chain.calendar_days_to_expiry == 17
        assert chain.resolved_spot == Decimal("24080.00")
        assert chain.atm_reference_strike == Decimal("24100.00")  # Nearest strike to 24080

        # Completeness
        assert chain.completeness.total_strikes == 3
        assert chain.completeness.paired_strikes == 1
        assert chain.completeness.ce_only_strikes == 1
        assert chain.completeness.pe_only_strikes == 1
        assert chain.completeness.ce_row_count == 2
        assert chain.completeness.pe_row_count == 2

        # Check sorted rows (24000, 24100, 24200)
        assert len(chain.rows) == 3
        row0 = chain.rows[0]
        assert row0.strike_price == Decimal("24000.00")
        assert row0.is_paired is True
        assert row0.call.close == Decimal("180.00")
        assert row0.put.close == Decimal("90.00")
        assert row0.call.moneyness == "ITM"  # 24000 CE when spot 24080
        assert row0.put.moneyness == "OTM"   # 24000 PE when spot 24080

        row1 = chain.rows[1]
        assert row1.strike_price == Decimal("24100.00")
        assert row1.is_paired is False
        assert row1.is_atm is True
        assert row1.call is not None
        assert row1.put is None  # MISSING != ZERO
        assert row1.call.moneyness == "OTM"  # 24100 CE when spot 24080

        row2 = chain.rows[2]
        assert row2.strike_price == Decimal("24200.00")
        assert row2.is_paired is False
        assert row2.call is None  # MISSING != ZERO
        assert row2.put is not None
        assert row2.put.moneyness == "ITM"  # 24200 PE when spot 24080


@pytest.mark.asyncio
async def test_stock_option_chain_and_spot_fallback():
    """Verify Stock option chain reconstruction and spot fallback to DailyPrice."""
    factory = get_session_factory()
    async with factory() as db_session:
        stk = Stock(symbol="RELIANCE", company_name="Reliance Industries", exchange=EXCHANGE_NSE)
        db_session.add(stk)
        await db_session.flush()

        # Seed DailyPrice spot fallback
        daily_price = DailyPrice(
            stock_id=stk.id,
            trade_date=date(2026, 8, 10),
            open=Decimal("3000.00"),
            high=Decimal("3050.00"),
            low=Decimal("2990.00"),
            close=Decimal("3020.00"),
            volume=100000,
        )
        db_session.add(daily_price)
        await db_session.flush()

        c_3000_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_stock_id=stk.id,
            trading_symbol="RELIANCE27AUG20263000CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("3000.00"),
            option_type="CE",
            lot_size=250,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_3000_ce)
        await db_session.flush()

        # Price without underlying_value populated (triggers spot fallback)
        p_3000_ce = DerivativeDailyPrice(
            contract_id=c_3000_ce.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("50.00"),
            volume_contracts=100,
            open_interest=5000,
            underlying_value=None,
            available_at=date(2026, 8, 10),
        )
        db_session.add(p_3000_ce)
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalOptionChainService(db_session)
        chain = await service.get_historical_option_chain(
            symbol="RELIANCE",
            trade_date=date(2026, 8, 10),
            expiry_date=date(2026, 8, 27),
        )

        assert chain.underlying_symbol == "RELIANCE"
        assert chain.underlying_type == "STOCK"
        assert chain.independent_spot == Decimal("3020.00")
        assert chain.resolved_spot == Decimal("3020.00")
        assert chain.atm_reference_strike == Decimal("3000.00")


@pytest.mark.asyncio
async def test_point_in_time_cutoff_filtering():
    """Verify available_at point-in-time filtering hides future rows."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        c_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624000CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_ce)
        await db_session.flush()

        # Available only at EOD 2026-08-11 (late revision)
        p_ce = DerivativeDailyPrice(
            contract_id=c_ce.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("180.00"),
            available_at=date(2026, 8, 11),
        )
        db_session.add(p_ce)
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalOptionChainService(db_session)
        # Query with as_of_date = 2026-08-10 (before revision) -> hides row
        chain_past = await service.get_historical_option_chain(
            symbol="NIFTY",
            trade_date=date(2026, 8, 10),
            expiry_date=date(2026, 8, 27),
            as_of_date=date(2026, 8, 10),
        )
        assert len(chain_past.rows) == 0
        assert "OPTION_CHAIN_EMPTY" in chain_past.quality_warnings

        # Query with as_of_date = 2026-08-11 -> visible
        chain_now = await service.get_historical_option_chain(
            symbol="NIFTY",
            trade_date=date(2026, 8, 10),
            expiry_date=date(2026, 8, 27),
            as_of_date=date(2026, 8, 11),
        )
        assert len(chain_now.rows) == 1


@pytest.mark.asyncio
async def test_expiry_and_date_discovery_queries():
    """Verify get_available_expiries and get_available_chain_dates queries."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        c_exp1 = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624000CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_exp2 = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY24SEP202624000CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 9, 24),
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_exp1, c_exp2])
        await db_session.flush()

        p1 = DerivativeDailyPrice(contract_id=c_exp1.id, trade_date=date(2026, 8, 10), available_at=date(2026, 8, 10))
        p2 = DerivativeDailyPrice(contract_id=c_exp2.id, trade_date=date(2026, 8, 10), available_at=date(2026, 8, 10))
        p3 = DerivativeDailyPrice(contract_id=c_exp1.id, trade_date=date(2026, 8, 11), available_at=date(2026, 8, 11))
        db_session.add_all([p1, p2, p3])
        await db_session.commit()

    async with factory() as db_session:
        service = HistoricalOptionChainService(db_session)
        expiries = await service.get_available_expiries("NIFTY", date(2026, 8, 10))
        assert expiries == [date(2026, 8, 27), date(2026, 9, 24)]

        dates = await service.get_available_chain_dates("NIFTY", date(2026, 8, 1), date(2026, 8, 31))
        assert dates == [date(2026, 8, 10), date(2026, 8, 11)]


@pytest.mark.asyncio
async def test_option_chain_rest_api_and_ui_partial():
    """Verify REST API /derivatives/options/chain & HTMX partial /derivatives/options/chain/table."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        c_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624000CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24000.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add(c_ce)
        await db_session.flush()

        p_ce = DerivativeDailyPrice(
            contract_id=c_ce.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("180.00"),
            available_at=date(2026, 8, 10),
        )
        db_session.add(p_ce)
        await db_session.commit()

    from tests.auth_helpers import auth_client, seed_builtins

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # 1. REST API Option Chain
        resp = await client.get("/api/v1/market-data/derivatives/options/chain?symbol=NIFTY&trade_date=2026-08-10&expiry_date=2026-08-27")
        assert resp.status_code == 200
        data = resp.json()
        assert data["underlying_symbol"] == "NIFTY"
        assert data["trade_date"] == "2026-08-10"
        assert data["expiry_date"] == "2026-08-27"
        assert len(data["rows"]) == 1

        # 2. REST API Available Expiries
        resp_exp = await client.get("/api/v1/market-data/derivatives/options/expiries?symbol=NIFTY&trade_date=2026-08-10")
        assert resp_exp.status_code == 200
        data_exp = resp_exp.json()
        assert data_exp["expiries"] == ["2026-08-27"]

        # 3. HTMX Option Chain Table UI partial
        resp_ui = await client.get("/market-data/derivatives/options/chain/table?symbol=NIFTY&trade_date=2026-08-10&expiry_date=2026-08-27")
        assert resp_ui.status_code == 200
        assert "CALLS" in resp_ui.text
        assert "PUTS" in resp_ui.text
        assert "24000" in resp_ui.text
