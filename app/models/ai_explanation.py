from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.stock import Stock


class AIExplanation(TimestampMixin, Base):
    __tablename__ = "ai_explanations"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "trade_date", "provider", name="uq_ai_explanation_stock_date_provider"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    response: Mapped[str] = mapped_column(Text)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    stock: Mapped[Stock] = relationship(back_populates="ai_explanations")
