from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import contains_eager

from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.repositories.base import BaseRepository


class MarketIndexPriceRepository(BaseRepository["MarketIndexPrice"]):
    def __init__(self, session: Any) -> None:
        super().__init__(MarketIndexPrice, session)

    async def get_by_index_and_date(
        self, market_index_id: int, trade_date: date
    ) -> MarketIndexPrice | None:
        statement = select(MarketIndexPrice).where(
            MarketIndexPrice.market_index_id == market_index_id,
            MarketIndexPrice.trade_date == trade_date,
        )
        result = await self.session.scalars(statement)
        return result.one_or_none()

    async def get_by_index_code_and_date(
        self, index_code: str, trade_date: date
    ) -> MarketIndexPrice | None:
        stmt = (
            select(MarketIndexPrice)
            .join(MarketIndex, MarketIndexPrice.market_index_id == MarketIndex.id)
            .where(MarketIndex.code == index_code, MarketIndexPrice.trade_date == trade_date)
        )
        result = await self.session.scalars(stmt)
        return result.one_or_none()

    async def get_date_range(
        self,
        market_index_id: int,
        start_date: date,
        end_date: date,
    ) -> list[MarketIndexPrice]:
        """Return price rows for the index within [start_date, end_date].

        No-look-ahead guarantee: rows are strictly bounded by ``end_date``
        (the requested evaluation date), never by the wall clock.
        """
        statement = select(MarketIndexPrice).where(
            MarketIndexPrice.market_index_id == market_index_id,
            MarketIndexPrice.trade_date >= start_date,
            MarketIndexPrice.trade_date <= end_date,
        )
        statement = statement.order_by(MarketIndexPrice.trade_date.desc())
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_date_range_by_index_code(
        self, index_code: str, start_date: date, end_date: date
    ) -> list[MarketIndexPrice]:
        """Return price rows for the index code within [start_date, end_date].

        No-look-ahead guarantee: rows are strictly bounded by ``end_date``
        (the requested evaluation date), never by the wall clock.
        """
        mi_stmt = select(MarketIndex.id).where(MarketIndex.code == index_code)
        mi_result = await self.session.scalars(mi_stmt)
        mi_id = mi_result.one_or_none()
        if mi_id is None:
            return []
        stmt = (
            select(MarketIndexPrice)
            .where(MarketIndexPrice.market_index_id == mi_id)
            .where(MarketIndexPrice.trade_date >= start_date)
            .where(MarketIndexPrice.trade_date <= end_date)
        )
        stmt = stmt.order_by(MarketIndexPrice.trade_date.desc())
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_latest(self, market_index_id: int) -> MarketIndexPrice | None:
        statement = select(MarketIndexPrice).where(
            MarketIndexPrice.market_index_id == market_index_id,
            MarketIndexPrice.trade_date <= date.today(),
        )
        statement = statement.order_by(MarketIndexPrice.trade_date.desc())
        result = await self.session.scalars(statement)
        return result.first()

    async def get_latest_on_or_before(
        self, market_index_id: int, end_date: date
    ) -> MarketIndexPrice | None:
        """Return the latest stored session bounded by an explicit date."""
        statement = (
            select(MarketIndexPrice)
            .where(
                MarketIndexPrice.market_index_id == market_index_id,
                MarketIndexPrice.trade_date <= end_date,
            )
            .order_by(MarketIndexPrice.trade_date.desc())
        )
        result = await self.session.scalars(statement)
        return result.first()

    async def get_existing_dates(
        self, market_index_id: int, start_date: date, end_date: date
    ) -> set[date]:
        statement = select(MarketIndexPrice.trade_date).where(
            MarketIndexPrice.market_index_id == market_index_id,
            MarketIndexPrice.trade_date >= start_date,
            MarketIndexPrice.trade_date <= end_date,
        )
        result = await self.session.scalars(statement)
        return set(result.all())

    async def get_ranges_by_index_codes(
        self,
        index_codes: tuple[str, ...],
        start_date: date,
        end_date: date,
    ) -> list[MarketIndexPrice]:
        """Load all requested histories in one SQL query with no look-ahead."""
        if not index_codes:
            return []
        statement = (
            select(MarketIndexPrice)
            .join(MarketIndex, MarketIndexPrice.market_index_id == MarketIndex.id)
            .options(contains_eager(MarketIndexPrice.market_index))
            .where(
                MarketIndex.code.in_(index_codes),
                MarketIndexPrice.trade_date >= start_date,
                MarketIndexPrice.trade_date <= end_date,
            )
            .order_by(MarketIndex.code, MarketIndexPrice.trade_date.desc())
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def bulk_upsert(self, records: list[MarketIndexPrice]) -> list[MarketIndexPrice]:
        for record in records:
            self.session.add(record)
        await self.session.flush()
        return records