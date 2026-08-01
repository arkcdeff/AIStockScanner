from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.repositories.base import BaseRepository


class ScanDefinitionRepository(BaseRepository[ScanDefinition]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ScanDefinition, session)

    async def get_by_code(self, code: str) -> ScanDefinition | None:
        statement = select(ScanDefinition).where(ScanDefinition.code == code)
        return await self.session.scalar(statement)

    async def list_active(self) -> list[ScanDefinition]:
        statement = select(ScanDefinition).where(ScanDefinition.is_active.is_(True))
        return list(await self.session.scalars(statement))


class ScanResultRepository(BaseRepository[ScanResult]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ScanResult, session)

    async def list_results(
        self,
        *,
        scan_code: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScanResult]:
        statement = select(ScanResult).join(ScanDefinition)
        if scan_code:
            statement = statement.where(ScanDefinition.code == scan_code)
        if symbol:
            statement = statement.join(Stock).where(Stock.symbol == symbol.upper())
        statement = statement.offset(offset).limit(limit)
        return list(await self.session.scalars(statement))
