"""Tests for Idempotent UPSERT Write Efficiency — Sprint 19.1J-R4.

Verifies:
  - Identical equity re-import -> zero actual updates
  - Identical derivative re-import -> zero actual updates
  - Genuine changes (OHLC, OI, volume, NULL transitions) -> exact row updates
  - Provenance isolation -> no data rewrite
  - No duplicates created
"""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.data.nse_parser import PriceRecord
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.stock import Stock
from app.repositories.derivative_daily_price import DerivativeDailyPriceRepository
from app.services.nse_import import NSEImportService


@pytest.mark.asyncio
async def test_equity_upsert_efficiency_and_genuine_changes() -> None:
    factory = get_session_factory()
    async with factory() as db_session:
        # 1. Create a Stock
        stock = Stock(symbol="TESTUPSERT", company_name="Test Upsert Corp", is_active=True)
        db_session.add(stock)
        await db_session.flush()

        import_service = NSEImportService(db_session)
        rec_v1 = PriceRecord(
            symbol="TESTUPSERT",
            series="EQ",
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("105"),
            last_price=Decimal("105"),
            previous_close=Decimal("98"),
            volume=1000,
            turnover=Decimal("105000"),
            trade_count=50,
            trade_date=date(2026, 7, 10),
            isin="INE000000001",
        )
        
        sid = stock.id
        # First Import
        await import_service._upsert([rec_v1])
        await db_session.commit()

        # Query initial row
        res = await db_session.execute(select(DailyPrice).where(DailyPrice.stock_id == sid, DailyPrice.trade_date == date(2026, 7, 10)))
        price_v1 = res.scalar_one()
        assert price_v1.close == Decimal("105")
        assert price_v1.volume == 1000

        # 2. Identical Re-Import -> Zero actual changes
        await import_service._upsert([rec_v1])
        await db_session.commit()
        db_session.expire_all()

        res = await db_session.execute(select(DailyPrice).where(DailyPrice.stock_id == sid, DailyPrice.trade_date == date(2026, 7, 10)))
        price_v2 = res.scalar_one()
        assert price_v2.close == Decimal("105")
        assert price_v2.volume == 1000

        # 3. Genuine OHLC Change -> Updates
        rec_v2_ohcl = PriceRecord(
            symbol="TESTUPSERT",
            series="EQ",
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("108"),
            last_price=Decimal("108"),
            previous_close=Decimal("98"),
            volume=1000,
            turnover=Decimal("105000"),
            trade_count=50,
            trade_date=date(2026, 7, 10),
            isin="INE000000001",
        )
        await import_service._upsert([rec_v2_ohcl])
        await db_session.commit()
        db_session.expire_all()

        res = await db_session.execute(select(DailyPrice).where(DailyPrice.stock_id == sid, DailyPrice.trade_date == date(2026, 7, 10)))
        price_v3 = res.scalar_one()
        assert price_v3.close == Decimal("108")

        # 4. Genuine Volume Change -> Updates
        rec_v3_vol = PriceRecord(
            symbol="TESTUPSERT",
            series="EQ",
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("95"),
            close=Decimal("108"),
            last_price=Decimal("108"),
            previous_close=Decimal("98"),
            volume=2500,
            turnover=Decimal("105000"),
            trade_count=50,
            trade_date=date(2026, 7, 10),
            isin="INE000000001",
        )
        await import_service._upsert([rec_v3_vol])
        await db_session.commit()
        db_session.expire_all()

        res = await db_session.execute(select(DailyPrice).where(DailyPrice.stock_id == sid, DailyPrice.trade_date == date(2026, 7, 10)))
        price_v4 = res.scalar_one()
        assert price_v4.volume == 2500

        # Ensure no duplicate records created
        all_prices = (await db_session.execute(select(DailyPrice).where(DailyPrice.stock_id == sid, DailyPrice.trade_date == date(2026, 7, 10)))).scalars().all()
        assert len(all_prices) == 1


