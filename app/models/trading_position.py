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

POSITION_SIDES = ("long", "short")


class TradingPosition(TimestampMixin, Base):
    """Minimal position persistence for the trading domain.

    Task 19.1 only establishes the storage contract. Position calculation and
    update algorithms (averaging, realized/unrealized PnL) are owned by
    Task 19.4 (Portfolio & Positions).
    """

    __tablename__ = "trading_positions"

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "stock_id",
            "instrument_type",
            name="uq_trading_positions_account_instrument",
        ),
        CheckConstraint(
            "position_side IN ('long', 'short')",
            name="ck_trading_positions_side_valid",
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
    position_side: Mapped[str] = mapped_column(String(8), nullable=False, default="long")
    net_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True, default=Decimal("0")
    )
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True, default=Decimal("0")
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<TradingPosition id={self.id} account_id={self.account_id} "
            f"symbol={self.symbol!r} side={self.position_side} "
            f"net_quantity={self.net_quantity}>"
        )
