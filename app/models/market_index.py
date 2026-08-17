from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.market_index_price import MarketIndexPrice


class MarketIndex(TimestampMixin, Base):
    __tablename__ = "market_indices"

    __table_args__ = (
        UniqueConstraint("code", "country", name="uq_market_index_code_country"),
        UniqueConstraint("provider_symbol", name="uq_market_index_provider_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(16), nullable=False, default="INDIA")
    exchange: Mapped[str] = mapped_column(String(32), nullable=True)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, default="indian_index"
    )
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(40), nullable=True, default="Asia/Kolkata"
    )
    provider_symbol: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Relationships
    index_prices: Mapped[list[MarketIndexPrice]] = relationship(
        back_populates="market_index", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<MarketIndex code={self.code} name={self.name} country={self.country}>"