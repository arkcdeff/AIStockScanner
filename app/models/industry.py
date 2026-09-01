from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.sector import Sector
    from app.models.stock import Stock


class Industry(TimestampMixin, Base):
    __tablename__ = "industries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True, index=True
    )

    sector: Mapped[Sector | None] = relationship()
    stocks: Mapped[list[Stock]] = relationship(back_populates="industry_ref")
