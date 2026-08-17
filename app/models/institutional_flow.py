from datetime import date

from sqlalchemy import CheckConstraint, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class InstitutionalFlow(TimestampMixin, Base):
    __tablename__ = "institutional_flows"

    __table_args__ = (
        UniqueConstraint(
            "trade_date", "investor_type", name="uq_institutional_flow_date_type"
        ),
        CheckConstraint(
            "investor_type IN ('FII', 'DII')",
            name="ck_institutional_flow_investor_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    investor_type: Mapped[str] = mapped_column(
        String(16), nullable=False
    )
    buy_value: Mapped[float] = mapped_column(nullable=True)
    sell_value: Mapped[float] = mapped_column(nullable=True)
    net_value: Mapped[float | None] = mapped_column(nullable=True)

    # Derived features
    one_day_cumulative: Mapped[float | None] = mapped_column(nullable=True)
    three_day_cumulative: Mapped[float | None] = mapped_column(nullable=True)
    five_day_cumulative: Mapped[float | None] = mapped_column(nullable=True)
    twenty_day_cumulative: Mapped[float | None] = mapped_column(nullable=True)

    source_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_at: Mapped[date | None] = mapped_column(nullable=True)
    fetched_at: Mapped[date | None] = mapped_column(nullable=True)
    freshness_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="stale"
    )

    # Relationships
    # No direct stock relationship; aggregated by date

    INVESTOR_TYPES = ["FII", "DII"]

    def __repr__(self) -> str:
        return (
            f"<InstitutionalFlow date={self.trade_date} "
            f"type={self.investor_type} net={self.net_value}>"
        )