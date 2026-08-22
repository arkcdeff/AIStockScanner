"""DerivativeDailyPrice model — Sprint 19.1D-2.

Stores end-of-day time-series facts for derivative contracts (futures & options).
Derivative prices are strictly isolated from cash-equity DailyPrice rows.

Invariants & Constraints:
  - UNIQUE(contract_id, trade_date)
  - open_interest >= 0 (when present; NULL if missing, MISSING != ZERO)
  - change_in_open_interest is signed (positive, negative, or zero)
  - volume_contracts >= 0, turnover >= 0
  - Point-in-time EOD semantics: available_at = date when EOD bhavcopy was published
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class DerivativeDailyPrice(TimestampMixin, Base):
    """End-of-Day price, volume, settlement, and open interest for a derivative contract."""

    __tablename__ = "derivative_daily_prices"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "trade_date",
            name="uq_derivative_daily_prices_contract_date",
        ),
        CheckConstraint(
            "open_interest IS NULL OR open_interest >= 0",
            name="ck_derivative_daily_prices_open_interest_non_negative",
        ),
        CheckConstraint(
            "volume_contracts IS NULL OR volume_contracts >= 0",
            name="ck_derivative_daily_prices_volume_non_negative",
        ),
        CheckConstraint(
            "turnover IS NULL OR turnover >= 0",
            name="ck_derivative_daily_prices_turnover_non_negative",
        ),
        Index("ix_derivative_daily_prices_contract_id", "contract_id"),
        Index("ix_derivative_daily_prices_trade_date", "trade_date"),
        Index("ix_derivative_daily_prices_available_at", "available_at"),
        Index("ix_derivative_daily_prices_contract_date", "contract_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("derivative_contracts.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Prices (Decimal/Numeric for precision)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    last_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    settlement_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    previous_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Volume & Open Interest
    volume_contracts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)  # Normalized to INR Rupees

    open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    change_in_open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # Signed

    # Underlying Value at observation
    underlying_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Point-in-time & Provenance
    available_at: Mapped[date] = mapped_column(Date, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationship
    contract = relationship("DerivativeContract", foreign_keys=[contract_id])

    def __repr__(self) -> str:
        return (
            f"<DerivativeDailyPrice contract_id={self.contract_id} date={self.trade_date} "
            f"close={self.close} settle={self.settlement_price} OI={self.open_interest}>"
        )
