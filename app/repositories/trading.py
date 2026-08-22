from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.models.trading_account import TradingAccount
from app.models.trading_execution import TradingExecution
from app.models.trading_order import TradingOrder
from app.models.trading_position import TradingPosition
from app.repositories.base import BaseRepository


class TradingAccountRepository(BaseRepository["TradingAccount"]):
    def __init__(self, session: Any) -> None:
        super().__init__(TradingAccount, session)

    async def get_by_owner(
        self,
        owner_user_id: int,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[TradingAccount]:
        statement = select(TradingAccount).where(
            TradingAccount.owner_user_id == owner_user_id
        )
        if status is not None:
            statement = statement.where(TradingAccount.status == status)
        statement = statement.order_by(TradingAccount.id).offset(offset).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def count_for_owner(
        self, owner_user_id: int, *, status: str | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(TradingAccount)
            .where(TradingAccount.owner_user_id == owner_user_id)
        )
        if status is not None:
            statement = statement.where(TradingAccount.status == status)
        return int(await self.session.scalar(statement) or 0)

    async def update_status(self, account_id: int, status: str) -> TradingAccount | None:
        account = await self.get(account_id)
        if account is None:
            return None
        account.status = status
        await self.session.flush()
        return account


class TradingOrderRepository(BaseRepository["TradingOrder"]):
    def __init__(self, session: Any) -> None:
        super().__init__(TradingOrder, session)

    async def get_by_client_order_id(
        self, account_id: int, client_order_id: str
    ) -> TradingOrder | None:
        statement = select(TradingOrder).where(
            TradingOrder.account_id == account_id,
            TradingOrder.client_order_id == client_order_id,
        )
        return await self.session.scalar(statement)

    async def get_for_account(self, order_id: int, account_id: int) -> TradingOrder | None:
        statement = select(TradingOrder).where(
            TradingOrder.id == order_id,
            TradingOrder.account_id == account_id,
        )
        return await self.session.scalar(statement)

    async def list_for_account(
        self,
        account_id: int,
        *,
        status: str | None = None,
        side: str | None = None,
        instrument_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[TradingOrder]:
        statement = select(TradingOrder).where(TradingOrder.account_id == account_id)
        if status is not None:
            statement = statement.where(TradingOrder.status == status)
        if side is not None:
            statement = statement.where(TradingOrder.side == side)
        if instrument_type is not None:
            statement = statement.where(TradingOrder.instrument_type == instrument_type)
        if created_after is not None:
            statement = statement.where(TradingOrder.created_at >= created_after)
        if created_before is not None:
            statement = statement.where(TradingOrder.created_at <= created_before)
        statement = statement.order_by(TradingOrder.id.desc()).offset(offset).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def count_for_account(
        self, account_id: int, *, status: str | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(TradingOrder)
            .where(TradingOrder.account_id == account_id)
        )
        if status is not None:
            statement = statement.where(TradingOrder.status == status)
        return int(await self.session.scalar(statement) or 0)

    async def update_status(self, order_id: int, status: str) -> TradingOrder | None:
        order = await self.get(order_id)
        if order is None:
            return None
        order.status = status
        await self.session.flush()
        return order


class TradingExecutionRepository(BaseRepository["TradingExecution"]):
    def __init__(self, session: Any) -> None:
        super().__init__(TradingExecution, session)

    async def get_by_execution_id(
        self, account_id: int, execution_id: str
    ) -> TradingExecution | None:
        statement = select(TradingExecution).where(
            TradingExecution.account_id == account_id,
            TradingExecution.execution_id == execution_id,
        )
        return await self.session.scalar(statement)

    async def list_for_order(
        self, order_id: int, *, offset: int = 0, limit: int = 100
    ) -> list[TradingExecution]:
        statement = (
            select(TradingExecution)
            .where(TradingExecution.order_id == order_id)
            .order_by(TradingExecution.id)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def count_for_order(self, order_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(TradingExecution)
            .where(TradingExecution.order_id == order_id)
        )
        return int(await self.session.scalar(statement) or 0)


class TradingPositionRepository(BaseRepository["TradingPosition"]):
    def __init__(self, session: Any) -> None:
        super().__init__(TradingPosition, session)

    async def get_for_account_instrument(
        self, account_id: int, stock_id: int, instrument_type: str
    ) -> TradingPosition | None:
        statement = select(TradingPosition).where(
            TradingPosition.account_id == account_id,
            TradingPosition.stock_id == stock_id,
            TradingPosition.instrument_type == instrument_type,
        )
        return await self.session.scalar(statement)

    async def list_for_account(
        self, account_id: int, *, offset: int = 0, limit: int = 100
    ) -> list[TradingPosition]:
        statement = (
            select(TradingPosition)
            .where(TradingPosition.account_id == account_id)
            .order_by(TradingPosition.id)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())
