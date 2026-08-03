from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.stock import Stock


class IndicatorResult(TimestampMixin, Base):
    __tablename__ = "indicator_results"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_indicator_result_stock_date"),
        Index("ix_indicator_results_date_stock", "trade_date", "stock_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)

    # Trend indicators
    ema_20: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    ema_50: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    ema_100: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    ema_200: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    sma: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Momentum indicators
    rsi_14: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    macd: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    macd_signal: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    macd_hist: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    stochastic_k: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    stochastic_d: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Volatility indicators
    atr: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    bollinger_upper: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    bollinger_middle: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    bollinger_lower: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Trend strength
    adx: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    supertrend: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    supertrend_direction: Mapped[int | None] = mapped_column(nullable=True)

    # Volume indicators
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    obv: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    stock: Mapped[Stock] = relationship(back_populates="indicator_results")
