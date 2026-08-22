"""IndexMembership — historical index constituent records.

Records when a security was a member of an index, including the validity
interval (valid_from / valid_to) and optional weight.

Point-in-time semantics:
  - available_at: when this membership was publicly knowable.
  - A member is visible at evaluation time T only if:
      available_at <= T
      AND valid_from <= T
      AND (valid_to IS NULL OR valid_to >= T)
  - Removed members are NOT deleted; valid_to is set to the last effective date.
  - This enables survivorship-bias-safe universe construction:
      get_index_members(index_id, as_of_date=T)
    returns only members valid AND knowable at T.

Overlap invariant (enforced at service layer via DataQualityIssue):
  - No two rows for the same (index_id, stock_id) should have overlapping
    validity intervals.  If detected, a DataQualityIssue with code
    MEMBERSHIP_OVERLAP is created.

Weight:
  - Stored as Numeric(8,4) when sourced from official constituency data.
  - NULL when not available or when the index is equal-weighted.
  - Do not fabricate weights.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.market_index import MarketIndex
    from app.models.stock import Stock


class IndexMembership(TimestampMixin, Base):
    """Historical constituent record for an index."""

    __tablename__ = "index_memberships"
    __table_args__ = (
        # Uniqueness of membership entry per index/stock/start-date
        UniqueConstraint(
            "index_id", "stock_id", "valid_from",
            name="uq_index_membership_index_stock_from",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_index_membership_valid_range",
        ),
        CheckConstraint(
            "weight IS NULL OR (weight >= 0 AND weight <= 100)",
            name="ck_index_membership_weight_range",
        ),
        Index("ix_index_memberships_index_id_valid", "index_id", "valid_from"),
        Index("ix_index_memberships_stock_id", "stock_id"),
        Index("ix_index_memberships_available_at", "available_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    index_id: Mapped[int] = mapped_column(
        ForeignKey("market_indices.id", ondelete="CASCADE"),
        nullable=False,
    )

    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Validity interval — when was this stock a constituent?
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Index weight (0-100, percent) — NULL if not officially sourced
    weight: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    # Point-in-time gate: when did this membership information become knowable?
    available_at: Mapped[date] = mapped_column(Date, nullable=False)

    # Provenance
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    market_index: Mapped[MarketIndex] = relationship(back_populates="memberships")
    stock: Mapped[Stock] = relationship(back_populates="index_memberships")

    def __repr__(self) -> str:
        return (
            f"<IndexMembership index_id={self.index_id} stock_id={self.stock_id} "
            f"from={self.valid_from} to={self.valid_to}>"
        )
