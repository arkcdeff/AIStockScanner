"""Fundamental and Corporate Intelligence Models — Sprint 19.1E.

Provides persistence models for point-in-time safe financial statements,
corporate announcements, and shareholding patterns.

Strict Invariants:
  - Financial amounts stored normalized in INR Crore (unit="INR_CRORE", currency="INR").
  - MISSING != ZERO (None represents missing/unreported items; never 0).
  - Explicit period types: "QUARTERLY" or "ANNUAL".
  - Explicit statement bases: "CONSOLIDATED" or "STANDALONE".
  - Revision aware: "AS_REPORTED" or "LATEST_RESTATED".
  - CorporateAction (from 19.1C) is reused for economic actions (dividends/splits/bonuses).
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.corporate_action import CorporateAction
    from app.models.stock import Stock


class FinancialStatement(Base):
    """Normalized point-in-time financial statement fact record (Income Statement, Balance Sheet, Cash Flow)."""

    __tablename__ = "financial_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    period_type: Mapped[str] = mapped_column(String(16), nullable=False)
    statement_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    reported_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)
    revision_type: Mapped[str] = mapped_column(String(16), nullable=False, default="AS_REPORTED")

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="INR_CRORE")

    # Income Statement (Numeric 18,4 | None)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    operating_income: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    ebit: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pbt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    pat: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    eps: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Balance Sheet (Numeric 18,4 | None)
    total_assets: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_liabilities: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_equity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    cash_and_equivalents: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Cash Flow (Numeric 18,4 | None)
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    investing_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    financing_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    free_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    shares_outstanding: Mapped[int | None] = mapped_column(Numeric(18, 0), nullable=True)

    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default="OFFICIAL_DISCLOSURE")
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    stock: Mapped[Stock] = relationship("Stock", backref="financial_statements")

    __table_args__ = (
        CheckConstraint(
            "period_type IN ('QUARTERLY', 'ANNUAL')",
            name="ck_financial_statements_period_type",
        ),
        CheckConstraint(
            "statement_basis IN ('CONSOLIDATED', 'STANDALONE')",
            name="ck_financial_statements_basis",
        ),
        CheckConstraint(
            "revision_type IN ('AS_REPORTED', 'LATEST_RESTATED')",
            name="ck_financial_statements_revision",
        ),
        UniqueConstraint(
            "stock_id",
            "period_type",
            "statement_basis",
            "period_end",
            "revision_type",
            name="uq_financial_statements_identity",
        ),
        Index("ix_financial_statements_stock_period", "stock_id", "period_end"),
        Index("ix_financial_statements_pit", "available_at"),
    )

    def compute_fingerprint(self) -> str:
        """Compute SHA-256 fingerprint for idempotency."""
        parts = [
            str(self.stock_id),
            self.period_type or "QUARTERLY",
            self.statement_basis or "CONSOLIDATED",
            self.period_end.isoformat() if self.period_end else "",
            self.revision_type or "AS_REPORTED",
        ]
        return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


class CorporateAnnouncement(Base):
    """Historical corporate announcement disclosure event."""

    __tablename__ = "corporate_announcements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    announcement_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    corporate_action_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("corporate_actions.id", ondelete="SET NULL"), nullable=True
    )

    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default="NSE_ANNOUNCEMENT")
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    stock: Mapped[Stock] = relationship("Stock", backref="corporate_announcements")
    corporate_action: Mapped[CorporateAction | None] = relationship("CorporateAction")

    __table_args__ = (
        CheckConstraint(
            "category IN ('FINANCIAL_RESULTS', 'BOARD_MEETING', 'DIVIDEND', 'BONUS', 'STOCK_SPLIT', "
            "'RIGHTS', 'BUYBACK', 'MERGER', 'DEMERGER', 'ACQUISITION', 'ORDER_WIN', 'CAPEX', "
            "'FUND_RAISE', 'MANAGEMENT_CHANGE', 'CREDIT_RATING', 'LITIGATION', 'REGULATORY', 'OTHER')",
            name="ck_corporate_announcements_category",
        ),
        Index("ix_corporate_announcements_stock_date", "stock_id", "announcement_date"),
        Index("ix_corporate_announcements_pit", "available_at"),
    )

    def compute_fingerprint(self) -> str:
        """Compute SHA-256 fingerprint for idempotency."""
        parts = [
            str(self.stock_id),
            self.category,
            self.announcement_date.isoformat(),
            self.title,
        ]
        return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


class ShareholdingPattern(Base):
    """Historical quarter-end shareholding breakdown and promoter pledge statistics."""

    __tablename__ = "shareholding_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    reported_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)

    promoter_holding_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    fii_holding_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    dii_holding_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    public_holding_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)

    promoter_pledged_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    promoter_holding_change: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)

    source_name: Mapped[str] = mapped_column(String(64), nullable=False, default="OFFICIAL_SHAREHOLDING")
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    stock: Mapped[Stock] = relationship("Stock", backref="shareholding_patterns")

    __table_args__ = (
        UniqueConstraint("stock_id", "period_end", name="uq_shareholding_patterns_identity"),
        Index("ix_shareholding_patterns_stock_period", "stock_id", "period_end"),
        Index("ix_shareholding_patterns_pit", "available_at"),
    )

    def compute_fingerprint(self) -> str:
        """Compute SHA-256 fingerprint for idempotency."""
        parts = [
            str(self.stock_id),
            self.period_end.isoformat(),
            str(self.promoter_holding_pct),
        ]
        return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


@event.listens_for(FinancialStatement, "before_insert")
def _set_statement_idempotency(mapper, connection, target: FinancialStatement) -> None:
    if not target.idempotency_hash:
        target.idempotency_hash = target.compute_fingerprint()


@event.listens_for(CorporateAnnouncement, "before_insert")
def _set_announcement_idempotency(mapper, connection, target: CorporateAnnouncement) -> None:
    if not target.idempotency_hash:
        target.idempotency_hash = target.compute_fingerprint()


@event.listens_for(ShareholdingPattern, "before_insert")
def _set_shareholding_idempotency(mapper, connection, target: ShareholdingPattern) -> None:
    if not target.idempotency_hash:
        target.idempotency_hash = target.compute_fingerprint()
