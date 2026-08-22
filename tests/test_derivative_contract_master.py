"""Tests for Derivative Contract Master + Source Registry — Sprint 19.1D-1.

Validates:
  - DerivativeContract model invariants (Futures vs Options, stock vs index underlying)
  - Database CheckConstraints and UniqueConstraint (idempotency_hash)
  - Parser robust format detection, Decimal conversion, and row error reporting
  - Import service underlying resolution and DataQualityIssue creation
  - Point-in-time available_at filtering
  - Source registry configuration and access classification metadata
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.data.derivative_parser import (
    DerivativeContractParser,
    DerivativeParserError,
)
from app.data.derivative_source_registry import (
    ACCESS_LOCAL_VALIDATED_FILE,
    ACCESS_OFFICIAL_PUBLIC,
    get_dataset_metadata,
    list_registered_datasets,
)
from app.data.exchange_constants import (
    DQ_DERIVATIVE_INVALID_EXPIRY,
    DQ_DERIVATIVE_INVALID_LOT_SIZE,
    DQ_DERIVATIVE_INVALID_OPTION_TYPE,
    DQ_DERIVATIVE_INVALID_STRIKE,
    DQ_DERIVATIVE_UNKNOWN_UNDERLYING,
)
from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract, DerivativeLotSizeHistory
from app.models.market_index import MarketIndex
from app.models.stock import Stock
from app.repositories.derivative import DerivativeContractRepository
from app.services.derivative_import import DerivativeContractImportService
from app.services.historical_query import HistoricalCoverageService, HistoricalQueryService


@pytest.mark.asyncio
async def test_source_registry_metadata() -> None:
    """Verify source registry exposes valid metadata for required datasets."""
    datasets = list_registered_datasets()
    assert len(datasets) >= 10

    contract_master = get_dataset_metadata("FO_CONTRACT_MASTER")
    assert contract_master is not None
    assert contract_master.official_owner == "NSE India"
    assert contract_master.access_classification == ACCESS_LOCAL_VALIDATED_FILE

    bhavcopy = get_dataset_metadata("FO_UDIFF_BHAVCOPY")
    assert bhavcopy is not None
    assert bhavcopy.access_classification == ACCESS_OFFICIAL_PUBLIC


@pytest.mark.asyncio
async def test_stock_future_valid_and_invariants() -> None:
    """Stock Future contract persists cleanly with valid invariants."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="FUTSTOCK", company_name="Fut Stock", exchange="NSE", lifecycle_status="active")
        session.add(stock)
        await session.flush()

        fut = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_stock_id=stock.id,
            trading_symbol="FUTSTOCK26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=250,
            available_at=date(2026, 3, 1),
        )
        repo = DerivativeContractRepository(session)
        saved = await repo.add(fut)
        await session.commit()

        assert saved.id is not None
        assert saved.strike_price is None
        assert saved.option_type is None
        assert saved.lot_size == 250
        assert saved.idempotency_hash is not None


@pytest.mark.asyncio
async def test_index_future_valid() -> None:
    """Index Future contract persists with MarketIndex underlying."""
    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY_FUT_TEST", country="INDIA", name="Nifty Fut Test")
        session.add(idx)
        await session.flush()

        fut = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=50,
            available_at=date(2026, 3, 1),
        )
        repo = DerivativeContractRepository(session)
        saved = await repo.add(fut)
        await session.commit()

        assert saved.id is not None
        assert saved.underlying_index_id == idx.id
        assert saved.underlying_stock_id is None


@pytest.mark.asyncio
async def test_option_contract_ce_pe_valid() -> None:
    """Option contracts (CE and PE) persist cleanly with strike price."""
    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="BANKNIFTY_OPT_TEST", country="INDIA", name="Bank Nifty Opt Test")
        session.add(idx)
        await session.flush()

        ce = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="BANKNIFTY26MAR48000CE",
            expiry_date=date(2026, 3, 26),
            strike_price=Decimal("48000.00"),
            option_type="CE",
            lot_size=15,
            available_at=date(2026, 3, 1),
        )
        pe = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="BANKNIFTY26MAR48000PE",
            expiry_date=date(2026, 3, 26),
            strike_price=Decimal("48000.00"),
            option_type="PE",
            lot_size=15,
            available_at=date(2026, 3, 1),
        )
        repo = DerivativeContractRepository(session)
        saved_ce = await repo.add(ce)
        saved_pe = await repo.add(pe)
        await session.commit()

        assert saved_ce.id != saved_pe.id
        assert saved_ce.option_type == "CE"
        assert saved_pe.option_type == "PE"


