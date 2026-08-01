from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, JSON, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class ScanResult(TimestampMixin, Base):
    __tablename__ = "scan_results"
    __table_args__ = (
        UniqueConstraint(
            "scan_definition_id", "stock_id", "scan_date", name="uq_scan_result_scan_stock_date"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_definition_id: Mapped[int] = mapped_column(
        ForeignKey("scan_definitions.id", ondelete="CASCADE"), index=True
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    scan_date: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

    scan_definition: Mapped["ScanDefinition"] = relationship(back_populates="results")
    stock: Mapped["Stock"] = relationship(back_populates="scan_results")
