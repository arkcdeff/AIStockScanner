from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.backtest import (
    BACKTEST_POSITION_SIDES,
    BACKTEST_RUN_STATUSES,
    BACKTEST_STRATEGY_TYPES,
    BACKTEST_TRADE_STATUSES,
)

STRATEGY_CODE_LENGTH = 80
STRATEGY_NAME_LENGTH = 150
INITIAL_CAPITAL_FIELD = Field(gt=0, description="Initial capital must be positive")


def _validate_enum(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)}")
    return value


class BacktestStrategyBase(BaseModel):
    name: str = Field(min_length=1, max_length=STRATEGY_NAME_LENGTH)
    code: str = Field(min_length=1, max_length=STRATEGY_CODE_LENGTH)
    strategy_type: str = "scanner"
    description: str | None = None
    scanner_code: str | None = Field(default=None, max_length=STRATEGY_CODE_LENGTH)
    configuration_json: dict = Field(default_factory=dict)
    is_active: bool = True
    created_by_user_id: int | None = None

    @field_validator("strategy_type")
    @classmethod
    def _check_strategy_type(cls, v: str) -> str:
        return _validate_enum(v, BACKTEST_STRATEGY_TYPES, "strategy_type")


class BacktestStrategyCreate(BacktestStrategyBase):
    pass


class BacktestStrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=STRATEGY_NAME_LENGTH)
    description: str | None = None
    strategy_type: str | None = None
    scanner_code: str | None = None
    configuration_json: dict | None = None
    is_active: bool | None = None

    @field_validator("strategy_type")
    @classmethod
    def _check_strategy_type(cls, v: str | None) -> str | None:
        return _validate_enum(v, BACKTEST_STRATEGY_TYPES, "strategy_type") if v else v


class BacktestStrategyRead(BacktestStrategyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class BacktestRunBase(BaseModel):
    strategy_id: int
    requested_by_user_id: int | None = None
    start_date: date
    end_date: date
    initial_capital: Decimal = INITIAL_CAPITAL_FIELD
    commission_rate: Decimal = Field(default=0, ge=0)
    slippage_rate: Decimal = Field(default=0, ge=0)
    position_size_type: str = "percent"
    position_size_value: Decimal = Field(default=0, ge=0)
    benchmark_symbol: str | None = Field(default=None, max_length=32)
    max_positions: int | None = Field(default=None, gt=0)
    status: str = "pending"

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        return _validate_enum(v, BACKTEST_RUN_STATUSES, "status")

    @model_validator(mode="after")
    def _check_date_range(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class BacktestRunCreate(BacktestRunBase):
    pass


class BacktestRunRead(BacktestRunBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    final_capital: Decimal | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class BacktestTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backtest_run_id: int
    stock_id: int
    entry_date: date
    entry_price: Decimal
    exit_date: date | None
    exit_price: Decimal | None
    quantity: Decimal
    position_side: str
    entry_signal: str | None
    exit_signal: str | None
    stop_loss: Decimal | None
    target_price: Decimal | None
    gross_pnl: Decimal | None
    costs: Decimal | None
    net_pnl: Decimal | None
    return_percent: Decimal | None
    bars_held: int | None
    status: str
    created_at: datetime
    updated_at: datetime

    @field_validator("position_side")
    @classmethod
    def _check_side(cls, v: str) -> str:
        return _validate_enum(v, BACKTEST_POSITION_SIDES, "position_side")

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        return _validate_enum(v, BACKTEST_TRADE_STATUSES, "status")


class BacktestEquityPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backtest_run_id: int
    trade_date: date
    cash: Decimal
    invested_value: Decimal
    portfolio_value: Decimal
    drawdown_percent: Decimal | None


class BacktestMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backtest_run_id: int
    total_return_percent: Decimal | None
    annualized_return_percent: Decimal | None
    cagr: Decimal | None
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: Decimal | None
    gross_profit: Decimal | None
    gross_loss: Decimal | None
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    expectancy: Decimal | None
    max_drawdown_percent: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    average_holding_days: Decimal | None
    benchmark_return_percent: Decimal | None
    alpha: Decimal | None
    created_at: datetime
    updated_at: datetime


class BacktestRunDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run: BacktestRunRead
    trades: list[BacktestTradeRead]
    equity_points: list[BacktestEquityPointRead]
    metric: BacktestMetricRead | None