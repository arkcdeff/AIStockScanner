"""Regression tests for intra-file derivative deduplication & LAST_OCCURRENCE_WINS policy — Sprint 19.1J-R4."""
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.stock import Stock
from app.services.bulk_nse_ingestion_service import BulkNSEIngestionService


def _build_fo_csv(rows: list[dict[str, str]]) -> bytes:
    """Helper to build NSE FO CSV bytes from row dicts matching BulkNSEIngestionService parser."""
    headers = [
        "SYMBOL", "INSTRUMENT", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP",
        "OPEN", "HIGH", "LOW", "CLOSE", "SETTLE_PR",
        "OPEN_INT", "CHG_IN_OI", "CONTRACTS", "TIMESTAMP"
    ]
    lines = [",".join(headers)]
    for r in rows:
        line = ",".join([r.get(h, "0") for h in headers])
        lines.append(line)
    return "\n".join(lines).encode("utf-8")


@pytest.mark.asyncio
async def test_same_value_intra_file_duplicate() -> None:
    """Verify multiple source rows with identical values produce 1 DB row and log duplicate metrics."""
    factory = get_session_factory()
    async with factory() as session:
        # Create underlying stock
        stock = Stock(symbol="TESTDEDUP1", company_name="Test Dedup 1", is_active=True)
        session.add(stock)
        await session.flush()

        service = BulkNSEIngestionService(session)
        await service._init_caches()

        row1 = {
            "SYMBOL": "TESTDEDUP1", "INSTRUMENT": "FUTSTK", "EXPIRY_DT": "2026-08-27",
            "STRIKE_PR": "0", "OPTION_TYP": "-", "OPEN": "100.0", "HIGH": "110.0",
            "LOW": "95.0", "CLOSE": "105.0", "SETTLE_PR": "105.0",
            "OPEN_INT": "5000", "CHG_IN_OI": "100", "CONTRACTS": "200",
            "TIMESTAMP": "2026-08-10"
        }
        # Duplicate row with identical values
        row2 = dict(row1)

        csv_bytes = _build_fo_csv([row1, row2])
        fut_c, opt_c, ddp_c, dup_c, id_dup, conf_dup = await service._ingest_derivative_csv(csv_bytes, "test_file_nsefo.csv")
        await session.commit()

        assert ddp_c == 1  # 1 unique row written
        assert dup_c == 1  # 1 intra-file duplicate recorded
        assert id_dup == 1
        assert conf_dup == 0

        # Verify DB contents
        res = await session.execute(select(DerivativeDailyPrice).join(DerivativeContract).where(DerivativeContract.trading_symbol.like("TESTDEDUP1%")))
        prices = res.scalars().all()
        assert len(prices) == 1
        assert prices[0].close == Decimal("105.0000")


