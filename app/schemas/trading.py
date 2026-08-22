from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.trading_account import (
    TRADING_ACCOUNT_BASE_CURRENCIES,
    TRADING_ACCOUNT_STATUSES,
    TRADING_EXECUTION_MODES,
)
from app.models.trading_order import (
    ORDER_ORIGINS,
    ORDER_SIDES,
    ORDER_TIME_IN_FORCE,
    ORDER_TYPES,
)


def _validate_enum(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)}")
    return value


class TradingAccountBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    execution_mode: str = "paper"
    base_currency: str = "INR"
    initial_cash: Decimal = Field(default=Decimal("0"), ge=0)
    status: str = "active"

    @field_validator("execution_mode")
    @classmethod
    def _check_execution_mode(cls, v: str) -> str:
        return _validate_enum(v, TRADING_EXECUTION_MODES, "execution_mode")

    @field_validator("base_currency")
    @classmethod
    def _check_base_currency(cls, v: str) -> str:
        return _validate_enum(v, TRADING_ACCOUNT_BASE_CURRENCIES, "base_currency")

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        return _validate_enum(v, TRADING_ACCOUNT_STATUSES, "status")


class TradingAccountCreate(TradingAccountBase):
    owner_user_id: int = Field(gt=0)
    is_default: bool = False


class TradingAccountRead(TradingAccountBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int | None
    cash_balance: Decimal
    is_default: bool
    created_at: datetime
    updated_at: datetime


class OrderBase(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    exchange: str = Field(default="NSE", max_length=16)
    instrument_type: str = Field(default="equity", max_length=16)
    side: str
    order_type: str
    quantity: int = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    time_in_force: str | None = None
    client_order_id: str = Field(min_length=1, max_length=128)
    origin: str | None = None
    strategy_reference: str | None = Field(default=None, max_length=128)
    signal_reference: str | None = Field(default=None, max_length=128)
    execution_mode: str = "paper"

    @field_validator("side")
    @classmethod
    def _check_side(cls, v: str) -> str:
        return _validate_enum(v, ORDER_SIDES, "side")

    @field_validator("order_type")
    @classmethod
    def _check_order_type(cls, v: str) -> str:
        return _validate_enum(v, ORDER_TYPES, "order_type")

    @field_validator("execution_mode")
    @classmethod
    def _check_execution_mode(cls, v: str) -> str:
        return _validate_enum(v, TRADING_EXECUTION_MODES, "execution_mode")

    @field_validator("time_in_force")
    @classmethod
    def _check_tif(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_enum(v, ORDER_TIME_IN_FORCE, "time_in_force")

    @field_validator("origin")
    @classmethod
    def _check_origin(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_enum(v, ORDER_ORIGINS, "origin")

    @model_validator(mode="after")
    def _check_price_requirements(self) -> OrderBase:
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("market orders must not specify a limit_price")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit orders require a positive limit_price")
        return self


class OrderCreate(OrderBase):
    account_id: int = Field(gt=0)
    stock_id: int = Field(gt=0)


class OrderRead(OrderBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    stock_id: int
    filled_quantity: int
    remaining_quantity: int
    status: str
    rejection_code: str | None
    rejection_reason: str | None
    submitted_at: datetime | None
    accepted_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    rejected_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    account_id: int
    execution_id: str
    quantity: int
    price: Decimal
    fees: Decimal
    executed_at: datetime
    source: str
    created_at: datetime
    updated_at: datetime


class PositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    stock_id: int
    symbol: str
    exchange: str
    instrument_type: str
    position_side: str
    net_quantity: int
    average_price: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
