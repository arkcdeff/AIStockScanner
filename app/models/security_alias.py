"""SecurityAlias — historical symbol identity records.

Each row records a canonical (exchange, symbol) pairing for a Stock entity
within a validity interval.  This allows point-in-time symbol resolution:

    resolve_security(exchange="NSE", symbol="OLDABC", as_of_date=date(2021,1,1))
    → Stock(id=42, ...)   # even though current symbol is NEWABC

Design invariants (enforced at service layer, not DB exclusion constraint):
  - A given (exchange, symbol) must not have overlapping validity intervals
    for different stock_ids (ambiguous identity).
  - valid_from <= valid_to when valid_to is not None.
  - available_at >= valid_from (cannot know about an identity before it exists,
    though in practice it may equal valid_from).

Overlap detection in service layer generates a DataQualityIssue with code
IDENTITY_AMBIGUOUS rather than raising a hard DB error, to allow partial imports.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.stock import Stock


class SecurityAlias(TimestampMixin, Base):
    """Historical symbol/identity record for a security on an exchange."""

    __tablename__ = "security_aliases"
    __table_args__ = (
        UniqueConstraint(
            "exchange", "symbol", "valid_from",
            name="uq_security_alias_exchange_symbol_from",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_security_alias_valid_range",
        ),
        Index("ix_security_aliases_exchange_symbol", "exchange", "symbol"),
        Index("ix_security_aliases_stock_id", "stock_id"),
        Index("ix_security_aliases_isin", "isin"),
        Index("ix_security_aliases_valid_from", "valid_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Exchange this alias is valid on (NSE / BSE)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)

    # Historical symbol on this exchange
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)

    # ISIN at time of this alias (may differ if company restructured)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Series (EQ, BE, etc.) at time of this alias
    series: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Validity interval on the exchange
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Point-in-time gate: when did this identity become knowable?
    available_at: Mapped[date] = mapped_column(Date, nullable=False)

    # Provenance
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Relationships
    stock: Mapped[Stock] = relationship(back_populates="security_aliases")

    def __repr__(self) -> str:
        return (
            f"<SecurityAlias exchange={self.exchange} symbol={self.symbol} "
            f"from={self.valid_from} to={self.valid_to}>"
        )
