from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ORDER_SIDES = ("buy", "sell")
ORDER_TYPES = ("market", "limit")
ORDER_STATUSES = (
    "created",
    "pending",
    "accepted",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected",
    "expired",
)
ORDER_TIME_IN_FORCE = ("day", "ioc")
ORDER_ORIGINS = ("manual", "strategy", "signal", "api", "system")


class TradingOrder(TimestampMixin, Base):
    """Paper/live order instruction.

    Task 19.1 establishes the order contract and lifecycle only. Orders are
    not executed here (no broker, no paper fill engine in this task).
    """

    __tablename__ = "trading_orders"

    __table_args__ = (
        UniqueConstraint(
            "account_id", "client_order_id", name="uq_trading_orders_account_client"
        ),
        CheckConstraint("side IN ('buy', 'sell')", name="ck_trading_orders_side_valid"),
        CheckConstraint(
            "order_type IN ('market', 'limit')", name="ck_trading_orders_type_valid"
        ),
        CheckConstraint(
            "status IN ('created', 'pending', 'accepted', 'partially_filled', 'filled', "
            "'cancelled', 'rejected', 'expired')",
            name="ck_trading_orders_status_valid",
        ),
        CheckConstraint(
            "execution_mode IN ('paper', 'live')",
            name="ck_trading_orders_execution_mode_valid",
        ),
        CheckConstraint(
            "time_in_force IS NULL OR time_in_force IN ('day', 'ioc')",
            name="ck_trading_orders_tif_valid",
        ),
        CheckConstraint("quantity > 0", name="ck_trading_orders_quantity_positive"),
        CheckConstraint("filled_quantity >= 0", name="ck_trading_orders_filled_nonneg"),
        CheckConstraint(
            "remaining_quantity >= 0", name="ck_trading_orders_remaining_nonneg"
        ),
        CheckConstraint(
            "limit_price IS NULL OR limit_price > 0",
            name="ck_trading_orders_limit_price_positive",
        ),
        CheckConstraint(
            "stop_price IS NULL OR stop_price > 0",
            name="ck_trading_orders_stop_price_positive",
        ),
        CheckConstraint(
            "origin IS NULL OR origin IN "
            "('manual', 'strategy', 'signal', 'api', 'system')",
            name="ck_trading_orders_origin_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False, default="NSE")
    instrument_type: Mapped[str] = mapped_column(String(16), nullable=False, default="equity")
    client_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    time_in_force: Mapped[str | None] = mapped_column(String(8), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remaining_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="created", index=True
    )
    origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    strategy_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signal_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rejection_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<TradingOrder id={self.id} account_id={self.account_id} "
            f"symbol={self.symbol!r} side={self.side} status={self.status}>"
        )
