from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.stock import Stock


class ComparableCompany(TimestampMixin, Base):
    __tablename__ = "comparable_companies"
    __table_args__ = (
        UniqueConstraint("stock_id", "peer_stock_id", name="uq_comparable_companies_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    peer_stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    stock: Mapped[Stock] = relationship(foreign_keys=[stock_id], back_populates="peers")
    peer_stock: Mapped[Stock] = relationship(foreign_keys=[peer_stock_id])
