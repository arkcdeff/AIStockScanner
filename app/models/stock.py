from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Stock(TimestampMixin, Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str] = mapped_column(String(16), default="NSE", index=True)
    isin: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    daily_prices: Mapped[list["DailyPrice"]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    scan_results: Mapped[list["ScanResult"]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
