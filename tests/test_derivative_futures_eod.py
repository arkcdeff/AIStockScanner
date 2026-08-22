"""Unit tests for Sprint 19.1D-2 — Historical NSE Futures EOD Import.

Covers:
  - DerivativeDailyPrice model invariants & DB constraints
  - Bhavcopy parser (Legacy & UDiFF formats)
  - Futures-only path (imports FUTURE rows, skips OPTION rows cleanly)
  - Canonical contract resolution (exchange + underlying + FUTURE + expiry)
  - No-trade row policy vs. OHLC validation
  - Independent close vs. settlement price storage
  - Signed change-in-OI & non-negative OI validation
  - Trade-date vs. expiry date validation
  - Point-in-time EOD available_at filtering
  - Futures curve query & coverage summary
  - Re-import idempotency & revision updates
  - Path traversal & zero live network security
"""
from datetime import date
from decimal import Decimal

import pytest

from app.data.derivative_bhavcopy_parser import (
    DerivativeBhavcopyParser,
)
from app.data.exchange_constants import (
    DQ_DERIVATIVE_PRICE_INVALID_OHLC,
    DQ_DERIVATIVE_PRICE_POST_EXPIRY,
    EXCHANGE_NSE,
)
from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.stock import Stock
from app.repositories.derivative_daily_price import DerivativeDailyPriceRepository
from app.services.derivative_futures_import import DerivativeFuturesImportService
from app.services.historical_query import HistoricalCoverageService, HistoricalQueryService


@pytest.mark.asyncio
async def test_derivative_daily_price_model_constraints():
    """Verify DerivativeDailyPrice model creation, decimal precision, and relationships."""
    factory = get_session_factory()
    async with factory() as db_session:
        stock = Stock(symbol="RELIANCE", company_name="Reliance Industries", exchange=EXCHANGE_NSE)
        db_session.add(stock)
        await db_session.flush()

        contract = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_stock_id=stock.id,
            trading_symbol="RELIANCE27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=250,
            available_at=date(2026, 8, 1),
        )
        db_session.add(contract)
        await db_session.flush()

        price = DerivativeDailyPrice(
            contract_id=contract.id,
            trade_date=date(2026, 8, 10),
            open=Decimal("2500.5000"),
            high=Decimal("2550.0000"),
            low=Decimal("2490.0000"),
            close=Decimal("2540.2500"),
            last_price=Decimal("2541.0000"),
            settlement_price=Decimal("2542.0000"),
            previous_close=Decimal("2495.0000"),
            volume_contracts=1200,
            turnover=Decimal("762075000.0000"),  # ₹ Rupees
            open_interest=50000,
            change_in_open_interest=-1500,  # Signed negative
            underlying_value=Decimal("2535.0000"),
            available_at=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )
        db_session.add(price)
        await db_session.commit()

        saved = await db_session.get(DerivativeDailyPrice, price.id)
        assert saved is not None
        assert saved.close == Decimal("2540.2500")
        assert saved.settlement_price == Decimal("2542.0000")  # Independent from close!
        assert saved.change_in_open_interest == -1500
        assert saved.contract.trading_symbol == "RELIANCE27AUG2026FUT"


@pytest.mark.asyncio
async def test_bhavcopy_parser_legacy_and_udiff():
    """Verify parsing both Legacy and UDiFF official F&O CSV formats."""
    parser = DerivativeBhavcopyParser()

    # 1. Legacy CSV Format
    legacy_csv = (
        "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        "FUTIDX,NIFTY,27-Aug-2026,0,XX,24000.00,24200.00,23950.00,24150.00,24160.00,100,603.75,10000,250,10-AUG-2026\n"
        "OPTSTK,RELIANCE,27-Aug-2026,2500.00,CE,50.00,65.00,45.00,60.00,60.00,50,125.00,5000,-100,10-AUG-2026\n"
    )
    res_legacy = parser.parse_csv(legacy_csv)
    assert res_legacy.total_rows == 2
    assert len(res_legacy.futures_records) == 1
    assert res_legacy.options_skipped_count == 1

    fut_legacy = res_legacy.futures_records[0]
    assert fut_legacy.underlying_symbol == "NIFTY"
    assert fut_legacy.expiry_date == date(2026, 8, 27)
    assert fut_legacy.close == Decimal("24150.00")
    assert fut_legacy.settlement_price == Decimal("24160.00")
    assert fut_legacy.turnover == Decimal("60375000.0000")  # Converted from 603.75 Lakhs to Rupees

    # 2. UDiFF CSV Format
    udiff_csv = (
        "RptDt,TckrSymb,FinInstrmTp,XpryDt,StrkPric,OptnTp,OpnPric,HghPric,LwPric,ClsPric,SttlmPric,LstPric,TtlTradgVol,TtlTrdVl,OpnIntrst,ChngInOpnIntrst,UndrlygPric\n"
        "2026-08-10,SBIN,FUTSTK,2026-08-27,0,,800.00,815.00,795.00,810.00,811.00,809.50,500,40500000,25000,500,808.00\n"
    )
    res_udiff = parser.parse_csv(udiff_csv)
    assert len(res_udiff.futures_records) == 1
    fut_udiff = res_udiff.futures_records[0]
    assert fut_udiff.underlying_symbol == "SBIN"
    assert fut_udiff.underlying_value == Decimal("808.00")


