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

EXECUTION_SOURCES = ("system", "simulation", "manual")


class TradingExecution(TimestampMixin, Base):
    """Execution / fill record for an order.

    Task 19.1 establishes the durable fill semantics only. No fill simulation
    or broker interaction exists in this task (Task 19.3 owns the engine).
    """

    __tablename__ = "trading_executions"

    __table_args__ = (
        UniqueConstraint(
            "account_id", "execution_id", name="uq_trading_executions_account_execution"
        ),
        CheckConstraint("quantity > 0", name="ck_trading_executions_quantity_positive"),
        CheckConstraint("price > 0", name="ck_trading_executions_price_positive"),
        CheckConstraint("fees >= 0", name="ck_trading_executions_fees_nonneg"),
        CheckConstraint(
            "source IN ('system', 'simulation', 'manual')",
            name="ck_trading_executions_source_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("trading_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    execution_id: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="system")

    def __repr__(self) -> str:
        return (
            f"<TradingExecution id={self.id} order_id={self.order_id} "
            f"quantity={self.quantity} price={self.price}>"
        )
