from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import JSON, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MarketIntelligenceSnapshot(TimestampMixin, Base):
    __tablename__ = "market_intelligence_snapshots"

    __table_args__ = (
        UniqueConstraint(
            "market_date", name="uq_market_intelligence_snapshot_date"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    generated_at: Mapped[date] = mapped_column(Date, nullable=False)

    # Factor scores (-100 to +100)
    price_action_score: Mapped[Decimal | None] = mapped_column(
        nullable=True
    )
    breadth_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    sector_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    institutional_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    volatility_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    global_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    gift_nifty_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    currency_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    crude_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    macro_score: Mapped[Decimal | None] = mapped_column(nullable=True)

    # Composite and confidence
    composite_score: Mapped[Decimal | None] = mapped_column(nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(nullable=True)

    # Regime
    regime: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )

    # Factor breakdowns (JSON)
    bullish_factors_json: Mapped[str | None] = mapped_column(
        JSON, nullable=True
    )
    bearish_factors_json: Mapped[str | None] = mapped_column(
        JSON, nullable=True
    )
    risk_factors_json: Mapped[str | None] = mapped_column(
        JSON, nullable=True
    )
    missing_factors_json: Mapped[str | None] = mapped_column(
        JSON, nullable=True
    )

    # Data completeness
    data_completeness_percent: Mapped[Decimal | None] = mapped_column(
        nullable=True
    )
    stale_factor_count: Mapped[int] = mapped_column(default=0)
    missing_factor_count: Mapped[int] = mapped_column(default=0)

    def __repr__(self) -> str:
        return (
            f"<MarketIntelligenceSnapshot date={self.market_date} "
            f"regime={self.regime} composite={self.composite_score}>"
        )