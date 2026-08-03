from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.stock_detail import StockDetailRepository


class StockDetailService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = StockDetailRepository(session)

    async def get_overview(self, symbol: str) -> dict[str, Any] | None:
        stock = await self.repo.get_stock(symbol)
        if not stock:
            return None

        sector_name = None
        if stock.sector_ref:
            sector_name = stock.sector_ref.name

        latest_price = await self.repo.get_latest_price(stock.id)
        if not latest_price:
            return {
                "stock": stock,
                "sector_name": sector_name,
                "latest_price": None,
                "price_change": None,
                "price_change_pct": None,
                "avg_volume_20d": None,
                "avg_delivery_20d": None,
                "latest_indicator": None,
                "scanner_matches": [],
            }

        averages = await self.repo.get_20d_averages(stock.id, latest_price.trade_date)
        latest_indicator = await self.repo.get_latest_indicator(stock.id)
        scanner_matches = await self.repo.get_scanner_matches(stock.id)

        price_change = None
        price_change_pct = None
        if latest_price.previous_close and latest_price.previous_close > 0:
            price_change = float(latest_price.close - latest_price.previous_close)
            price_change_pct = (price_change / float(latest_price.previous_close)) * 100

        return {
            "stock": stock,
            "sector_name": sector_name,
            "latest_price": latest_price,
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "avg_volume_20d": averages.get("avg_volume"),
            "avg_delivery_20d": averages.get("avg_delivery"),
            "latest_indicator": latest_indicator,
            "scanner_matches": scanner_matches,
        }
