from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.repositories.stock import StockRepository
from app.schemas.stock import StockCreate


class StockService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = StockRepository(session)

    async def create(self, payload: StockCreate) -> Stock:
        existing = await self.repository.get_by_symbol(payload.symbol)
        if existing is not None:
            raise ValueError(f"Stock symbol '{payload.symbol.upper()}' already exists")

        stock = Stock(
            symbol=payload.symbol.upper().strip(),
            company_name=payload.company_name.strip(),
            exchange=payload.exchange.upper().strip(),
            series=payload.series.upper().strip() if payload.series else None,
            isin=payload.isin.upper().strip() if payload.isin else None,
            sector_id=payload.sector_id,
            industry=payload.industry.strip() if payload.industry else None,
        )
        await self.repository.add(stock)
        await self.session.commit()
        return stock