@pytest.mark.asyncio
async def test_underlying_exactly_one_constraint() -> None:
    """DB check constraint rejects contracts with both or neither underlyings."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="BOTHCO", company_name="Both Co", exchange="NSE", lifecycle_status="active")
        idx = MarketIndex(code="BOTH_IDX", country="INDIA", name="Both Idx")
        session.add(stock)
        session.add(idx)
        await session.flush()

        # Both underlyings set
        c_both = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_stock_id=stock.id,
            underlying_index_id=idx.id,
            trading_symbol="BOTH26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=100,
            available_at=date(2026, 3, 1),
        )
        session.add(c_both)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

    async with factory() as session:
        # Neither underlying set
        c_none = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            trading_symbol="NONE26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=100,
            available_at=date(2026, 3, 1),
        )
        session.add(c_none)
        with pytest.raises(IntegrityError):
            await session.flush()


@pytest.mark.asyncio
async def test_idempotency_deduplication() -> None:
    """Re-adding identical contract returns existing contract without duplicating."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="IDEMPCO", company_name="Idemp Co", exchange="NSE", lifecycle_status="active")
        session.add(stock)
        await session.flush()

        c1 = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_stock_id=stock.id,
            trading_symbol="IDEMPCO26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=500,
            available_at=date(2026, 3, 1),
        )
        c2 = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_stock_id=stock.id,
            trading_symbol="IDEMPCO26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=500,
            available_at=date(2026, 3, 1),
        )

        repo = DerivativeContractRepository(session)
        res1 = await repo.add(c1)
        await session.commit()

        res2 = await repo.add(c2)
        assert res1.id == res2.id

        contracts = await repo.list_contracts(underlying_stock_id=stock.id)
        assert len(contracts) == 1


@pytest.mark.asyncio
async def test_point_in_time_available_at_gate() -> None:
    """Contracts published after as_of_date are invisible to queries."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="PITDERIV", company_name="PIT Deriv", exchange="NSE", lifecycle_status="active")
        session.add(stock)
        await session.flush()

        c = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_stock_id=stock.id,
            trading_symbol="PITDERIV26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=100,
            available_at=date(2026, 3, 15),
        )
        repo = DerivativeContractRepository(session)
        await repo.add(c)
        await session.commit()

        # Query before available_at -> empty
        early = await repo.list_contracts(underlying_stock_id=stock.id, as_of_date=date(2026, 3, 10))
        assert len(early) == 0

        # Query on/after available_at -> visible
        after = await repo.list_contracts(underlying_stock_id=stock.id, as_of_date=date(2026, 3, 15))
        assert len(after) == 1


@pytest.mark.asyncio
async def test_lot_size_history_query() -> None:
    """Historical lot size changes are preserved and queryable as of date T."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="LOTCO", company_name="Lot Co", exchange="NSE", lifecycle_status="active")
        session.add(stock)
        await session.flush()

        h1 = DerivativeLotSizeHistory(
            underlying_stock_id=stock.id,
            lot_size=1000,
            effective_from=date(2022, 1, 1),
            effective_to=date(2024, 12, 31),
            available_at=date(2022, 1, 1),
        )
        h2 = DerivativeLotSizeHistory(
            underlying_stock_id=stock.id,
            lot_size=500,
            effective_from=date(2025, 1, 1),
            effective_to=None,
            available_at=date(2025, 1, 1),
        )
        repo = DerivativeContractRepository(session)
        await repo.add_lot_size_history(h1)
        await repo.add_lot_size_history(h2)
        await session.commit()

        size_2023 = await repo.get_lot_size_as_of(underlying_stock_id=stock.id, as_of_date=date(2023, 6, 1))
        size_2025 = await repo.get_lot_size_as_of(underlying_stock_id=stock.id, as_of_date=date(2025, 3, 1))

        assert size_2023 == 1000
        assert size_2025 == 500


def test_parser_standard_and_udiff_formats() -> None:
    """Parser auto-detects headers and parses contracts cleanly."""
    parser = DerivativeContractParser()

    # Standard format CSV
    csv_standard = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,MARKET_LOT,TOKEN
FUTSTK,RELIANCE,26-Mar-2026,0.00,XX,250,12345
OPTSTK,RELIANCE,26-Mar-2026,3000.00,CE,250,12346
"""
    res1 = parser.parse_csv(csv_standard, as_of_date=date(2026, 3, 1))
    assert res1.total_rows == 2
    assert len(res1.contracts) == 2
    assert len(res1.row_errors) == 0

    fut = res1.contracts[0]
    assert fut.instrument_type == "FUTURE"
    assert fut.strike_price is None
    assert fut.option_type is None
    assert fut.lot_size == 250

    opt = res1.contracts[1]
    assert opt.instrument_type == "OPTION"
    assert opt.strike_price == Decimal("3000.00")
    assert opt.option_type == "CE"

    official_udiff = """TradDt,FinInstrmTp,FinInstrmId,TckrSymb,XpryDt,StrkPric,OptnTp,FinInstrmNm,NewBrdLotQty
