from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.portfolio import Holding, Portfolio
from app.models.stock import Stock
from app.repositories.portfolio import HoldingRepository, PortfolioRepository


class PortfolioError(Exception):
    pass


class PortfolioService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.portfolio_repo = PortfolioRepository(session)
        self.holding_repo = HoldingRepository(session)

    # ------------------------------------------------------------ portfolios

    async def create(self, payload: dict[str, Any]) -> Portfolio:
        if not payload.get("account_name") and not payload.get("broker"):
            raise PortfolioError("account_name or broker is required")
        portfolio = Portfolio(**{k: v for k, v in payload.items() if hasattr(Portfolio, k)})
        await self.portfolio_repo.add(portfolio)
        await self.session.commit()
        await self.session.refresh(portfolio)
        return portfolio

    async def update(self, portfolio_id: int, payload: dict[str, Any]) -> Portfolio:
        portfolio = await self.portfolio_repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioError(f"Portfolio {portfolio_id} not found")
        for key, value in payload.items():
            if hasattr(Portfolio, key) and value is not None:
                setattr(portfolio, key, value)
        await self.session.commit()
        await self.session.refresh(portfolio)
        return portfolio

    async def delete(self, portfolio_id: int) -> None:
        portfolio = await self.portfolio_repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioError(f"Portfolio {portfolio_id} not found")
        await self.session.delete(portfolio)
        await self.session.commit()

    async def list(self) -> list[Portfolio]:
        return await self.portfolio_repo.list_with_holdings()

    async def get(self, portfolio_id: int) -> Portfolio | None:
        return await self.portfolio_repo.get_with_holdings(portfolio_id)

    # --------------------------------------------------------------- holdings

    async def add_holding(self, portfolio_id: int, payload: dict[str, Any]) -> Holding:
        portfolio = await self.portfolio_repo.get(portfolio_id)
        if portfolio is None:
            raise PortfolioError(f"Portfolio {portfolio_id} not found")
        stock_id = payload.get("stock_id")
        if stock_id is None:
            raise PortfolioError("stock_id is required")
        stock = await self.session.get(Stock, stock_id)
        if stock is None:
            raise PortfolioError(f"Stock {stock_id} not found")
        if float(payload.get("quantity", 0)) <= 0 or float(payload.get("average_price", 0)) <= 0:
            raise PortfolioError("quantity and average_price must be positive")
        existing = await self.holding_repo.get_for_portfolio(portfolio_id, stock_id)
        if existing is not None:
            raise PortfolioError("Stock already held in this portfolio")
        holding = Holding(**{k: v for k, v in payload.items() if hasattr(Holding, k)})
        holding.portfolio_id = portfolio_id
        await self.holding_repo.add(holding)
        await self.session.commit()
        await self.session.refresh(holding)
        return holding

    async def update_holding(
        self, portfolio_id: int, holding_id: int, payload: dict[str, Any]
    ) -> Holding:
        holding = await self.holding_repo.get(holding_id)
        if holding is None or holding.portfolio_id != portfolio_id:
            raise PortfolioError("Holding not found in portfolio")
        for key, value in payload.items():
            if hasattr(Holding, key) and value is not None:
                setattr(holding, key, value)
        await self.session.commit()
        await self.session.refresh(holding)
        return holding

    async def remove_holding(self, portfolio_id: int, holding_id: int) -> None:
        holding = await self.holding_repo.get(holding_id)
        if holding is None or holding.portfolio_id != portfolio_id:
            raise PortfolioError("Holding not found in portfolio")
        await self.session.delete(holding)
        await self.session.commit()

    async def list_holdings(self, portfolio_id: int) -> list[Holding]:
        return await self.holding_repo.list_for_portfolio(portfolio_id)

    async def holdings_for_stock(self, stock_id: int) -> list[Holding]:
        return await self.holding_repo.list_for_stock(stock_id)

    # ---------------------------------------------------------- valuations

    async def value_holdings(self, holdings: list[Holding]) -> list[dict[str, Any]]:
        if not holdings:
            return []
        stock_ids = [holding.stock_id for holding in holdings]
        latest_prices = await self._latest_prices(stock_ids)
        items: list[dict[str, Any]] = []
        for holding in holdings:
            price = latest_prices.get(holding.stock_id)
            close = float(price.close) if price else None
            previous_close = float(price.previous_close) if price and price.previous_close else None
            quantity = float(holding.quantity)
            average_price = float(holding.average_price)
            invested = quantity * average_price
            current_value = quantity * close if close is not None else None
            pl = (current_value - invested) if current_value is not None else None
            roi_pct = (pl / invested * 100) if pl is not None and invested else None
            today_pl = None
            if close is not None and previous_close is not None:
                today_pl = (close - previous_close) * quantity
            items.append(
                {
                    "holding": holding,
                    "stock": holding.stock,
                    "latest_close": close,
                    "previous_close": previous_close,
                    "quantity": quantity,
                    "average_price": average_price,
                    "invested": round(invested, 2),
                    "current_value": round(current_value, 2) if current_value is not None else None,
                    "pl": round(pl, 2) if pl is not None else None,
                    "roi_pct": round(roi_pct, 2) if roi_pct is not None else None,
                    "today_pl": round(today_pl, 2) if today_pl is not None else None,
                }
            )
        total_value = sum(item["current_value"] or 0 for item in items)
        for item in items:
            allocation = (item["current_value"] or 0) / total_value * 100 if total_value else 0
            item["allocation_pct"] = round(allocation, 2)
        return items

    async def portfolio_valuation(self, portfolio_id: int) -> dict[str, Any]:
        holdings = await self.holding_repo.list_for_portfolio(portfolio_id)
        valued = await self.value_holdings(holdings)
        return self._aggregate(portfolio_id, valued)

    async def overall_summary(self) -> dict[str, Any]:
        portfolios = await self.portfolio_repo.list_with_holdings()
        holdings = [h for portfolio in portfolios for h in portfolio.holdings]
        valued = await self.value_holdings(holdings)
        return self._aggregate(None, valued)

    def _aggregate(
        self, portfolio_id: int | None, valued: list[dict[str, Any]]
    ) -> dict[str, Any]:
        total_value = round(sum(item["current_value"] or 0 for item in valued), 2)
        total_invested = round(sum(item["invested"] for item in valued), 2)
        total_pl = round(total_value - total_invested, 2)
        total_roi = round(total_pl / total_invested * 100, 2) if total_invested else 0.0
        today_pl = round(sum(item["today_pl"] or 0 for item in valued), 2)
        return {
            "portfolio_id": portfolio_id,
            "holdings_count": len(valued),
            "total_invested": total_invested,
            "total_value": total_value,
            "total_pl": total_pl,
            "total_roi_pct": total_roi,
            "today_pl": today_pl,
        }

    async def _latest_prices(self, stock_ids: list[int]) -> dict[int, DailyPrice]:
        prices = (
            await self.session.scalars(
                select(DailyPrice)
                .where(DailyPrice.stock_id.in_(stock_ids))
                .order_by(DailyPrice.stock_id, DailyPrice.trade_date)
            )
        ).all()
        latest: dict[int, DailyPrice] = {}
        for price in prices:
            latest[price.stock_id] = price
        return latest