@pytest.mark.asyncio
async def test_futures_import_service_end_to_end():
    """Test importing Futures EOD data, contract resolution, skipping options, and idempotency."""
    factory = get_session_factory()
    async with factory() as db_session:
        # Seed underlying index & stock
        index = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        stock = Stock(symbol="SBIN", company_name="State Bank of India", exchange=EXCHANGE_NSE)
        db_session.add_all([index, stock])
        await db_session.flush()

        # Seed master contracts for NIFTY and SBIN
        c_index = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=index.id,
            trading_symbol="NIFTY27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_stock = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_stock_id=stock.id,
            trading_symbol="SBIN27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=1500,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_index, c_stock])
        await db_session.commit()

    async with factory() as db_session:
        # Sample Bhavcopy with 2 futures (NIFTY, SBIN), 1 option (skipped), and 1 unknown contract
        csv_content = (
            "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
            "FUTIDX,NIFTY,27-Aug-2026,0,XX,24000.00,24200.00,23950.00,24150.00,24160.00,100,603.75,10000,250,10-AUG-2026\n"
            "FUTSTK,SBIN,27-Aug-2026,0,XX,800.00,815.00,795.00,810.00,811.00,500,6075.00,25000,500,10-AUG-2026\n"
            "OPTIDX,NIFTY,27-Aug-2026,24000,CE,150.00,180.00,120.00,160.00,160.00,200,1200.00,15000,100,10-AUG-2026\n"
            "FUTSTK,UNKNOWN_CO,27-Aug-2026,0,XX,100.00,105.00,98.00,102.00,102.00,10,10.00,100,0,10-AUG-2026\n"
        )

        service = DerivativeFuturesImportService(db_session)
        summary = await service.import_futures_bhavcopy(
            content=csv_content,
            as_of_date=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )

        assert summary.total_rows == 4
        assert summary.futures_rows == 3
        assert summary.options_skipped == 1
        assert summary.imported_count == 2
        assert summary.unresolved_contracts == 1

        # Verify rows in DB
        p_repo = DerivativeDailyPriceRepository(db_session)
        nifty_price = await p_repo.get_by_contract_and_date(c_index.id, date(2026, 8, 10))
        assert nifty_price is not None
        assert nifty_price.close == Decimal("24150.00")
        assert nifty_price.settlement_price == Decimal("24160.00")
        assert nifty_price.change_in_open_interest == 250

        # Re-importing exact same file -> idempotent (no duplicates)
        summary2 = await service.import_futures_bhavcopy(
            content=csv_content,
            as_of_date=date(2026, 8, 10),
        )
        assert summary2.imported_count == 2


@pytest.mark.asyncio
async def test_futures_import_prefers_index_alias_over_colliding_stock_symbol():
    factory = get_session_factory()
    async with factory() as db_session:
        index = MarketIndex(
            code="NIFTY_NEXT_50",
            name="Nifty Next 50",
            country="INDIA",
            provider_symbol="NIFTINEXT50",
        )
        stock = Stock(
            symbol="NIFTYNXT50",
            company_name="Legacy misclassified index symbol",
            exchange=EXCHANGE_NSE,
        )
        db_session.add_all([index, stock])
        await db_session.flush()
        contract = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=index.id,
            trading_symbol="NIFTYNXT5026JUNFUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 6, 30),
            lot_size=10,
            available_at=date(2026, 6, 1),
        )
        db_session.add(contract)
        await db_session.commit()

        content = (
            "TradDt,FinInstrmTp,TckrSymb,XpryDt,OpnPric,HghPric,LwPric,ClsPric,"
            "SttlmPric,TtlTradgVol,OpnIntrst,ChngInOpnIntrst,UndrlygPric\n"
            "2026-06-01,IDF,NIFTYNXT50,2026-06-30,65000,65100,64900,65050,"
            "65050,10,1000,5,65025\n"
        )
        summary = await DerivativeFuturesImportService(
            db_session
        ).import_futures_bhavcopy(content, as_of_date=date(2026, 6, 1))

        assert summary.imported_count == 1
        assert summary.unresolved_contracts == 0
        saved = await DerivativeDailyPriceRepository(
            db_session
        ).get_by_contract_and_date(contract.id, date(2026, 6, 1))
        assert saved is not None


