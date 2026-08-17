from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.market_index import MarketIndex

class MarketIndexPrice(TimestampMixin, Base):
    __tablename__ = "market_index_prices"

    __table_args__ = (
        UniqueConstraint(
            "market_index_id", "trade_date", name="uq_market_index_price_index_date"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_index_id: Mapped[int] = mapped_column(
        ForeignKey("market_indices.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    previous_close: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    change: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    change_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    volume: Mapped[int | None] = mapped_column(nullable=True)
    observed_at: Mapped[date] = mapped_column(nullable=True)
    fetched_at: Mapped[date] = mapped_column(nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    freshness_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="stale"
    )

    # Relationships
    market_index: Mapped[MarketIndex] = relationship(
        back_populates="index_prices"
    )

    def __repr__(self) -> str:
        return (
            f"<MarketIndexPrice index_code={self.market_index.code} "
            f"date={self.trade_date} close={self.close}>"
        )