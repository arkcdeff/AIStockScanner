"""DerivativeContractRepository — Repository layer for derivative contracts and lot-size history.

Sprint 19.1D-1.
Enforces point-in-time querying (available_at <= as_of_date) and idempotent additions.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.derivative_contract import DerivativeContract, DerivativeLotSizeHistory


class DerivativeContractRepository:
    """Repository managing DerivativeContract and DerivativeLotSizeHistory persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, contract: DerivativeContract) -> DerivativeContract:
        """Add a DerivativeContract idempotently.

        If a contract with the exact same SHA-256 fingerprint already exists,
        returns the existing contract without duplicating.
        """
        if not contract.idempotency_hash:
            contract.idempotency_hash = contract.compute_fingerprint()

        existing = await self.get_by_fingerprint(contract.idempotency_hash)
        if existing is not None:
            return existing

        self.session.add(contract)
        await self.session.flush()
        return contract

    async def get_by_fingerprint(self, fingerprint: str) -> DerivativeContract | None:
        """Fetch a contract by its unique idempotency SHA-256 hash."""
        stmt = select(DerivativeContract).where(
            DerivativeContract.idempotency_hash == fingerprint
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, contract_id: int) -> DerivativeContract | None:
        """Fetch contract by primary key."""
        stmt = (
            select(DerivativeContract)
            .where(DerivativeContract.id == contract_id)
            .options(
                selectinload(DerivativeContract.underlying_stock),
                selectinload(DerivativeContract.underlying_index),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_economic_identity(
        self,
        exchange: str,
        underlying_stock_id: int | None,
        underlying_index_id: int | None,
        instrument_type: str,
        expiry_date: date,
        strike_price: Decimal | None = None,
        option_type: str | None = None,
        as_of_date: date | None = None,
    ) -> DerivativeContract | None:
        """Fetch contract matching its core economic fields."""
        stmt = select(DerivativeContract).where(
            DerivativeContract.exchange == exchange,
            DerivativeContract.instrument_type == instrument_type,
            DerivativeContract.expiry_date == expiry_date,
        )
        if underlying_stock_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == underlying_stock_id)
        if underlying_index_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_index_id == underlying_index_id)
        if strike_price is not None:
            stmt = stmt.where(DerivativeContract.strike_price == strike_price)
        if option_type is not None:
            stmt = stmt.where(DerivativeContract.option_type == option_type)
        if as_of_date is not None:
            stmt = stmt.where(DerivativeContract.available_at <= as_of_date)

        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_contracts(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        instrument_type: str | None = None,
        expiry_date: date | None = None,
        as_of_date: date | None = None,
        include_expired: bool = True,
    ) -> list[DerivativeContract]:
        """List derivative contracts matching point-in-time cutoff filters."""
        stmt = select(DerivativeContract)

        if underlying_stock_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_stock_id == underlying_stock_id)
        if underlying_index_id is not None:
            stmt = stmt.where(DerivativeContract.underlying_index_id == underlying_index_id)
        if instrument_type is not None:
            stmt = stmt.where(DerivativeContract.instrument_type == instrument_type)
        if expiry_date is not None:
            stmt = stmt.where(DerivativeContract.expiry_date == expiry_date)
        if as_of_date is not None:
            stmt = stmt.where(DerivativeContract.available_at <= as_of_date)
        if not include_expired and as_of_date is not None:
            stmt = stmt.where(DerivativeContract.expiry_date >= as_of_date)

        stmt = stmt.order_by(
            DerivativeContract.expiry_date.asc(),
            DerivativeContract.trading_symbol.asc(),
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add_lot_size_history(
        self, history: DerivativeLotSizeHistory
    ) -> DerivativeLotSizeHistory:
        """Add a lot size history record."""
        self.session.add(history)
        await self.session.flush()
        return history

    async def get_lot_size_as_of(
        self,
        underlying_stock_id: int | None = None,
        underlying_index_id: int | None = None,
        as_of_date: date = date.today(),
    ) -> int | None:
        """Query historical lot size for an underlying on date T."""
        stmt = select(DerivativeLotSizeHistory).where(
            DerivativeLotSizeHistory.available_at <= as_of_date,
            DerivativeLotSizeHistory.effective_from <= as_of_date,
            (DerivativeLotSizeHistory.effective_to.is_(None))
            | (DerivativeLotSizeHistory.effective_to >= as_of_date),
        )
        if underlying_stock_id is not None:
            stmt = stmt.where(DerivativeLotSizeHistory.underlying_stock_id == underlying_stock_id)
        if underlying_index_id is not None:
            stmt = stmt.where(DerivativeLotSizeHistory.underlying_index_id == underlying_index_id)

        stmt = stmt.order_by(DerivativeLotSizeHistory.effective_from.desc())
        result = await self.session.execute(stmt)
        record = result.scalars().first()
        return record.lot_size if record else None
