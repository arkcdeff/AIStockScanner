from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.stock import Stock


class TechnicalAnalysis(TimestampMixin, Base):
    __tablename__ = "technical_analysis"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_technical_analysis_stock_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)

    # Composite scores (0-100)
    trend_score: Mapped[int] = mapped_column(Integer, default=0)
    momentum_score: Mapped[int] = mapped_column(Integer, default=0)
    volume_score: Mapped[int] = mapped_column(Integer, default=0)
    delivery_score: Mapped[int] = mapped_column(Integer, default=0)
    volatility_score: Mapped[int] = mapped_column(Integer, default=0)
    overall_score: Mapped[int] = mapped_column(Integer, default=0)

    # Support / resistance
    support: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    resistance: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Classic pivot points (derived from previous day H/L/C)
    pivot: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pivot_r1: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pivot_r2: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pivot_r3: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pivot_s1: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pivot_s2: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pivot_s3: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Fibonacci pivot points
    fib_pivot: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fib_r1: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fib_r2: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fib_r3: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fib_s1: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fib_s2: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    fib_s3: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Volume / delivery metrics
    relative_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    delivery_strength: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Ratings
    trend_rating: Mapped[str | None] = mapped_column(String(24), nullable=True)
    momentum_rating: Mapped[str | None] = mapped_column(String(24), nullable=True)
    volatility_rating: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # Flags
    is_trending: Mapped[bool] = mapped_column(Boolean, default=False)
    is_breakout: Mapped[bool] = mapped_column(Boolean, default=False)

    stock: Mapped[Stock] = relationship(back_populates="technical_analysis")
