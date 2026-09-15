"""Derivative Daily Price Repository — Sprint 19.1D-2.

Provides bounded, point-in-time safe data access and bulk upsert operations for
DerivativeDailyPrice records.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import asc, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice


class DerivativeDailyPriceRepository:
    """Repository for DerivativeDailyPrice persistence & queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_upsert(self, prices: list[DerivativeDailyPrice]) -> int:
        """Perform an idempotent bulk upsert on (contract_id, trade_date)."""
        if not prices:
            return 0

        # Extract dict representations for insert values
        values_list = []
        for p in prices:
            if isinstance(p, dict):
                values_list.append(p)
            else:
                values_list.append(
                    {
                        "contract_id": p.contract_id,
                        "trade_date": p.trade_date,
                        "open": p.open,
                        "high": p.high,
                        "low": p.low,
                        "close": p.close,
                        "last_price": p.last_price,
                        "settlement_price": p.settlement_price,
                        "previous_close": p.previous_close,
                        "volume_contracts": p.volume_contracts,
                        "turnover": p.turnover,
                        "open_interest": p.open_interest,
                        "change_in_open_interest": p.change_in_open_interest,
                        "underlying_value": p.underlying_value,
                        "available_at": p.available_at,
                        "source_name": p.source_name,
                        "source_reference": p.source_reference,
                        "fetched_at": p.fetched_at,
                    }
                )

        # Batch size of 1000 for efficiency
        batch_size = 1000
        upserted_count = 0

        for i in range(0, len(values_list), batch_size):
            batch = values_list[i : i + batch_size]
            stmt = insert(DerivativeDailyPrice).values(batch)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_derivative_daily_prices_contract_date",
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "last_price": stmt.excluded.last_price,
                    "settlement_price": stmt.excluded.settlement_price,
                    "previous_close": stmt.excluded.previous_close,
                    "volume_contracts": stmt.excluded.volume_contracts,
                    "turnover": stmt.excluded.turnover,
                    "open_interest": stmt.excluded.open_interest,
                    "change_in_open_interest": stmt.excluded.change_in_open_interest,
                    "underlying_value": stmt.excluded.underlying_value,
                    "available_at": stmt.excluded.available_at,
                    "source_name": stmt.excluded.source_name,
                    "source_reference": stmt.excluded.source_reference,
                    "fetched_at": stmt.excluded.fetched_at,
                },
                where=or_(
                    DerivativeDailyPrice.open.is_distinct_from(stmt.excluded.open),
                    DerivativeDailyPrice.high.is_distinct_from(stmt.excluded.high),
                    DerivativeDailyPrice.low.is_distinct_from(stmt.excluded.low),
                    DerivativeDailyPrice.close.is_distinct_from(stmt.excluded.close),
                    DerivativeDailyPrice.last_price.is_distinct_from(stmt.excluded.last_price),
                    DerivativeDailyPrice.settlement_price.is_distinct_from(stmt.excluded.settlement_price),
                    DerivativeDailyPrice.previous_close.is_distinct_from(stmt.excluded.previous_close),
                    DerivativeDailyPrice.volume_contracts.is_distinct_from(stmt.excluded.volume_contracts),
                    DerivativeDailyPrice.turnover.is_distinct_from(stmt.excluded.turnover),
                    DerivativeDailyPrice.open_interest.is_distinct_from(stmt.excluded.open_interest),
                    DerivativeDailyPrice.change_in_open_interest.is_distinct_from(stmt.excluded.change_in_open_interest),
                    DerivativeDailyPrice.underlying_value.is_distinct_from(stmt.excluded.underlying_value),
                ),
            )
            await self.session.execute(stmt)
            upserted_count += len(batch)

        return upserted_count

    async def get_by_contract_and_date(
        self, contract_id: int, trade_date: date
    ) -> DerivativeDailyPrice | None:
        """Fetch single daily price row by contract_id and trade_date."""
        stmt = select(DerivativeDailyPrice).where(
            DerivativeDailyPrice.contract_id == contract_id,
            DerivativeDailyPrice.trade_date == trade_date,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_futures_history(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        expiry_date: date | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        as_of_date: date | None = None,
    ) -> list[tuple[DerivativeDailyPrice, DerivativeContract]]:
        """Fetch point-in-time safe historical futures prices with contract details."""
        stmt = (
            select(DerivativeDailyPrice, DerivativeContract)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(DerivativeContract.instrument_type == "FUTURE")
        )

        if underlying_stock_id:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == underlying_stock_id)
        if underlying_index_id:
            stmt = stmt.where(DerivativeContract.underlying_index_id == underlying_index_id)
        if expiry_date:
            stmt = stmt.where(DerivativeContract.expiry_date == expiry_date)
        if start_date:
            stmt = stmt.where(DerivativeDailyPrice.trade_date >= start_date)
        if end_date:
            stmt = stmt.where(DerivativeDailyPrice.trade_date <= end_date)
        if as_of_date:
            stmt = stmt.where(DerivativeDailyPrice.available_at <= as_of_date)

        stmt = stmt.order_by(
            asc(DerivativeDailyPrice.trade_date),
            asc(DerivativeContract.expiry_date),
        )

        res = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in res.all()]

    async def get_futures_curve(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        trade_date: date = date.today(),
        as_of_date: date | None = None,
    ) -> list[tuple[DerivativeDailyPrice, DerivativeContract]]:
        """Fetch all active futures expiries and their price facts for an underlying on trade_date."""
        cutoff_date = as_of_date or trade_date

        stmt = (
            select(DerivativeDailyPrice, DerivativeContract)
            .join(DerivativeContract, DerivativeDailyPrice.contract_id == DerivativeContract.id)
            .where(
                DerivativeContract.instrument_type == "FUTURE",
                DerivativeDailyPrice.trade_date == trade_date,
                DerivativeDailyPrice.available_at <= cutoff_date,
            )
        )

        if underlying_stock_id:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == underlying_stock_id)
        if underlying_index_id:
            stmt = stmt.where(DerivativeContract.underlying_index_id == underlying_index_id)

        stmt = stmt.order_by(asc(DerivativeContract.expiry_date))
        res = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in res.all()]