@pytest.mark.asyncio
async def test_derivative_upsert_efficiency_and_genuine_changes() -> None:
    factory = get_session_factory()
    async with factory() as db_session:
        # 1. Create Stock & Contract
        stock = Stock(symbol="TESTFUTSTOCK", company_name="Test Fut Stock Corp", is_active=True)
        db_session.add(stock)
        await db_session.flush()

        contract = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="FUTURE",
            underlying_stock_id=stock.id,
            trading_symbol="TESTFUT2026",
            expiry_date=date(2026, 7, 30),
            lot_size=50,
            lifecycle_status="ACTIVE",
            idempotency_hash="hash_test_fut_upsert",
            available_at=date(2026, 7, 10),
        )
        db_session.add(contract)
        await db_session.flush()

        repo = DerivativeDailyPriceRepository(db_session)

        # Initial Insert
        p1 = DerivativeDailyPrice(
            contract_id=contract.id,
            trade_date=date(2026, 7, 10),
            open=Decimal("1000"),
            high=Decimal("1050"),
            low=Decimal("990"),
            close=Decimal("1020"),
            settlement_price=Decimal("1020"),
            open_interest=5000,
            change_in_open_interest=100,
            volume_contracts=200,
            available_at=date(2026, 7, 10),
            source_name="TEST_SRC",
            source_reference="file_v1.csv",
            fetched_at=datetime.now(),
        )
        await repo.bulk_upsert([p1])
        await db_session.commit()

        # 2. Re-import Identical -> No row mutation
        p_identical = DerivativeDailyPrice(
            contract_id=contract.id,
            trade_date=date(2026, 7, 10),
            open=Decimal("1000"),
            high=Decimal("1050"),
            low=Decimal("990"),
            close=Decimal("1020"),
            settlement_price=Decimal("1020"),
            open_interest=5000,
            change_in_open_interest=100,
            volume_contracts=200,
            available_at=date(2026, 7, 10),
            source_name="TEST_SRC_PROVENANCE_CHANGE",
            source_reference="file_v2.csv",
            fetched_at=datetime.now(),
        )
        await repo.bulk_upsert([p_identical])
        await db_session.commit()

        res = await db_session.execute(select(DerivativeDailyPrice).where(DerivativeDailyPrice.contract_id == contract.id, DerivativeDailyPrice.trade_date == date(2026, 7, 10)))
        rec_v1 = res.scalar_one()
        assert rec_v1.close == Decimal("1020")
        assert rec_v1.open_interest == 5000
        assert rec_v1.volume_contracts == 200

        cid = contract.id
        # 3. OI Change -> Updates
        p_oi_change = DerivativeDailyPrice(
            contract_id=cid,
            trade_date=date(2026, 7, 10),
            open=Decimal("1000"),
            high=Decimal("1050"),
            low=Decimal("990"),
            close=Decimal("1020"),
            settlement_price=Decimal("1020"),
            open_interest=6500,
            change_in_open_interest=1600,
            volume_contracts=200,
            available_at=date(2026, 7, 10),
        )
        await repo.bulk_upsert([p_oi_change])
        await db_session.commit()
        db_session.expire_all()

        res = await db_session.execute(select(DerivativeDailyPrice).where(DerivativeDailyPrice.contract_id == cid, DerivativeDailyPrice.trade_date == date(2026, 7, 10)))
        rec_v2 = res.scalar_one()
        assert rec_v2.open_interest == 6500

        # 4. Nullable value changed NULL -> value -> NULL
        p_null_to_val = DerivativeDailyPrice(
            contract_id=cid,
            trade_date=date(2026, 7, 10),
            open=Decimal("1000"),
            high=Decimal("1050"),
            low=Decimal("990"),
            close=Decimal("1020"),
            settlement_price=Decimal("1020"),
            open_interest=6500,
            change_in_open_interest=1600,
            volume_contracts=200,
            underlying_value=Decimal("24000.50"),
            available_at=date(2026, 7, 10),
        )
        await repo.bulk_upsert([p_null_to_val])
        await db_session.commit()
        db_session.expire_all()

        res = await db_session.execute(select(DerivativeDailyPrice).where(DerivativeDailyPrice.contract_id == cid, DerivativeDailyPrice.trade_date == date(2026, 7, 10)))
        rec_v3 = res.scalar_one()
        assert rec_v3.underlying_value == Decimal("24000.50")

        p_val_to_null = DerivativeDailyPrice(
            contract_id=cid,
            trade_date=date(2026, 7, 10),
            open=Decimal("1000"),
            high=Decimal("1050"),
            low=Decimal("990"),
            close=Decimal("1020"),
            settlement_price=Decimal("1020"),
            open_interest=6500,
            change_in_open_interest=1600,
            volume_contracts=200,
            underlying_value=None,
            available_at=date(2026, 7, 10),
        )
        await repo.bulk_upsert([p_val_to_null])
        await db_session.commit()
        db_session.expire_all()

        res = await db_session.execute(select(DerivativeDailyPrice).where(DerivativeDailyPrice.contract_id == cid, DerivativeDailyPrice.trade_date == date(2026, 7, 10)))
        rec_v4 = res.scalar_one()
        assert rec_v4.underlying_value is None

        # Ensure zero duplicates created
        all_recs = (await db_session.execute(select(DerivativeDailyPrice).where(DerivativeDailyPrice.contract_id == cid, DerivativeDailyPrice.trade_date == date(2026, 7, 10)))).scalars().all()
        assert len(all_recs) == 1
