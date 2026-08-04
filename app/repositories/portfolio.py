from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.portfolio import Holding, Portfolio
from app.repositories.base import BaseRepository


class PortfolioRepository(BaseRepository[Portfolio]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Portfolio, session)

    async def list_with_holdings(self) -> list[Portfolio]:
        statement = (
            select(Portfolio)
            .options(selectinload(Portfolio.holdings).selectinload(Holding.stock))
            .order_by(Portfolio.id)
        )
        return list(await self.session.scalars(statement))

    async def get_with_holdings(self, object_id: int) -> Portfolio | None:
        statement = (
            select(Portfolio)
            .where(Portfolio.id == object_id)
            .options(selectinload(Portfolio.holdings).selectinload(Holding.stock))
        )
        return await self.session.scalar(statement)


class HoldingRepository(BaseRepository[Holding]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Holding, session)

    async def get_for_portfolio(self, portfolio_id: int, stock_id: int) -> Holding | None:
        statement = select(Holding).where(
            Holding.portfolio_id == portfolio_id,
            Holding.stock_id == stock_id,
        )
        return await self.session.scalar(statement)

    async def list_for_portfolio(self, portfolio_id: int) -> list[Holding]:
        statement = (
            select(Holding)
            .where(Holding.portfolio_id == portfolio_id)
            .options(selectinload(Holding.stock))
            .order_by(Holding.id)
        )
        return list(await self.session.scalars(statement))

    async def list_for_stock(self, stock_id: int) -> list[Holding]:
        statement = (
            select(Holding)
            .where(Holding.stock_id == stock_id)
            .options(selectinload(Holding.portfolio))
            .order_by(Holding.id)
        )
        return list(await self.session.scalars(statement))