2026-06-01,STF,62333,ABCAPITAL,2026-06-30,,,ABCAPITAL26JUNFUT,3100
"""
    udiff_result = parser.parse_csv(official_udiff)

    assert len(udiff_result.contracts) == 1
    assert udiff_result.contracts[0].trading_symbol == "ABCAPITAL26JUNFUT"
    assert udiff_result.contracts[0].lot_size == 3100
    assert udiff_result.contracts[0].available_at == date(2026, 6, 1)


def test_parser_malformed_rows_and_errors() -> None:
    """Parser captures per-row invalid data into row_errors without throwing."""
    parser = DerivativeContractParser()

    csv_bad = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,MARKET_LOT
FUTSTK,TATA,INVALID_DATE,0,XX,100
OPTSTK,TATA,26-Mar-2026,-50,CE,100
OPTSTK,TATA,26-Mar-2026,500,INVALID_OPT,100
FUTSTK,TATA,26-Mar-2026,0,XX,-10
"""
    res = parser.parse_csv(csv_bad, as_of_date=date(2026, 3, 1))
    assert res.total_rows == 4
    assert len(res.contracts) == 0
    assert len(res.row_errors) == 4

    codes = [e.issue_code for e in res.row_errors]
    assert DQ_DERIVATIVE_INVALID_EXPIRY in codes
    assert DQ_DERIVATIVE_INVALID_STRIKE in codes
    assert DQ_DERIVATIVE_INVALID_OPTION_TYPE in codes
    assert DQ_DERIVATIVE_INVALID_LOT_SIZE in codes


def test_parser_unsupported_header_throws() -> None:
    """Parser raises DerivativeParserError on invalid header."""
    parser = DerivativeContractParser()
    csv_header = "COL_A,COL_B,COL_C\n1,2,3\n"
    with pytest.raises(DerivativeParserError):
        parser.parse_csv(csv_header)


@pytest.mark.asyncio
async def test_importer_service_underlying_resolution() -> None:
    """Importer service resolves MarketIndex vs Stock and logs DataQualityIssue for unresolved."""
    factory = get_session_factory()
    async with factory() as session:
        # Create MarketIndex NIFTY and Stock SBIN
        idx = MarketIndex(code="NIFTY", country="INDIA", name="Nifty 50")
        stock = Stock(symbol="SBIN", company_name="State Bank", exchange="NSE", lifecycle_status="active")
        session.add(idx)
        session.add(stock)
        await session.commit()

        csv_content = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,MARKET_LOT
FUTIDX,NIFTY,26-Mar-2026,0.00,XX,50
FUTSTK,SBIN,26-Mar-2026,0.00,XX,1500
FUTSTK,UNKNOWN_XYZ,26-Mar-2026,0.00,XX,100
"""
        service = DerivativeContractImportService(session)
        summary = await service.import_contract_master_file(
            content=csv_content,
            as_of_date=date(2026, 3, 1),
            source_reference="test_file.csv",
        )
        await session.commit()

        assert summary.total_rows == 3
        assert summary.imported_count == 2
        assert summary.unresolved_underlying_count == 1
        assert summary.quality_issues_count == 1

        # Check contracts in DB
        h_service = HistoricalQueryService(session)
        nifty_contracts = await h_service.get_derivative_contracts(underlying_index_id=idx.id)
        sbin_contracts = await h_service.get_derivative_contracts(underlying_stock_id=stock.id)

        assert len(nifty_contracts) == 1
        assert len(sbin_contracts) == 1

        # Check DataQualityIssue created
        issues = await h_service.get_data_quality_issues(exchange="NSE")
        unresolved_issues = [i for i in issues if i.issue_code == DQ_DERIVATIVE_UNKNOWN_UNDERLYING]
        assert len(unresolved_issues) == 1
        assert unresolved_issues[0].symbol == "UNKNOWN_XYZ"


@pytest.mark.asyncio
async def test_historical_coverage_service_derivatives() -> None:
    """HistoricalCoverageService returns derivative contract coverage metrics."""
    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY_COV", country="INDIA", name="Nifty Cov")
        session.add(idx)
        await session.flush()

        fut = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_index_id=idx.id,
            trading_symbol="NIFTYCOV26MARFUT",
            expiry_date=date(2026, 3, 26),
            lot_size=50,
            available_at=date(2026, 3, 1),
        )
        repo = DerivativeContractRepository(session)
        await repo.add(fut)
        await session.commit()

        cov_service = HistoricalCoverageService(session)
        cov = await cov_service.get_derivative_contract_coverage(as_of_date=date(2026, 3, 10))

        assert cov["total_contracts"] >= 1
        assert cov["futures_count"] >= 1
