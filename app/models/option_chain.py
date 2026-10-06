"""OptionChainSnapshot and OptionChainQuote SQLAlchemy models — Milestone G.11-OPT-1.

Implements the persistent schema for synchronized Indian index option-chain
observations, range-partitioned quotes, revision tracking, source/calculated Greeks
isolation, and multi-timestamp point-in-time (PIT) replay safety.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OptionChainSnapshot(Base):
    """Synchronized cross-sectional observation header for an index option chain."""

    __tablename__ = "option_chain_snapshots"
    __table_args__ = (
        CheckConstraint(
            "chain_scope IN ('ALL_SUPPORTED_EXPIRIES', 'SINGLE_EXPIRY')",
            name="ck_option_chain_snapshots_chain_scope",
        ),
        CheckConstraint(
            "(chain_scope = 'ALL_SUPPORTED_EXPIRIES' AND expiry_scope IS NULL) OR "
            "(chain_scope = 'SINGLE_EXPIRY' AND expiry_scope IS NOT NULL)",
            name="ck_option_chain_snapshots_expiry_scope_match",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_option_chain_snapshots_revision_pos",
        ),
        CheckConstraint(
            "quality_status IN ('VALID', 'WARNING', 'ACCEPT_WITH_FLAG', 'REJECTED')",
            name="ck_option_chain_snapshots_quality_status",
        ),
        # Partial unique index for ALL_SUPPORTED_EXPIRIES (expiry_scope IS NULL)
        Index(
            "uq_opt_snapshots_all",
            "underlying_index_id",
            "market_timestamp",
            "source_name",
            "revision",
            unique=True,
            postgresql_where=text("chain_scope = 'ALL_SUPPORTED_EXPIRIES'"),
        ),
        # Partial unique index for SINGLE_EXPIRY
        Index(
            "uq_opt_snapshots_single",
            "underlying_index_id",
            "expiry_scope",
            "market_timestamp",
            "source_name",
            "revision",
            unique=True,
            postgresql_where=text("chain_scope = 'SINGLE_EXPIRY'"),
        ),
        Index("ix_opt_snapshots_underlying_mkt_ts", "underlying_index_id", "market_timestamp"),
        Index("ix_opt_snapshots_underlying_avail_at", "underlying_index_id", "available_at"),
        Index("ix_opt_snapshots_batch_id", "batch_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    underlying_index_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("market_indices.id", ondelete="CASCADE"), nullable=False
    )

    chain_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    expiry_scope: Mapped[date | None] = mapped_column(Date, nullable=True)
    spot_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    # 5-Timestamp Model (Stored UTC)
    market_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingestion_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Provenance
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    raw_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Revision
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # Data Quality & Completeness
    quality_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="VALID", server_default="VALID"
    )
    quality_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    is_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    expected_strike_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_strike_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Optional Futures Reference
    near_futures_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    near_futures_contract_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("derivative_contracts.id", ondelete="SET NULL"), nullable=True
    )

    # Greek Calculation Reproducibility Context
    calculation_spot_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    risk_free_rate_used: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    dividend_yield_used: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    time_to_expiry_years: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    time_to_expiry_convention: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pricing_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    underlying_index = relationship("MarketIndex", foreign_keys=[underlying_index_id])
    near_futures_contract = relationship(
        "DerivativeContract", foreign_keys=[near_futures_contract_id]
    )
    quotes = relationship(
        "OptionChainQuote",
        back_populates="snapshot",
        cascade="all, delete-orphan",
        primaryjoin="OptionChainSnapshot.id == OptionChainQuote.snapshot_id",
    )

    def __repr__(self) -> str:
        return (
            f"<OptionChainSnapshot id={self.id} underlying_id={self.underlying_index_id} "
            f"scope={self.chain_scope} mkt_time={self.market_timestamp} rev={self.revision}>"
        )


class OptionChainQuote(Base):
    """Range-partitioned individual option contract quote within a snapshot."""

    __tablename__ = "option_chain_quotes"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "contract_id",
            "trade_date",
            name="uq_option_chain_quotes_snapshot_contract_date",
        ),
        CheckConstraint(
            "option_type IN ('CE', 'PE')",
            name="ck_option_chain_quotes_option_type",
        ),
        CheckConstraint(
            "strike_price > 0",
            name="ck_option_chain_quotes_strike_positive",
        ),
        CheckConstraint(
            "volume IS NULL OR volume >= 0",
            name="ck_option_chain_quotes_volume_non_negative",
        ),
        CheckConstraint(
            "open_interest IS NULL OR open_interest >= 0",
            name="ck_option_chain_quotes_open_interest_non_negative",
        ),
        CheckConstraint(
            "(bid IS NULL OR ask IS NULL) OR (bid <= ask)",
            name="ck_option_chain_quotes_bid_ask_valid",
        ),
        Index("ix_opt_quotes_snapshot_date_contract", "snapshot_id", "trade_date", "contract_id"),
        Index("ix_opt_quotes_contract_trade_date", "contract_id", "trade_date"),
        Index("ix_opt_quotes_trade_date_snapshot", "trade_date", "snapshot_id"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("option_chain_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    contract_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("derivative_contracts.id", ondelete="CASCADE"), nullable=False
    )

    # Partition Key
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True, nullable=False)

    # Contract Normalized Specs
    strike_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    option_type: Mapped[str] = mapped_column(String(2), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Market Facts
    ltp: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    ask: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    bid_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ask_qty: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # OI Semantics Separation
    source_change_in_oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    interval_change_in_oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("option_chain_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    session_change_in_oi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Source-Provided Analytics
    source_iv: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    source_delta: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    source_gamma: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    source_theta: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    source_vega: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    source_rho: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    # Calculated Analytics
    calculated_iv: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    calculated_delta: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    calculated_gamma: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    calculated_theta: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    calculated_vega: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    calculated_rho: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    # Quality & Provenance
    quality_flags: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    snapshot = relationship("OptionChainSnapshot", foreign_keys=[snapshot_id], back_populates="quotes")
    contract = relationship("DerivativeContract", foreign_keys=[contract_id])
    previous_snapshot = relationship("OptionChainSnapshot", foreign_keys=[previous_snapshot_id])

    def __repr__(self) -> str:
        return (
            f"<OptionChainQuote id={self.id} snapshot_id={self.snapshot_id} "
            f"contract_id={self.contract_id} strike={self.strike_price} type={self.option_type} "
            f"trade_date={self.trade_date}>"
        )
