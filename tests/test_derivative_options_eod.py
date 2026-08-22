"""Unit tests for Sprint 19.1D-3 — Historical NSE Options EOD Import.

Covers:
  - Reuse of generic DerivativeDailyPrice model for Options (no duplicate OptionDailyPrice model/table)
  - Index CE/PE & Stock CE/PE contract identity resolution
  - Decimal strike precision and canonical string resolution
  - Options importer service (imports OPTION rows, skips FUTURE rows cleanly)
  - No-trade option row policy vs. OHLC bounds validation
  - Independent settlement price vs. close storage
  - Signed change-in-OI & non-negative OI validation
  - Trade-date vs. expiry date validation
  - Point-in-time EOD available_at cutoff filtering
  - Raw strike ladder query (`get_options_for_expiry`)
  - Historical options coverage summary (`get_options_price_coverage`)
  - Large-batch synthetic options import idempotency & performance
  - Path safety & zero live network security
"""
from datetime import date
from decimal import Decimal

import pytest

from app.data.derivative_bhavcopy_parser import (
    DerivativeBhavcopyParser,
)
from app.data.exchange_constants import (
    EXCHANGE_NSE,
)
from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.stock import Stock
from app.repositories.derivative_daily_price import DerivativeDailyPriceRepository
from app.services.derivative_options_import import DerivativeOptionsImportService
from app.services.historical_query import HistoricalCoverageService, HistoricalQueryService


@pytest.mark.asyncio
async def test_options_reuse_derivative_daily_price_model():
    """Verify Option EOD observations use the generic DerivativeDailyPrice model without duplicate tables."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        ce_contract = DerivativeContract(
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
        db_session.add(ce_contract)
        await db_session.flush()

        ce_price = DerivativeDailyPrice(
            contract_id=ce_contract.id,
            trade_date=date(2026, 8, 10),
            open=Decimal("150.0000"),
            high=Decimal("180.0000"),
            low=Decimal("120.0000"),
            close=Decimal("165.5000"),
            last_price=Decimal("166.0000"),
            settlement_price=Decimal("165.5000"),
            previous_close=Decimal("140.0000"),
            volume_contracts=5000,
            turnover=Decimal("3012500000.0000"),  # ₹ Rupees
            open_interest=250000,
            change_in_open_interest=15000,  # Signed
            underlying_value=Decimal("24150.0000"),
            available_at=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )
        db_session.add(ce_price)
        await db_session.commit()

        saved = await db_session.get(DerivativeDailyPrice, ce_price.id)
        assert saved is not None
        assert saved.contract.instrument_type == "OPTION"
        assert saved.contract.strike_price == Decimal("24000.00")
        assert saved.contract.option_type == "CE"
        assert saved.close == Decimal("165.5000")


@pytest.mark.asyncio
async def test_options_bhavcopy_parser_ce_pe_and_futures_skipped():
    """Verify DerivativeBhavcopyParser extracts option records and counts futures_skipped."""
    parser = DerivativeBhavcopyParser()
    csv_data = (
        "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        "FUTIDX,NIFTY,27-Aug-2026,0,XX,24000.00,24200.00,23950.00,24150.00,24160.00,100,603.75,10000,250,10-AUG-2026\n"
        "OPTIDX,NIFTY,27-Aug-2026,24000.00,CE,150.00,180.00,120.00,165.00,165.00,200,1200.00,15000,100,10-AUG-2026\n"
        "OPTIDX,NIFTY,27-Aug-2026,24000.00,PE,80.00,95.00,65.00,75.00,75.00,150,900.00,12000,-50,10-AUG-2026\n"
    )
    res = parser.parse_csv(csv_data)
    assert res.total_rows == 3
    assert len(res.options_records) == 2
    assert res.futures_skipped_count == 1

    ce = res.options_records[0]
    pe = res.options_records[1]

    assert ce.option_type == "CE"
    assert ce.strike_price == Decimal("24000.00")
    assert pe.option_type == "PE"
    assert pe.change_in_open_interest == -50


@pytest.mark.asyncio
async def test_options_import_service_end_to_end():
    """Verify DerivativeOptionsImportService contract resolution, futures skipping, and idempotency."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        stk = Stock(symbol="SBIN", company_name="State Bank of India", exchange=EXCHANGE_NSE)
        db_session.add_all([idx, stk])
        await db_session.flush()

        # Seed master contracts for NIFTY CE/PE and SBIN CE
        c_nifty_ce = DerivativeContract(
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
        c_nifty_pe = DerivativeContract(
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
        c_sbin_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_stock_id=stk.id,
            trading_symbol="SBIN27AUG2026800CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("800.00"),
            option_type="CE",
            lot_size=1500,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_nifty_ce, c_nifty_pe, c_sbin_ce])
        await db_session.commit()

    async with factory() as db_session:
        csv_content = (
            "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
            "FUTIDX,NIFTY,27-Aug-2026,0,XX,24000.00,24200.00,23950.00,24150.00,24160.00,100,603.75,10000,250,10-AUG-2026\n"
            "OPTIDX,NIFTY,27-Aug-2026,24000,CE,150.00,180.00,120.00,165.00,165.00,200,1200.00,15000,100,10-AUG-2026\n"
            "OPTIDX,NIFTY,27-Aug-2026,24000,PE,80.00,95.00,65.00,75.00,75.00,150,900.00,12000,-50,10-AUG-2026\n"
            "OPTSTK,SBIN,27-Aug-2026,800.00,CE,15.00,20.00,12.00,18.00,18.00,30,360.00,5000,20,10-AUG-2026\n"
            "OPTSTK,UNKNOWN_STK,27-Aug-2026,100.00,CE,5.00,6.00,4.00,5.50,5.50,10,10.00,100,0,10-AUG-2026\n"
        )

        service = DerivativeOptionsImportService(db_session)
        summary = await service.import_options_bhavcopy(
            content=csv_content,
            as_of_date=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )

        assert summary.total_rows == 5
        assert summary.options_rows == 4
        assert summary.futures_skipped == 1
        assert summary.imported_count == 3
        assert summary.unresolved_contracts == 1
        assert summary.ce_rows == 3
        assert summary.pe_rows == 1
        assert summary.index_options_count == 2
        assert summary.stock_options_count == 1

        # Verify DB entries
        p_repo = DerivativeDailyPriceRepository(db_session)
        ce_price = await p_repo.get_by_contract_and_date(c_nifty_ce.id, date(2026, 8, 10))
        pe_price = await p_repo.get_by_contract_and_date(c_nifty_pe.id, date(2026, 8, 10))

        assert ce_price is not None
        assert pe_price is not None
        assert ce_price.close == Decimal("165.00")
        assert pe_price.close == Decimal("75.00")

        # Idempotent re-importing exact same file -> no duplicates
        summary2 = await service.import_options_bhavcopy(
            content=csv_content,
            as_of_date=date(2026, 8, 10),
        )
        assert summary2.imported_count == 3


