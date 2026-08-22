"""ORM Models for Sprint 19.1D-7: Participant-wise Positions & FII Derivatives Statistics.

Defines persistence models for official NSE participant datasets:
  - DerivativeParticipantPosition: Normalized participant-category position/volume fact rows
  - FiiDerivativeStatistic: Summary stats for Foreign Institutional Investors derivatives activity

Strict Invariants:
  - Long and Short contract gross counts must be >= 0
  - OPEN_INTEREST is explicitly distinguished from TRADING_VOLUME via dataset_type
  - Contract-level identity is never fabricated from aggregate category reports
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DerivativeParticipantPosition(Base):
    """Normalized open interest or volume facts grouped by participant category and instrument bucket."""

    __tablename__ = "derivative_participant_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "OPEN_INTEREST" or "TRADING_VOLUME"
    participant_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "CLIENT", "DII", "FII", "PRO"
    instrument_bucket: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "INDEX_FUTURE", "STOCK_FUTURE", "INDEX_OPTION_CALL", etc.

    long_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    short_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reported_total_long: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reported_total_short: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    available_at: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default="NSE_INDIA")
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "report_date",
            "dataset_type",
            "participant_type",
            "instrument_bucket",
            name="uq_derivative_participant_positions_identity",
        ),
        CheckConstraint("long_contracts >= 0", name="ck_participant_position_long_non_negative"),
        CheckConstraint("short_contracts >= 0", name="ck_participant_position_short_non_negative"),
        Index("ix_participant_pos_lookup", "report_date", "dataset_type"),
    )

    @property
    def net_contracts(self) -> int:
        """Calculate net position contracts (long - short)."""
        return self.long_contracts - self.short_contracts


class FiiDerivativeStatistic(Base):
    """Summary statistics for Foreign Institutional Investors (FII) derivatives activity."""

    __tablename__ = "fii_derivative_statistics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    instrument_category: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "INDEX_FUTURES", "INDEX_OPTIONS", "STOCK_FUTURES", "STOCK_OPTIONS"

    buy_contracts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    buy_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    sell_contracts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sell_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    open_interest_contracts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    open_interest_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    available_at: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default="NSE_INDIA")
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "report_date",
            "instrument_category",
            name="uq_fii_derivative_statistics_identity",
        ),
    )