@pytest.mark.asyncio
async def test_conflicting_intra_file_duplicate_last_occurrence_wins() -> None:
    """Verify conflicting intra-file duplicates follow deterministic LAST_OCCURRENCE_WINS policy."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="TESTDEDUP2", company_name="Test Dedup 2", is_active=True)
        session.add(stock)
        await session.flush()

        service = BulkNSEIngestionService(session)
        await service._init_caches()

        row_first = {
            "SYMBOL": "TESTDEDUP2", "INSTRUMENT": "FUTSTK", "EXPIRY_DT": "2026-08-27",
            "STRIKE_PR": "0", "OPTION_TYP": "-", "OPEN": "100.0", "HIGH": "110.0",
            "LOW": "95.0", "CLOSE": "100.0", "SETTLE_PR": "100.0",
            "OPEN_INT": "5000", "CHG_IN_OI": "100", "CONTRACTS": "200",
            "TIMESTAMP": "2026-08-10"
        }
        # Conflicting second row with updated close price (150.0) and OI (9000)
        row_last = dict(row_first)
        row_last["CLOSE"] = "150.0"
        row_last["OPEN_INT"] = "9000"

        csv_bytes = _build_fo_csv([row_first, row_last])
        fut_c, opt_c, ddp_c, dup_c, id_dup, conf_dup = await service._ingest_derivative_csv(csv_bytes, "test_file2_nsefo.csv")
        await session.commit()

        assert ddp_c == 1
        assert dup_c == 1
        assert id_dup == 0
        assert conf_dup == 1

        # Verify LAST OCCURRENCE (row_last) won
        res = await session.execute(select(DerivativeDailyPrice).join(DerivativeContract).where(DerivativeContract.trading_symbol.like("TESTDEDUP2%")))
        price = res.scalar_one()
        assert price.close == Decimal("150.0000")
        assert price.open_interest == 9000


@pytest.mark.asyncio
async def test_chunk_boundary_straddling_deduplication() -> None:
    """Verify deduplication works seamlessly across chunk boundaries (> 500 records batch)."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="TESTDEDUP3", company_name="Test Dedup 3", is_active=True)
        session.add(stock)
        await session.flush()

        service = BulkNSEIngestionService(session)
        await service._init_caches()

        # Build 501 rows: 500 unique options contracts + 1 duplicate of contract #1 at index 500 (straddling chunk boundary)
        rows = []
        for i in range(500):
            rows.append({
                "SYMBOL": "TESTDEDUP3", "INSTRUMENT": "OPTSTK", "EXPIRY_DT": "2026-08-27",
                "STRIKE_PR": str(1000 + i * 10), "OPTION_TYP": "CE", "OPEN": "10.0", "HIGH": "15.0",
                "LOW": "5.0", "CLOSE": "12.0", "SETTLE_PR": "12.0",
                "OPEN_INT": "1000", "CHG_IN_OI": "50", "CONTRACTS": "100",
                "TIMESTAMP": "2026-08-10"
            })
        
        # Duplicate of index 0 straddling chunk boundary (501st row) with updated close price 25.0
        dup_straddle = dict(rows[0])
        dup_straddle["CLOSE"] = "25.0"
        rows.append(dup_straddle)

        csv_bytes = _build_fo_csv(rows)
        fut_c, opt_c, ddp_c, dup_c, id_dup, conf_dup = await service._ingest_derivative_csv(csv_bytes, "test_file3_nsefo.csv")
        await session.commit()

        assert ddp_c == 500  # Exactly 500 unique contracts written
        assert dup_c == 1    # 1 duplicate detected across chunk boundary
        assert conf_dup == 1

        # Check straddled contract value
        res = await session.execute(
            select(DerivativeDailyPrice)
            .join(DerivativeContract)
            .where(DerivativeContract.trading_symbol == "TESTDEDUP327AUG20261000CE")
        )
        price = res.scalar_one()
        assert price.close == Decimal("25.0000")  # Straddled last row won


@pytest.mark.asyncio
async def test_postgresql_no_extra_updates_on_deduped_reimport() -> None:
    """Verify deduplicated re-import produces 0 n_tup_upd and 0 dead tuple rewrites in PostgreSQL."""
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(symbol="TESTDEDUP4", company_name="Test Dedup 4", is_active=True)
        session.add(stock)
        await session.flush()

        service = BulkNSEIngestionService(session)
        await service._init_caches()

        row1 = {
            "SYMBOL": "TESTDEDUP4", "INSTRUMENT": "FUTSTK", "EXPIRY_DT": "2026-08-27",
            "STRIKE_PR": "0", "OPTION_TYP": "-", "OPEN": "200.0", "HIGH": "210.0",
            "LOW": "195.0", "CLOSE": "205.0", "SETTLE_PR": "205.0",
            "OPEN_INT": "8000", "CHG_IN_OI": "200", "CONTRACTS": "400",
            "TIMESTAMP": "2026-08-10"
        }
        row2 = dict(row1)
        row2["CLOSE"] = "208.0"  # conflicting duplicate in same file

        csv_bytes = _build_fo_csv([row1, row2])

        # Initial import
        await service._ingest_derivative_csv(csv_bytes, "test_file4_nsefo.csv")
        await session.commit()
        await session.execute(text("ANALYZE derivative_daily_prices;"))
        await session.commit()

        upd_before = (await session.execute(text("SELECT n_tup_upd FROM pg_stat_user_tables WHERE relname = 'derivative_daily_prices'"))).scalar()

        # Identical second import with duplicates
        await service._ingest_derivative_csv(csv_bytes, "test_file4_nsefo.csv")
        await session.commit()
        await session.execute(text("ANALYZE derivative_daily_prices;"))
        await session.commit()

        upd_after = (await session.execute(text("SELECT n_tup_upd FROM pg_stat_user_tables WHERE relname = 'derivative_daily_prices'"))).scalar()

        assert upd_after - upd_before == 0  # Zero physical updates on PostgreSQL!
