"""DerivativeContract and DerivativeLotSizeHistory models — Sprint 19.1D-1.

DerivativeContract provides canonical identity for futures and options
contracts (index and stock derivatives) without modifying Stock or DailyPrice.

Invariants:
  - Exactly one underlying: underlying_stock_id OR underlying_index_id must be set.
  - Futures: instrument_type='FUTURE', strike_price=NULL, option_type=NULL, lot_size > 0.
  - Options: instrument_type='OPTION', strike_price > 0, option_type IN ('CE', 'PE'), lot_size > 0.
  - Point-in-time EOD semantics: available_at = date when contract master row was published.
  - Idempotency hash: SHA-256 fingerprint on core economic fields.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class DerivativeContract(TimestampMixin, Base):
    """Canonical identity and metadata for derivative contracts (Futures & Options)."""

    __tablename__ = "derivative_contracts"
    __table_args__ = (
        CheckConstraint(
            "(underlying_stock_id IS NOT NULL AND underlying_index_id IS NULL) OR "
            "(underlying_stock_id IS NULL AND underlying_index_id IS NOT NULL)",
            name="ck_derivative_contracts_exactly_one_underlying",
        ),
        CheckConstraint(
            "lot_size > 0",
            name="ck_derivative_contracts_lot_size_positive",
        ),
        CheckConstraint(
            "instrument_type IN ('FUTURE', 'OPTION')",
            name="ck_derivative_contracts_instrument_type",
        ),
        CheckConstraint(
            "(instrument_type = 'FUTURE' AND strike_price IS NULL AND option_type IS NULL) OR "
            "(instrument_type = 'OPTION' AND strike_price IS NOT NULL AND strike_price > 0 AND option_type IN ('CE', 'PE'))",
            name="ck_derivative_contracts_instrument_invariants",
        ),
        CheckConstraint(
            "lifecycle_status IN ('ACTIVE', 'EXPIRED', 'INACTIVE', 'UNKNOWN')",
            name="ck_derivative_contracts_lifecycle_status",
        ),
        Index("ix_derivative_contracts_underlying_stock", "underlying_stock_id"),
        Index("ix_derivative_contracts_underlying_index", "underlying_index_id"),
        Index("ix_derivative_contracts_expiry_date", "expiry_date"),
        Index("ix_derivative_contracts_trading_symbol", "trading_symbol"),
        Index("ix_derivative_contracts_instrument_type", "instrument_type"),
        Index("ix_derivative_contracts_available_at", "available_at"),
        Index("ix_derivative_contracts_source_contract_id", "source_contract_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Classification & Underlyings
    exchange: Mapped[str] = mapped_column(String(16), nullable=False, default="NSE")
    segment: Mapped[str] = mapped_column(String(16), nullable=False, default="NFO")
    # G.11-OPT-14D: the provider's own segment (e.g. "NSE_FO") preserved next to
    # the application's canonical segment ("NFO") instead of replacing it. Nullable
    # because every pre-14D row carries no provider identity at all.
    provider_segment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    instrument_type: Mapped[str] = mapped_column(String(32), nullable=False)

    underlying_stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=True
    )
    underlying_index_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_indices.id", ondelete="CASCADE"), nullable=True
    )

    # Identifiers
    trading_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    source_contract_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Contract Specifications
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    strike_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(8), nullable=True)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_size: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True, default=Decimal("0.05")
    )

    # Dates & Lifecycle
    contract_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_trading_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE"
    )

    # Point-in-time & Idempotency
    idempotency_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)

    # Provenance
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    underlying_stock = relationship("Stock", foreign_keys=[underlying_stock_id])
    underlying_index = relationship("MarketIndex", foreign_keys=[underlying_index_id])

    def compute_fingerprint(self) -> str:
        """Compute a deterministic SHA-256 hash based on economic identity fields."""
        def _fmt(val):
            if val is None:
                return "NULL"
            if isinstance(val, Decimal):
                return str(val.normalize())
            if isinstance(val, date):
                return val.isoformat()
            return str(val)

        parts = [
            _fmt(self.exchange),
            _fmt(self.underlying_stock_id),
            _fmt(self.underlying_index_id),
            _fmt(self.instrument_type),
            _fmt(self.expiry_date),
            _fmt(self.strike_price),
            _fmt(self.option_type),
        ]
        canonical = "|".join(parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def __repr__(self) -> str:
        return (
            f"<DerivativeContract id={self.id} symbol={self.trading_symbol} "
            f"type={self.instrument_type} expiry={self.expiry_date} strike={self.strike_price}>"
        )


@event.listens_for(DerivativeContract, "before_insert")
def _set_derivative_contract_idempotency_hash(mapper, connection, target: DerivativeContract):
    if not target.idempotency_hash:
        target.idempotency_hash = target.compute_fingerprint()


class DerivativeLotSizeHistory(TimestampMixin, Base):
    """Historical tracking of derivative lot size modifications for underlyings."""

    __tablename__ = "derivative_lot_size_histories"
    __table_args__ = (
        CheckConstraint(
            "(underlying_stock_id IS NOT NULL AND underlying_index_id IS NULL) OR "
            "(underlying_stock_id IS NULL AND underlying_index_id IS NOT NULL)",
            name="ck_derivative_lot_size_histories_exactly_one_underlying",
        ),
        CheckConstraint(
            "lot_size > 0",
            name="ck_derivative_lot_size_histories_lot_size_positive",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_derivative_lot_size_histories_validity_range",
        ),
        Index("ix_derivative_lot_size_stock_from", "underlying_stock_id", "effective_from"),
        Index("ix_derivative_lot_size_index_from", "underlying_index_id", "effective_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    underlying_stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=True
    )
    underlying_index_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_indices.id", ondelete="CASCADE"), nullable=True
    )

    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    available_at: Mapped[date] = mapped_column(Date, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)

    underlying_stock = relationship("Stock", foreign_keys=[underlying_stock_id])
    underlying_index = relationship("MarketIndex", foreign_keys=[underlying_index_id])

    def __repr__(self) -> str:
        return (
            f"<DerivativeLotSizeHistory id={self.id} lot_size={self.lot_size} "
            f"from={self.effective_from} to={self.effective_to}>"
        )


class DerivativeContractMetadataHistory(TimestampMixin, Base):
    """Per-contract, effective-dated metadata history.

    G.11-OPT-14E-A.

    Mutable metadata (lot_size, tick_size, freeze_quantity, minimum_lot, is_weekly)
    is recorded here as an append-only timeline owned by one canonical
    derivative_contracts row. Economic contract identity (the fingerprint) stays
    in DerivativeContract and is never rewritten or duplicated because of a
    metadata change.

    The table is intentionally separate from DerivativeLotSizeHistory: that table
    is underlying-scoped and was created earlier; this table is per-contract and
    carries the full metadata set required by 14E.
    """

    __tablename__ = "derivative_contract_metadata_histories"
    __table_args__ = (
        CheckConstraint(
            "lot_size > 0",
            name="ck_derivative_contract_metadata_histories_lot_size_positive",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_derivative_contract_metadata_histories_validity_range",
        ),
        CheckConstraint(
            "effective_from <= available_at",
            name="ck_derivative_contract_metadata_histories_available_at_geq",
        ),
        Index(
            "ix_derivative_contract_metadata_histories_contract_id_from",
            "contract_id",
            "effective_from",
        ),
        Index(
            "ix_derivative_contract_metadata_histories_available_at",
            "available_at",
        ),
        Index(
            "ix_derivative_contract_metadata_histories_source_name",
            "source_name",
        ),
    )

    # (The per-contract metadata timeline is append-only: no fact is ever
    # rewritten by 14E-B; ``effective_to`` stays NULL and as-of resolution is
    # deterministic by ``effective_from DESC``.)

    id: Mapped[int] = mapped_column(primary_key=True)

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("derivative_contracts.id", ondelete="CASCADE"), nullable=False
    )
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_size: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 4), nullable=True
    )
    freeze_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_lot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_weekly: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    available_at: Mapped[date] = mapped_column(Date, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_snapshot_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    contract = relationship("DerivativeContract", backref="metadata_history")

    def __repr__(self) -> str:
        return (
            f"<DerivativeContractMetadataHistory id={self.id} contract_id={self.contract_id} "
            f"lot_size={self.lot_size} effective_from={self.effective_from}>"
        )


class DerivativeContractSyncRun(TimestampMixin, Base):
    """Durable audit record of one contract-master reconciliation run.

    G.11-OPT-14E-B.

    Every reconciliation (dry-run, committed, aborted, or clean-with-no-writes)
    records exactly one row so that "which snapshot produced this contract state?"
    is answerable from the database instead of from stdout. The row is written in
    the *same* transaction as the contract/metadata writes it describes, so a
    rolled-back write can never leave an audit row claiming a committed sync.

    ``dry_run`` and ``committed`` are independent facts:
      * ``dry_run=True``  -> ``committed`` is always False and ``status='DRY_RUN'``.
      * ``dry_run=False`` -> ``status`` is ``COMMITTED``, ``NO_WRITE_NEEDED`` or
        ``ABORTED``, and ``committed`` is True only for ``COMMITTED``.

    This table has no foreign key into ``derivative_contracts`` on purpose: the
    audit must survive independently of any contract lifecycle and must never
    cascade a contract deletion into the audit trail.
    """

    __tablename__ = "derivative_contract_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRY_RUN', 'COMMITTED', 'NO_WRITE_NEEDED', 'ABORTED')",
            name="ck_derivative_contract_sync_runs_status",
        ),
        CheckConstraint(
            "dry_run = false OR committed = false",
            name="ck_derivative_contract_sync_runs_dry_run_not_committed",
        ),
        Index(
            "ix_derivative_contract_sync_runs_underlying_as_of",
            "underlying",
            "as_of_date",
        ),
        Index("ix_derivative_contract_sync_runs_payload_hash", "payload_hash"),
        Index("ix_derivative_contract_sync_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    underlying_index_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    committed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Provenance of the reconciled payload (never invented; see service docs).
    provider_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_hash_method: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Counts by reconciliation outcome, exactly as classified.
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    newly_listed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_drift: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expired_absent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_while_unexpired: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stale: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflicts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_facts_written: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    abort_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    validation_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DerivativeContractSyncRun id={self.id} underlying={self.underlying} "
            f"as_of={self.as_of_date} status={self.status} dry_run={self.dry_run}>"
        )
