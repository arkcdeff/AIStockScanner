from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.models.daily_price import DailyPrice
from app.models.import_job import ImportJob
from app.models.stock import Stock


class MarketDataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_prices(
        self,
        *,
        symbol: str | None = None,
        trade_date: date | None = None,
        offset: int = 0,
        limit: int = 100,
        product_only: bool = False,
    ) -> list[tuple[DailyPrice, Stock]]:
        statement = select(DailyPrice, Stock).join(Stock, Stock.id == DailyPrice.stock_id)
        if product_only:
            statement = statement.where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
        if symbol:
            statement = statement.where(Stock.symbol == symbol.upper())
        if trade_date:
            statement = statement.where(DailyPrice.trade_date == trade_date)
        statement = (
            statement.order_by(DailyPrice.trade_date.desc(), Stock.symbol)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return list(result.all())

    async def summary(self) -> dict[str, object]:
        stock_count = await self.session.scalar(select(func.count()).select_from(Stock))
        price_count = await self.session.scalar(select(func.count()).select_from(DailyPrice))
        min_date, max_date = (
            await self.session.execute(
                select(func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date))
            )
        ).one()
        return {
            "stocks": stock_count or 0,
            "daily_prices": price_count or 0,
            "first_trade_date": min_date,
            "last_trade_date": max_date,
        }

    async def product_summary(self) -> dict[str, object]:
        """Return market-data totals excluding known synthetic persistence fixtures."""
        product_ids = select(Stock.id).where(
            Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)
        )
        stock_count = await self.session.scalar(
            select(func.count()).select_from(Stock).where(
                Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)
            )
        )
        price_count = await self.session.scalar(
            select(func.count()).select_from(DailyPrice).where(
                DailyPrice.stock_id.in_(product_ids)
            )
        )
        min_date, max_date = (
            await self.session.execute(
                select(func.min(DailyPrice.trade_date), func.max(DailyPrice.trade_date)).where(
                    DailyPrice.stock_id.in_(product_ids)
                )
            )
        ).one()
        return {
            "stocks": stock_count or 0,
            "daily_prices": price_count or 0,
            "first_trade_date": min_date,
            "last_trade_date": max_date,
        }

    async def list_imports(self, limit: int = 50) -> list[ImportJob]:
        statement = select(ImportJob).order_by(ImportJob.id.desc()).limit(limit)
        return list((await self.session.scalars(statement)).all())