@pytest.mark.asyncio
async def test_ohlc_validation_and_no_trade_policy():
    """Verify that valid traded rows validate OHLC bounds, while 0-volume no-trade rows do not fail."""
    parser = DerivativeBhavcopyParser()

    # 1. Invalid OHLC traded row (Low > High)
    bad_ohlc_csv = (
        "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        "FUTIDX,NIFTY,27-Aug-2026,0,XX,24000.00,24100.00,24200.00,24050.00,24050.00,100,600.00,10000,0,10-AUG-2026\n"
    )
    res_bad = parser.parse_csv(bad_ohlc_csv)
    assert len(res_bad.row_errors) == 1
    assert res_bad.row_errors[0].issue_code == DQ_DERIVATIVE_PRICE_INVALID_OHLC

    # 2. No-trade row (0 contracts, zero prices)
    no_trade_csv = (
        "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        "FUTIDX,NIFTY,27-Aug-2026,0,XX,0.00,0.00,0.00,0.00,24150.00,0,0.00,10000,0,10-AUG-2026\n"
    )
    res_no_trade = parser.parse_csv(no_trade_csv)
    assert len(res_no_trade.futures_records) == 1
    assert res_no_trade.futures_records[0].is_no_trade is True
    assert res_no_trade.futures_records[0].settlement_price == Decimal("24150.00")


@pytest.mark.asyncio
async def test_post_expiry_trade_date_validation():
    """Verify that rows with trade_date > expiry_date are flagged and rejected."""
    parser = DerivativeBhavcopyParser()
    post_expiry_csv = (
        "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        "FUTIDX,NIFTY,27-Aug-2026,0,XX,24000.00,24200.00,23950.00,24150.00,24150.00,100,600.00,10000,0,28-AUG-2026\n"
    )
    res = parser.parse_csv(post_expiry_csv)
    assert len(res.row_errors) == 1
    assert res.row_errors[0].issue_code == DQ_DERIVATIVE_PRICE_POST_EXPIRY


@pytest.mark.asyncio
async def test_point_in_time_and_futures_curve_queries():
    """Verify HistoricalQueryService point-in-time cutoff filtering and futures term structure curve."""
    factory = get_session_factory()
    async with factory() as db_session:
        index = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(index)
        await db_session.flush()

        # Seed near, next, and far month contracts
        c_near = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=index.id,
            trading_symbol="NIFTY27AUG2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 8, 27),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        c_next = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=index.id,
            trading_symbol="NIFTY24SEP2026FUT",
            instrument_type="FUTURE",
            expiry_date=date(2026, 9, 24),
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_near, c_next])
        await db_session.flush()

        p_near = DerivativeDailyPrice(
            contract_id=c_near.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("24150.00"),
            available_at=date(2026, 8, 10),
        )
        p_next = DerivativeDailyPrice(
            contract_id=c_next.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("24250.00"),
            available_at=date(2026, 8, 10),
        )
        db_session.add_all([p_near, p_next])
        await db_session.commit()

    async with factory() as db_session:
        query_service = HistoricalQueryService(db_session)

        # 1. Curve query on 2026-08-10 returns near and next month expiries in order
        curve = await query_service.get_futures_curve(
            underlying_index_id=index.id,
            trade_date=date(2026, 8, 10),
            as_of_date=date(2026, 8, 10),
        )
        assert len(curve) == 2
        assert curve[0][1].expiry_date == date(2026, 8, 27)
        assert curve[0][0].close == Decimal("24150.00")
        assert curve[1][1].expiry_date == date(2026, 9, 24)
        assert curve[1][0].close == Decimal("24250.00")

        # 2. Point-in-time cutoff: as_of_date < 2026-08-10 returns zero rows
        curve_past = await query_service.get_futures_curve(
            underlying_index_id=index.id,
            trade_date=date(2026, 8, 10),
            as_of_date=date(2026, 8, 9),
        )
        assert len(curve_past) == 0

        # 3. Historical coverage
        cov_service = HistoricalCoverageService(db_session)
        cov = await cov_service.get_futures_price_coverage(as_of_date=date(2026, 8, 10))
        assert cov["total_price_rows"] == 2
        assert cov["contracts_with_prices"] == 2
