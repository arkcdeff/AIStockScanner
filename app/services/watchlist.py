from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS, is_product_symbol
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.models.watchlist import Watchlist, WatchlistStock
from app.repositories.watchlist import WatchlistRepository, WatchlistStockRepository


class WatchlistError(Exception):
    pass


class WatchlistService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.watchlist_repo = WatchlistRepository(session)
        self.stock_repo = WatchlistStockRepository(session)

    # --------------------------------------------------------------- watchlists

    async def create(self, payload: dict[str, Any]) -> Watchlist:
        if not payload.get("name"):
            raise WatchlistError("Watchlist name is required")
        if payload.get("is_default"):
            await self._clear_defaults()
        watchlist = Watchlist(**{k: v for k, v in payload.items() if hasattr(Watchlist, k)})
        await self.watchlist_repo.add(watchlist)
        await self.session.commit()
        await self.session.refresh(watchlist)
        return watchlist

    async def update(self, watchlist_id: int, payload: dict[str, Any]) -> Watchlist:
        watchlist = await self.watchlist_repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistError(f"Watchlist {watchlist_id} not found")
        if payload.get("is_default"):
            await self._clear_defaults()
        for key, value in payload.items():
            if hasattr(Watchlist, key) and value is not None:
                setattr(watchlist, key, value)
        await self.session.commit()
        await self.session.refresh(watchlist)
        return watchlist

    async def delete(self, watchlist_id: int) -> None:
        watchlist = await self.watchlist_repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistError(f"Watchlist {watchlist_id} not found")
        await self.session.delete(watchlist)
        await self.session.commit()

    async def list(self) -> list[Watchlist]:
        return await self.watchlist_repo.list_ordered()

    async def get(self, watchlist_id: int) -> Watchlist | None:
        return await self.watchlist_repo.get_with_stocks(watchlist_id)

    async def _clear_defaults(self) -> None:
        default = await self.watchlist_repo.get_default()
        if default is not None:
            default.is_default = False
            await self.session.commit()

    # ----------------------------------------------------------------- stocks

    async def add_stock(
        self,
        watchlist_id: int,
        stock_id: int,
        *,
        notes: str | None = None,
        priority: int = 0,
    ) -> WatchlistStock:
        watchlist = await self.watchlist_repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistError(f"Watchlist {watchlist_id} not found")
        stock = await self.session.get(Stock, stock_id)
        if stock is None:
            raise WatchlistError(f"Stock {stock_id} not found")
        if not is_product_symbol(stock.symbol):
            raise WatchlistError("Stock is not available in the product universe")
        existing = await self.stock_repo.get_entry(watchlist_id, stock_id)
        if existing is not None:
            raise WatchlistError("Stock already in watchlist")
        entry = WatchlistStock(
            watchlist_id=watchlist_id,
            stock_id=stock_id,
            notes=notes,
            priority=priority,
        )
        await self.stock_repo.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def remove_stock(self, watchlist_id: int, stock_id: int) -> None:
        entry = await self.stock_repo.get_entry(watchlist_id, stock_id)
        if entry is None:
            raise WatchlistError("Stock not found in watchlist")
        await self.session.delete(entry)
        await self.session.commit()

    async def update_stock(
        self,
        watchlist_id: int,
        stock_id: int,
        *,
        notes: str | None = None,
        priority: int | None = None,
    ) -> WatchlistStock:
        entry = await self.stock_repo.get_entry(watchlist_id, stock_id)
        if entry is None:
            raise WatchlistError("Stock not found in watchlist")
        if notes is not None:
            entry.notes = notes
        if priority is not None:
            entry.priority = priority
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def list_stocks(self, watchlist_id: int) -> list[dict[str, Any]]:
        entries = await self.stock_repo.list_for_watchlist(watchlist_id)
        entries = [entry for entry in entries if is_product_symbol(entry.stock.symbol)]
        if not entries:
            return []
        stock_ids = [entry.stock_id for entry in entries]
        prices = (
            await self.session.execute(
                select(DailyPrice)
                .where(DailyPrice.stock_id.in_(stock_ids))
                .order_by(DailyPrice.stock_id, DailyPrice.trade_date)
            )
        ).scalars()
        latest_price_map: dict[int, DailyPrice] = {}
        for price in prices:
            latest_price_map[price.stock_id] = price

        return [
            {
                "entry": entry,
                "stock": entry.stock,
                "latest_price": latest_price_map.get(entry.stock_id),
            }
            for entry in entries
        ]

    async def search_stocks(self, query: str, limit: int = 20) -> list[Stock]:
        statement = (
            select(Stock)
            .where(
                Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                (Stock.symbol.ilike(f"%{query}%")) | (Stock.company_name.ilike(f"%{query}%")),
            )
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def watchlists_for_symbol(self, symbol: str) -> list[WatchlistStock]:
        return await self.stock_repo.list_by_symbol(symbol)
