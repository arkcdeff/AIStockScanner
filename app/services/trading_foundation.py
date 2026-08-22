from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.models.trading_account import TRADING_EXECUTION_MODES, TradingAccount
from app.models.trading_order import TradingOrder
from app.repositories.trading import (
    TradingAccountRepository,
    TradingExecutionRepository,
    TradingOrderRepository,
    TradingPositionRepository,
)
from app.schemas.trading import OrderCreate, TradingAccountCreate


class TradingFoundationError(Exception):
    """Base error for the trading foundation domain."""


class TradingAccountNotFoundError(TradingFoundationError):
    def __init__(self, account_id: int) -> None:
        super().__init__(f"Trading account {account_id} not found")
        self.account_id = account_id


class TradingAccountInactiveError(TradingFoundationError):
    def __init__(self, account_id: int) -> None:
        super().__init__(f"Trading account {account_id} is not active")
        self.account_id = account_id


class DuplicateClientOrderIdError(TradingFoundationError):
    def __init__(self, account_id: int, client_order_id: str) -> None:
        super().__init__(
            f"Client order id {client_order_id!r} already exists for account {account_id}"
        )
        self.account_id = account_id
        self.client_order_id = client_order_id


class UnsupportedExecutionModeError(TradingFoundationError):
    def __init__(self, execution_mode: str) -> None:
        super().__init__(f"Execution mode {execution_mode!r} is not supported for this task")
        self.execution_mode = execution_mode


class InvalidOrderTransitionError(TradingFoundationError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"Invalid order transition: {current} -> {target}")
        self.current = current
        self.target = target


class InvalidOrderError(TradingFoundationError):
    """Raised for order-domain validation that is not a transition error."""


ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "created": {"pending", "cancelled", "rejected"},
    "pending": {"accepted", "cancelled", "rejected", "expired"},
    "accepted": {"partially_filled", "filled", "cancelled", "rejected", "expired"},
    "partially_filled": {"partially_filled", "filled", "cancelled", "expired"},
    "filled": set(),
    "cancelled": set(),
    "rejected": set(),
    "expired": set(),
}

TERMINAL_ORDER_STATUSES = {"filled", "cancelled", "rejected", "expired"}


def is_terminal_order_status(status: str) -> bool:
    return status in TERMINAL_ORDER_STATUSES


def validate_order_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    if current in TERMINAL_ORDER_STATUSES:
        return False
    return target in ORDER_STATUS_TRANSITIONS.get(current, set())


class TradingAccountService:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.accounts = TradingAccountRepository(session)

    async def create_account(self, data: TradingAccountCreate) -> TradingAccount:
        if data.execution_mode not in TRADING_EXECUTION_MODES:
            raise UnsupportedExecutionModeError(data.execution_mode)
        if data.execution_mode == "live":
            raise UnsupportedExecutionModeError("live")
        account = TradingAccount(
            owner_user_id=data.owner_user_id,
            name=data.name,
            execution_mode=data.execution_mode,
            base_currency=data.base_currency,
            initial_cash=data.initial_cash,
            cash_balance=data.initial_cash,
            status=data.status,
            is_default=data.is_default,
        )
        return await self.accounts.add(account)

    async def get(self, account_id: int) -> TradingAccount | None:
        return await self.accounts.get(account_id)

    async def get_for_owner(
        self,
        owner_user_id: int,
        *,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[TradingAccount]:
        return await self.accounts.get_by_owner(
            owner_user_id, status=status, offset=offset, limit=limit
        )

    async def set_status(self, account_id: int, status: str) -> TradingAccount:
        account = await self.accounts.get(account_id)
        if account is None:
            raise TradingAccountNotFoundError(account_id)
        account.status = status
        await self.session.flush()
        return account


class TradingOrderService:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.orders = TradingOrderRepository(session)
        self.accounts = TradingAccountRepository(session)
        self.executions = TradingExecutionRepository(session)
        self.positions = TradingPositionRepository(session)

    async def create_order(self, data: OrderCreate) -> TradingOrder:
        account = await self.accounts.get(data.account_id)
        if account is None:
            raise TradingAccountNotFoundError(data.account_id)
        if account.status != "active":
            raise TradingAccountInactiveError(data.account_id)
        if data.execution_mode == "live":
            raise UnsupportedExecutionModeError("live")

        existing = await self.orders.get_by_client_order_id(
            account.id, data.client_order_id
        )
        if existing is not None:
            raise DuplicateClientOrderIdError(account.id, data.client_order_id)

        order = TradingOrder(
            account_id=account.id,
            stock_id=data.stock_id,
            symbol=data.symbol,
            exchange=data.exchange,
            instrument_type=data.instrument_type,
            client_order_id=data.client_order_id,
            side=data.side,
            order_type=data.order_type,
            execution_mode=data.execution_mode,
            time_in_force=data.time_in_force,
            quantity=data.quantity,
            filled_quantity=0,
            remaining_quantity=data.quantity,
            limit_price=data.limit_price,
            stop_price=data.stop_price,
            status="created",
            origin=data.origin,
            strategy_reference=data.strategy_reference,
            signal_reference=data.signal_reference,
        )
        self.session.add(order)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            raise DuplicateClientOrderIdError(
                account.id, data.client_order_id
            ) from None
        await self.session.refresh(order)
        return order

    async def transition_order(
        self, order: TradingOrder, target: str, *, ts: datetime | None = None
    ) -> TradingOrder:
        ts = ts or datetime.now(UTC)
        if order.status == target:
            return order
        if not validate_order_transition(order.status, target):
            raise InvalidOrderTransitionError(order.status, target)
        order.status = target
        if target == "pending":
            order.submitted_at = ts
        elif target == "accepted":
            order.accepted_at = ts
        elif target == "filled":
            order.completed_at = ts
        elif target == "cancelled":
            order.cancelled_at = ts
        elif target == "rejected":
            order.rejected_at = ts
        await self.session.flush()
        return order

    async def reject_order(
        self,
        order: TradingOrder,
        rejection_code: str,
        rejection_reason: str,
        *,
        ts: datetime | None = None,
    ) -> TradingOrder:
        ts = ts or datetime.now(UTC)
        if not validate_order_transition(order.status, "rejected"):
            raise InvalidOrderTransitionError(order.status, "rejected")
        order.status = "rejected"
        order.rejected_at = ts
        order.rejection_code = rejection_code
        order.rejection_reason = rejection_reason
        await self.session.flush()
        return order
