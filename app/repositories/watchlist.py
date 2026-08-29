from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.stock import Stock
from app.models.watchlist import Watchlist, WatchlistStock
from app.repositories.base import BaseRepository


class WatchlistRepository(BaseRepository[Watchlist]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Watchlist, session)

    async def list_ordered(self) -> list[Watchlist]:
        statement = (
            select(Watchlist)
            .options(selectinload(Watchlist.stocks).selectinload(WatchlistStock.stock))
            .order_by(
                Watchlist.is_default.desc(),
                Watchlist.is_favorite.desc(),
                Watchlist.sort_order.asc(),
                Watchlist.name.asc(),
            )
        )
        return list(await self.session.scalars(statement))

    async def get_with_stocks(self, object_id: int) -> Watchlist | None:
        statement = (
            select(Watchlist)
            .where(Watchlist.id == object_id)
            .options(selectinload(Watchlist.stocks).selectinload(WatchlistStock.stock))
        )
        return await self.session.scalar(statement)

    async def get_default(self) -> Watchlist | None:
        statement = (
            select(Watchlist).where(Watchlist.is_default.is_(True)).order_by(Watchlist.id).limit(1)
        )
        return await self.session.scalar(statement)


class WatchlistStockRepository(BaseRepository[WatchlistStock]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(WatchlistStock, session)

    async def get_entry(self, watchlist_id: int, stock_id: int) -> WatchlistStock | None:
        statement = select(WatchlistStock).where(
            WatchlistStock.watchlist_id == watchlist_id,
            WatchlistStock.stock_id == stock_id,
        )
        return await self.session.scalar(statement)

    async def list_for_watchlist(self, watchlist_id: int) -> list[WatchlistStock]:
        statement = (
            select(WatchlistStock)
            .where(WatchlistStock.watchlist_id == watchlist_id)
            .options(selectinload(WatchlistStock.stock))
            .order_by(WatchlistStock.priority.asc(), WatchlistStock.added_at.asc())
        )
        return list(await self.session.scalars(statement))

    async def list_by_symbol(self, symbol: str) -> list[WatchlistStock]:
        statement = (
            select(WatchlistStock)
            .join(Stock, WatchlistStock.stock_id == Stock.id)
            .where(Stock.symbol == symbol.upper())
            .options(selectinload(WatchlistStock.watchlist))
        )
        return list(await self.session.scalars(statement))
