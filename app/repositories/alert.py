from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.alert import AlertEvent, AlertRule
from app.models.stock import Stock
from app.repositories.base import BaseRepository


class AlertRuleRepository(BaseRepository[AlertRule]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AlertRule, session)

    async def list_rules(self, *, offset: int = 0, limit: int = 100) -> list[AlertRule]:
        statement = (
            select(AlertRule)
            .options(selectinload(AlertRule.stock), selectinload(AlertRule.watchlist))
            .order_by(AlertRule.enabled.desc(), AlertRule.id)
            .offset(offset)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def list_active(self) -> list[AlertRule]:
        statement = (
            select(AlertRule)
            .where(AlertRule.enabled.is_(True))
            .options(selectinload(AlertRule.stock), selectinload(AlertRule.watchlist))
        )
        return list(await self.session.scalars(statement))

    async def get_with_details(self, object_id: int) -> AlertRule | None:
        statement = (
            select(AlertRule)
            .where(AlertRule.id == object_id)
            .options(selectinload(AlertRule.stock), selectinload(AlertRule.watchlist))
        )
        return await self.session.scalar(statement)

    async def count_enabled(self) -> int:
        return (
            await self.session.scalar(
                select(func.count()).select_from(AlertRule).where(AlertRule.enabled.is_(True))
            )
            or 0
        )


class AlertEventRepository(BaseRepository[AlertEvent]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AlertEvent, session)

    async def get_with_details(self, object_id: int) -> AlertEvent | None:
        statement = (
            select(AlertEvent)
            .where(AlertEvent.id == object_id)
            .options(selectinload(AlertEvent.alert_rule), selectinload(AlertEvent.stock))
        )
        return await self.session.scalar(statement)

    async def list_recent(
        self, *, offset: int = 0, limit: int = 50, status: str | None = None
    ) -> list[AlertEvent]:
        statement = (
            select(AlertEvent)
            .options(selectinload(AlertEvent.alert_rule), selectinload(AlertEvent.stock))
            .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
        )
        if status:
            statement = statement.where(AlertEvent.status == status)
        statement = statement.offset(offset).limit(limit)
        return list(await self.session.scalars(statement))

    async def list_for_stock(self, stock_id: int, limit: int = 20) -> list[AlertEvent]:
        statement = (
            select(AlertEvent)
            .where(AlertEvent.stock_id == stock_id)
            .options(selectinload(AlertEvent.alert_rule))
            .order_by(AlertEvent.created_at.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def count_by_status(self, status: str) -> int:
        return (
            await self.session.scalar(
                select(func.count()).select_from(AlertEvent).where(AlertEvent.status == status)
            )
            or 0
        )

    async def list_for_symbol(self, symbol: str, limit: int = 20) -> list[AlertEvent]:
        statement = (
            select(AlertEvent)
            .join(Stock, AlertEvent.stock_id == Stock.id)
            .where(Stock.symbol == symbol.upper())
            .options(selectinload(AlertEvent.alert_rule), selectinload(AlertEvent.stock))
            .order_by(AlertEvent.created_at.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))
