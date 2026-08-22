from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

TRADING_ACCOUNT_STATUSES = ("active", "suspended", "closed")
TRADING_EXECUTION_MODES = ("paper", "live")
TRADING_ACCOUNT_BASE_CURRENCIES = ("INR", "USD")


class TradingAccount(TimestampMixin, Base):
    """Paper/live trading account owned by a user.

    Task 19.1 establishes the account foundation only. The execution mode
    defaults to ``paper`` and live mode is not operational in this task.
    """

    __tablename__ = "trading_accounts"

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'suspended', 'closed')",
            name="ck_trading_accounts_status_valid",
        ),
        CheckConstraint(
            "execution_mode IN ('paper', 'live')",
            name="ck_trading_accounts_execution_mode_valid",
        ),
        CheckConstraint(
            "base_currency IN ('INR', 'USD')",
            name="ck_trading_accounts_base_currency_valid",
        ),
        CheckConstraint("initial_cash >= 0", name="ck_trading_accounts_initial_cash_nonneg"),
        CheckConstraint(
            "cash_balance >= 0", name="ck_trading_accounts_cash_balance_nonneg"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="paper")
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    initial_cash: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    cash_balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=Decimal("0")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return (
            f"<TradingAccount id={self.id} name={self.name!r} "
            f"execution_mode={self.execution_mode} status={self.status}>"
        )