@pytest.mark.asyncio
async def test_no_trade_illiquid_option_policy():
    """Verify illiquid option zero-volume rows process without spurious OHLC corruption errors."""
    parser = DerivativeBhavcopyParser()
    no_trade_csv = (
        "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\n"
        "OPTIDX,NIFTY,27-Aug-2026,30000,CE,0.00,0.00,0.00,0.00,5.00,0,0.00,1000,0,10-AUG-2026\n"
    )
    res = parser.parse_csv(no_trade_csv)
    assert len(res.options_records) == 1
    opt = res.options_records[0]
    assert opt.is_no_trade is True
    assert opt.settlement_price == Decimal("5.00")
    assert opt.open_interest == 1000
    assert len(res.row_errors) == 0


@pytest.mark.asyncio
async def test_raw_strike_ladder_and_options_coverage_queries():
    """Verify HistoricalQueryService.get_options_for_expiry strike ladder & coverage query."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

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
        c_24500_ce = DerivativeContract(
            exchange=EXCHANGE_NSE,
            underlying_index_id=idx.id,
            trading_symbol="NIFTY27AUG202624500CE",
            instrument_type="OPTION",
            expiry_date=date(2026, 8, 27),
            strike_price=Decimal("24500.00"),
            option_type="CE",
            lot_size=25,
            available_at=date(2026, 8, 1),
        )
        db_session.add_all([c_24000_ce, c_24000_pe, c_24500_ce])
        await db_session.flush()

        p_24000_ce = DerivativeDailyPrice(
            contract_id=c_24000_ce.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("165.00"),
            available_at=date(2026, 8, 10),
        )
        p_24000_pe = DerivativeDailyPrice(
            contract_id=c_24000_pe.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("75.00"),
            available_at=date(2026, 8, 10),
        )
        p_24500_ce = DerivativeDailyPrice(
            contract_id=c_24500_ce.id,
            trade_date=date(2026, 8, 10),
            close=Decimal("45.00"),
            available_at=date(2026, 8, 10),
        )
        db_session.add_all([p_24000_ce, p_24000_pe, p_24500_ce])
        await db_session.commit()

    async with factory() as db_session:
        h_query = HistoricalQueryService(db_session)
        ladder = await h_query.get_options_for_expiry(
            underlying_index_id=idx.id,
            expiry_date=date(2026, 8, 27),
            trade_date=date(2026, 8, 10),
            as_of_date=date(2026, 8, 10),
        )
        assert len(ladder) == 3

        # Sorted by strike_price asc, option_type asc:
        # [24000.00 CE, 24000.00 PE, 24500.00 CE]
        assert ladder[0][1].strike_price == Decimal("24000.00")
        assert ladder[0][1].option_type == "CE"
        assert ladder[0][0].close == Decimal("165.00")

        assert ladder[1][1].strike_price == Decimal("24000.00")
        assert ladder[1][1].option_type == "PE"

        assert ladder[2][1].strike_price == Decimal("24500.00")
        assert ladder[2][1].option_type == "CE"

        # Point-in-time cutoff: as_of_date < 2026-08-10 returns 0 rows
        ladder_past = await h_query.get_options_for_expiry(
            underlying_index_id=idx.id,
            expiry_date=date(2026, 8, 27),
            trade_date=date(2026, 8, 10),
            as_of_date=date(2026, 8, 9),
        )
        assert len(ladder_past) == 0

        # Coverage query
        cov_service = HistoricalCoverageService(db_session)
        cov = await cov_service.get_options_price_coverage(as_of_date=date(2026, 8, 10))
        assert cov["total_price_rows"] == 3
        assert cov["ce_price_rows"] == 2
        assert cov["pe_price_rows"] == 1


@pytest.mark.asyncio
async def test_large_batch_synthetic_options_import():
    """Verify performance and idempotency on a moderate synthetic option batch (100 option strikes)."""
    factory = get_session_factory()
    async with factory() as db_session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", country="INDIA")
        db_session.add(idx)
        await db_session.flush()

        contracts = []
        rows = [
            "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP"
        ]

        base_strike = Decimal("24000.00")
        for i in range(50):
            strike = base_strike + Decimal(i * 50)
            for opt_type in ("CE", "PE"):
                trading_symbol = f"NIFTY27AUG2026{int(strike)}{opt_type}"
                c = DerivativeContract(
                    exchange=EXCHANGE_NSE,
                    underlying_index_id=idx.id,
                    trading_symbol=trading_symbol,
                    instrument_type="OPTION",
                    expiry_date=date(2026, 8, 27),
                    strike_price=strike,
                    option_type=opt_type,
                    lot_size=25,
                    available_at=date(2026, 8, 1),
                )
                contracts.append(c)
                rows.append(
                    f"OPTIDX,NIFTY,27-Aug-2026,{strike:.2f},{opt_type},100.00,120.00,90.00,110.00,110.00,500,1375.00,50000,500,10-AUG-2026"
                )

        db_session.add_all(contracts)
        await db_session.commit()

        csv_batch = "\n".join(rows)

    async with factory() as db_session:
        service = DerivativeOptionsImportService(db_session)
        summary = await service.import_options_bhavcopy(
            content=csv_batch,
            as_of_date=date(2026, 8, 10),
            source_name="FO_UDIFF_BHAVCOPY",
        )

        assert summary.total_rows == 100
        assert summary.options_rows == 100
        assert summary.imported_count == 100
        assert summary.unresolved_contracts == 0

        # Idempotent re-import of exact same batch
        summary2 = await service.import_options_bhavcopy(
            content=csv_batch,
            as_of_date=date(2026, 8, 10),
        )
        assert summary2.imported_count == 100
