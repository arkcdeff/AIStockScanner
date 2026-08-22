from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.ai_explanation import AIExplanation
    from app.models.alert import AlertEvent, AlertRule
    from app.models.backtest import BacktestTrade
    from app.models.corporate_action import CorporateAction
    from app.models.daily_price import DailyPrice
    from app.models.index_membership import IndexMembership
    from app.models.indicator_result import IndicatorResult
    from app.models.portfolio import Holding
    from app.models.scan_result import ScanResult
    from app.models.sector import Sector
    from app.models.security_alias import SecurityAlias
    from app.models.technical_analysis import TechnicalAnalysis
    from app.models.watchlist import WatchlistStock


class Stock(TimestampMixin, Base):
    __tablename__ = "stocks"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_stock_exchange_symbol"),
        CheckConstraint(
            "lifecycle_status IN ('active','suspended','delisted','inactive','unknown')",
            name="ck_stock_lifecycle_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    exchange: Mapped[str] = mapped_column(String(16), default="NSE", index=True)
    series: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    isin: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    sector_id: Mapped[int | None] = mapped_column(
        ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    industry: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Historical lifecycle status — see exchange_constants.LIFECYCLE_STATUSES
    # Default 'active' for all existing rows (set in migration via server_default).
    # Existing scanners/backtests do NOT read this column — they are unchanged.
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active", index=True
    )

    # Existing relationships (UNCHANGED)
    sector_ref: Mapped[Sector | None] = relationship(back_populates="stocks")
    daily_prices: Mapped[list[DailyPrice]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    scan_results: Mapped[list[ScanResult]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    indicator_results: Mapped[list[IndicatorResult]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    technical_analysis: Mapped[list[TechnicalAnalysis]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    ai_explanations: Mapped[list[AIExplanation]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    watchlist_entries: Mapped[list[WatchlistStock]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    holdings: Mapped[list[Holding]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(back_populates="stock")
    alert_events: Mapped[list[AlertEvent]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    backtest_trades: Mapped[list[BacktestTrade]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )

    # New 19.1C relationships
    security_aliases: Mapped[list[SecurityAlias]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    corporate_actions: Mapped[list[CorporateAction]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    index_memberships: Mapped[list[IndexMembership]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
