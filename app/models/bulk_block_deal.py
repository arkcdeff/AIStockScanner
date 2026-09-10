"""BulkBlockDeal model — Cash market Bulk Deals and Block Deals.

Stores official exchange-disclosed bulk and block deal transactions with full
source provenance, date-granularity point-in-time (PIT) availability, and
amendment tracking.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.stock import Stock


class BulkBlockDeal(Base):
    """Cash market bulk or block deal transaction."""

    __tablename__ = "bulk_block_deals"

    __table_args__ = (
        Index(
            "uq_bulk_block_deals_source_line",
            "source_reference",
            "source_line_index",
            unique=True,
            postgresql_where=text("is_current = TRUE"),
        ),
        CheckConstraint(
            "deal_type IN ('BULK', 'BLOCK')",
            name="ck_bulk_block_deals_deal_type",
        ),
        CheckConstraint(
            "buy_sell IN ('BUY', 'SELL')",
            name="ck_bulk_block_deals_buy_sell",
        ),
        CheckConstraint("quantity > 0", name="ck_bulk_block_deals_quantity_pos"),
        CheckConstraint("trade_price > 0", name="ck_bulk_block_deals_price_pos"),
        CheckConstraint("deal_value > 0", name="ck_bulk_block_deals_value_pos"),
        Index("ix_bulk_block_deals_stock_date", "stock_id", "trade_date"),
        Index("ix_bulk_block_deals_symbol_date", "symbol", "trade_date"),
        Index("ix_bulk_block_deals_date_type", "trade_date", "deal_type"),
        Index("ix_bulk_block_deals_client", "client_name"),
        Index("ix_bulk_block_deals_available_at", "available_at"),
        Index(
            "ix_bulk_block_deals_current_type_date",
            "deal_type",
            "trade_date",
            postgresql_where=text("is_current = TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Core transaction facts
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    deal_type: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    security_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    buy_sell: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trade_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    deal_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Entity resolution (unresolved securities keep stock_id = NULL)
    stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("stocks.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Provenance and identity
    source_line_index: Mapped[int] = mapped_column(nullable=False)
    deal_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Amendment tracking
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("bulk_block_deals.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Point-in-time availability (date granularity post-market disclosure: trade_date + 1 day)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)

    # Provenance metadata
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, server_default="NSE_INDIA")
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    # Relationships
    stock: Mapped[Stock | None] = relationship("Stock", foreign_keys=[stock_id])
    superseded_by: Mapped[list[BulkBlockDeal]] = relationship(
        "BulkBlockDeal",
        foreign_keys=[supersedes_id],
        backref=None,
    )

    def __repr__(self) -> str:
        return (
            f"<BulkBlockDeal id={self.id} date={self.trade_date} type={self.deal_type} "
            f"symbol={self.symbol} side={self.buy_sell} qty={self.quantity} price={self.trade_price} "
            f"current={self.is_current}>"
        )
