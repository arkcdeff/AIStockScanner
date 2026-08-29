from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.market_intelligence.breadth_universe import (
    BreadthUniverse,
    universe_eligible_stock,
    universe_sql_filters,
)
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.repositories.base import BaseRepository


class BreadthRepository(BaseRepository[Stock]):
    """Bulk repository for market-breadth inputs.

    All date-bounded reads are bounded by an explicit ``end_date`` (the
    requested evaluation date) so the no-look-ahead guarantee is preserved:
    rows are never fetched relative to the wall clock.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Stock, session)

    async def list_universe_stocks(self, universe: BreadthUniverse) -> list[Stock]:
        """Return the current members of the breadth universe in one query.

        ``universe_eligible_stock`` is applied per row so the eligibility rule
        (exchange/series/active) stays centralized in the universe module.
        """
        statement = (
            select(Stock)
            .where(
                *universe_sql_filters(universe),
                Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
            )
            .order_by(Stock.symbol)
        )
        result = await self.session.scalars(statement)
        return [
            stock for stock in result.all() if universe_eligible_stock(stock, universe)
        ]

    async def load_daily_prices(
        self,
        stock_ids: list[int],
        start_date: date,
        end_date: date,
    ) -> list[DailyPrice]:
        """Load every universe stock's history in one bounded SQL query.

        No-look-ahead guarantee: rows are strictly bounded by ``end_date``
        (the requested evaluation date), never by the wall clock.
        """
        if not stock_ids:
            return []
        statement = select(DailyPrice).where(
            DailyPrice.stock_id.in_(stock_ids),
            DailyPrice.trade_date >= start_date,
            DailyPrice.trade_date <= end_date,
        )
        statement = statement.order_by(DailyPrice.stock_id, DailyPrice.trade_date)
        result = await self.session.scalars(statement)
        return list(result.all())
