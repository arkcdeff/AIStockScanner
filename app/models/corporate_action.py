"""CorporateAction — historical corporate actions for securities.

Idempotency key: (stock_id, action_type, ex_date)
Repeated imports of the same action (same stock + type + ex_date) will
hit the UniqueConstraint and should be handled via ON CONFLICT DO NOTHING
or an upsert in the importer.

Point-in-time semantics:
  - available_at: when this action was publicly knowable (announcement / gazette).
    For point-in-time research, only actions with available_at <= as_of_date
    should be applied.
  - ex_date: the date on which the price goes ex-adjustment.  Adjustment
    applies to all prices strictly before ex_date.

Price adjustment (19.1C — deterministic service-layer only, no DailyPrice mutation):
  - split: adjusted = raw / (numerator / denominator)
  - bonus: same formula (bonus 1:2 means 1 free share per 2 held
           → ratio_numerator=1, ratio_denominator=2
           → factor = (2+1)/2 = 1.5 — see PriceAdjustmentService)
  - dividend: deferred (see exchange_constants.DEFERRED_ADJUSTMENT_TYPES)
  - rights: deferred
  - merger / demerger / buyback: deferred
  - symbol_change / name_change: data-only, no price adjustment
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.stock import Stock


class CorporateAction(TimestampMixin, Base):
    """Historical corporate action record (split, bonus, dividend, etc.)."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ("
            "'split','bonus','dividend','rights','merger','demerger',"
            "'buyback','symbol_change','name_change'"
            ")",
            name="ck_corporate_action_type",
        ),
        CheckConstraint(
            "ratio_numerator IS NULL OR ratio_numerator > 0",
            name="ck_corporate_action_ratio_num_positive",
        ),
        CheckConstraint(
            "ratio_denominator IS NULL OR ratio_denominator > 0",
            name="ck_corporate_action_ratio_den_positive",
        ),
        CheckConstraint(
            "cash_amount IS NULL OR cash_amount >= 0",
            name="ck_corporate_action_cash_amount_nonneg",
        ),
        Index("ix_corporate_actions_stock_id_exdate", "stock_id", "ex_date"),
        Index("ix_corporate_actions_available_at", "available_at"),
        Index("ix_corporate_actions_action_type", "action_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Action type — see exchange_constants.CORPORATE_ACTION_TYPES
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Deterministic identity
    idempotency_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Key dates
    announcement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    record_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ex_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Ratio fields — for split/bonus/rights
    # Split 1:2 (1 share → 2 shares): numerator=1, denominator=2
    # Bonus 1:2 (1 free per 2 held → total 3 per 2): numerator=1, denominator=2
    ratio_numerator: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    ratio_denominator: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    # Cash amount — for dividend / buyback price
    cash_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # Symbol/name change details
    old_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    new_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Provenance
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Point-in-time: when was this action publicly knowable?
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Additional notes / metadata
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Human-verified flag (auto-imports are unverified by default)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relationships
    stock: Mapped[Stock] = relationship(back_populates="corporate_actions")


    def compute_fingerprint(self) -> str:
        """Compute a deterministic SHA-256 hash based on economic fields."""
        import hashlib
        from decimal import Decimal
        def _fmt(val) -> str:
            if val is None:
                return "NULL"
            if isinstance(val, Decimal):
                return str(val.normalize())
            if isinstance(val, date):
                return val.isoformat()
            return str(val)
            
        parts = [
            _fmt(self.stock_id),
            _fmt(self.action_type),
            _fmt(self.ex_date),
            _fmt(self.ratio_numerator),
            _fmt(self.ratio_denominator),
            _fmt(self.cash_amount),
            _fmt(self.old_symbol),
            _fmt(self.new_symbol),
        ]
        canonical = "|".join(parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def __repr__(self) -> str:
        return (
            f"<CorporateAction stock_id={self.stock_id} type={self.action_type} "
            f"ex_date={self.ex_date}>"
        )


@event.listens_for(CorporateAction, 'before_insert')
def receive_before_insert(mapper, connection, target):
    if not target.idempotency_hash:
        target.idempotency_hash = target.compute_fingerprint()
