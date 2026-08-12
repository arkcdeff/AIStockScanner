from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.auth import User
    from app.models.stock import Stock

BACKTEST_STRATEGY_TYPES = ("scanner", "rule_based", "composite")
BACKTEST_RUN_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
BACKTEST_POSITION_SIDES = ("long", "short")
BACKTEST_TRADE_STATUSES = ("open", "closed")
BACKTEST_POSITION_SIZE_TYPES = ("fixed_amount", "fixed_quantity", "percent_capital", "equal_weight")


class BacktestStrategy(TimestampMixin, Base):
    __tablename__ = "backtest_strategies"
    __table_args__ = (
        CheckConstraint(
            "strategy_type IN ('scanner', 'rule_based', 'composite')",
            name="backtest_strategy_type_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_type: Mapped[str] = mapped_column(String(24), default="scanner", index=True)
    scanner_code: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    configuration_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_by_user: Mapped[User | None] = relationship()
    runs: Mapped[list[BacktestRun]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class BacktestRun(TimestampMixin, Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        CheckConstraint(
            "start_date <= end_date",
            name="backtest_run_date_range_valid",
        ),
        CheckConstraint(
            "initial_capital > 0",
            name="backtest_run_initial_capital_positive",
        ),
        CheckConstraint(
            "commission_rate >= 0",
            name="backtest_run_commission_non_negative",
        ),
        CheckConstraint(
            "slippage_rate >= 0",
            name="backtest_run_slippage_non_negative",
        ),
        CheckConstraint(
            "position_size_value >= 0",
            name="backtest_run_position_size_value_non_negative",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="backtest_run_status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    initial_capital: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    final_capital: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    benchmark_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)

    commission_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)
    slippage_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)

    position_size_type: Mapped[str] = mapped_column(String(24), default="percent")
    position_size_value: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0)

    max_positions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    strategy: Mapped[BacktestStrategy] = relationship(back_populates="runs")
    requested_by_user: Mapped[User | None] = relationship()
    trades: Mapped[list[BacktestTrade]] = relationship(
        back_populates="backtest_run", cascade="all, delete-orphan"
    )
    equity_points: Mapped[list[BacktestEquityPoint]] = relationship(
        back_populates="backtest_run", cascade="all, delete-orphan"
    )
    metric: Mapped[BacktestMetric | None] = relationship(
        back_populates="backtest_run", cascade="all, delete-orphan", uselist=False
    )


class BacktestTrade(TimestampMixin, Base):
    __tablename__ = "backtest_trades"
    __table_args__ = (
        CheckConstraint(
            "position_side IN ('long', 'short')",
            name="backtest_trade_position_side_valid",
        ),
        CheckConstraint(
            "status IN ('open', 'closed')",
            name="backtest_trade_status_valid",
        ),
        CheckConstraint(
            "quantity > 0",
            name="backtest_trade_quantity_positive",
        ),
        CheckConstraint(
            "entry_price > 0",
            name="backtest_trade_entry_price_positive",
        ),
        CheckConstraint(
            "exit_price IS NULL OR exit_price > 0",
            name="backtest_trade_exit_price_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)

    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), default="long")

    entry_signal: Mapped[str | None] = mapped_column(String(80), nullable=True)
    exit_signal: Mapped[str | None] = mapped_column(String(80), nullable=True)

    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    gross_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    costs: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    net_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    return_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    bars_held: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="open")

    backtest_run: Mapped[BacktestRun] = relationship(back_populates="trades")
    stock: Mapped[Stock] = relationship(back_populates="backtest_trades")


class BacktestEquityPoint(Base):
    __tablename__ = "backtest_equity_points"
    __table_args__ = (
        UniqueConstraint(
            "backtest_run_id", "trade_date", name="uq_backtest_equity_run_date"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    invested_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    portfolio_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    drawdown_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    backtest_run: Mapped[BacktestRun] = relationship(back_populates="equity_points")


class BacktestMetric(TimestampMixin, Base):
    __tablename__ = "backtest_metrics"
    __table_args__ = (
        UniqueConstraint("backtest_run_id", name="uq_backtest_metric_run"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    total_return_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    annualized_return_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    cagr: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0)
    losing_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    gross_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    average_win: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    average_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    expectancy: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    max_drawdown_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    sortino_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    average_holding_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    benchmark_return_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    alpha: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    backtest_run: Mapped[BacktestRun] = relationship(back_populates="metric")
