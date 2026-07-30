from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.repositories.base import BaseRepository


class StockRepository(BaseRepository[Stock]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Stock, session)

    async def get_by_symbol(self, symbol: str) -> Stock | None:
        statement = select(Stock).where(Stock.symbol == symbol.upper())
        return await self.session.scalar(statement)
