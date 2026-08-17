from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models.market_index import MarketIndex
from app.repositories.base import BaseRepository


class MarketIndexRepository(BaseRepository["MarketIndex"]):
    def __init__(self, session: Any) -> None:
        super().__init__(MarketIndex, session)

    async def get_by_code(self, code: str) -> MarketIndex | None:
        statement = select(MarketIndex).where(MarketIndex.code == code)
        result = await self.session.scalars(statement)
        return result.one_or_none()

    async def list_active(self) -> list[MarketIndex]:
        statement = select(MarketIndex).where(MarketIndex.is_active)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_by_category(self, category: str) -> list[MarketIndex]:
        statement = select(MarketIndex).where(
            MarketIndex.category == category, MarketIndex.is_active
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_by_country(self, country: str) -> list[MarketIndex]:
        statement = select(MarketIndex).where(
            MarketIndex.country == country, MarketIndex.is_active
        )
        result = await self.session.scalars(statement)
        return list(result.all())