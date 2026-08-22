"""DataQualityIssue — detected data quality problems.

Records deterministic quality issues found during import, validation, or
historical coverage assessment.  Issues are never silently fixed — they
are recorded and reported so that the research platform knows what data
it can and cannot trust.

Design principles:
  - MISSING != ZERO (missing delivery, missing price, missing volume are all
    recorded separately from zero values).
  - Issues can be resolved (resolved=True, resolved_at set) when the source
    data is later corrected or the issue is confirmed as expected behaviour.
  - Multiple issues for the same (exchange, symbol, trade_date) are allowed.
  - Severity: 'warning' (suspicious but may be valid) vs 'error' (definitively
    invalid by the validation rules).

Issue codes (see exchange_constants.py):
  MISSING_PRICE, MISSING_DELIVERY, OHLC_INVALID, NEGATIVE_VOLUME,
  DUPLICATE_PRICE, IDENTITY_AMBIGUOUS, INVALID_VALIDITY_RANGE,
  MEMBERSHIP_OVERLAP, CORPORATE_ACTION_UNRESOLVED, SOURCE_UNAVAILABLE,
  PROVENANCE_MISSING, ZERO_VOLUME
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DataQualityIssue(TimestampMixin, Base):
    """A detected data quality issue in the historical price or identity data."""

    __tablename__ = "data_quality_issues"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('warning', 'error')",
            name="ck_data_quality_issue_severity",
        ),
        Index("ix_data_quality_issues_exchange_date", "exchange", "trade_date"),
        Index("ix_data_quality_issues_issue_code", "issue_code"),
        Index("ix_data_quality_issues_resolved", "resolved"),
        Index("ix_data_quality_issues_detected_at", "detected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Issue classification
    issue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")

    # Scope / context
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Human-readable explanation
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Detection and resolution timestamps
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DataQualityIssue code={self.issue_code} severity={self.severity} "
            f"exchange={self.exchange} symbol={self.symbol} date={self.trade_date}>"
        )
