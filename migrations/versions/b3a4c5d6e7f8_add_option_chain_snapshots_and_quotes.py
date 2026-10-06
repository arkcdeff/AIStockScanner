"""Add option_chain_snapshots and range-partitioned option_chain_quotes tables.

Milestone: G.11-OPT-1 — Option-Chain Data Foundation (Step 1)
Authority: docs/G11-OPT1-ARCHITECTURE.md (SPEC-R2), docs/G11-OPT1-IMPLEMENTATION-SPECIFICATION.md

Revision ID: b3a4c5d6e7f8
Revises: f1b9c8d7e6a5
Create Date: 2026-09-23 15:30:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b3a4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f1b9c8d7e6a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create option_chain_snapshots table
    op.create_table(
        "option_chain_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("underlying_index_id", sa.Integer(), nullable=False),
        sa.Column("chain_scope", sa.String(length=32), nullable=False),
        sa.Column("expiry_scope", sa.Date(), nullable=True),
        sa.Column("spot_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("market_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingestion_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("source_reference", sa.String(length=256), nullable=True),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=32), nullable=False),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("quality_status", sa.String(length=32), server_default="VALID", nullable=False),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_truncated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("expected_strike_count", sa.Integer(), nullable=True),
        sa.Column("observed_strike_count", sa.Integer(), nullable=True),
        sa.Column("near_futures_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("near_futures_contract_id", sa.Integer(), nullable=True),
        sa.Column("calculation_spot_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("risk_free_rate_used", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("dividend_yield_used", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("time_to_expiry_years", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("time_to_expiry_convention", sa.String(length=32), nullable=True),
        sa.Column("pricing_model_version", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chain_scope IN ('ALL_SUPPORTED_EXPIRIES', 'SINGLE_EXPIRY')",
            name="ck_option_chain_snapshots_chain_scope",
        ),
        sa.CheckConstraint(
            "(chain_scope = 'ALL_SUPPORTED_EXPIRIES' AND expiry_scope IS NULL) OR "
            "(chain_scope = 'SINGLE_EXPIRY' AND expiry_scope IS NOT NULL)",
            name="ck_option_chain_snapshots_expiry_scope_match",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_option_chain_snapshots_revision_pos"),
        sa.CheckConstraint(
            "quality_status IN ('VALID', 'WARNING', 'ACCEPT_WITH_FLAG', 'REJECTED')",
            name="ck_option_chain_snapshots_quality_status",
        ),
        sa.ForeignKeyConstraint(
            ["underlying_index_id"],
            ["market_indices.id"],
            name=op.f("fk_option_chain_snapshots_underlying_index_id_market_indices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["near_futures_contract_id"],
            ["derivative_contracts.id"],
            name=op.f("fk_option_chain_snapshots_near_futures_contract_id_derivative_contracts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_option_chain_snapshots")),
    )

    # Partial unique indexes for snapshot logical identity
    op.create_index(
        "uq_opt_snapshots_all",
        "option_chain_snapshots",
        ["underlying_index_id", "market_timestamp", "source_name", "revision"],
        unique=True,
        postgresql_where=sa.text("chain_scope = 'ALL_SUPPORTED_EXPIRIES'"),
    )
    op.create_index(
        "uq_opt_snapshots_single",
        "option_chain_snapshots",
        ["underlying_index_id", "expiry_scope", "market_timestamp", "source_name", "revision"],
        unique=True,
        postgresql_where=sa.text("chain_scope = 'SINGLE_EXPIRY'"),
    )
    op.create_index(
        "ix_opt_snapshots_underlying_mkt_ts",
        "option_chain_snapshots",
        ["underlying_index_id", "market_timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_opt_snapshots_underlying_avail_at",
        "option_chain_snapshots",
        ["underlying_index_id", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_opt_snapshots_batch_id",
        "option_chain_snapshots",
        ["batch_id"],
        unique=False,
    )

    # 2. Create range-partitioned option_chain_quotes table
    op.create_table(
        "option_chain_quotes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("strike_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("option_type", sa.String(length=2), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("ltp", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("bid", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("ask", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("bid_qty", sa.BigInteger(), nullable=True),
        sa.Column("ask_qty", sa.BigInteger(), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("open_interest", sa.BigInteger(), nullable=True),
        sa.Column("source_change_in_oi", sa.BigInteger(), nullable=True),
        sa.Column("interval_change_in_oi", sa.BigInteger(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("previous_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("session_change_in_oi", sa.BigInteger(), nullable=True),
        sa.Column("source_iv", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("source_delta", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("source_gamma", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("source_theta", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("source_vega", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("source_rho", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("calculated_iv", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("calculated_delta", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("calculated_gamma", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("calculated_theta", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("calculated_vega", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("calculated_rho", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", "trade_date", name=op.f("pk_option_chain_quotes")),
        sa.UniqueConstraint(
            "snapshot_id",
            "contract_id",
            "trade_date",
            name="uq_option_chain_quotes_snapshot_contract_date",
        ),
        sa.CheckConstraint(
            "option_type IN ('CE', 'PE')", name="ck_option_chain_quotes_option_type"
        ),
        sa.CheckConstraint("strike_price > 0", name="ck_option_chain_quotes_strike_positive"),
        sa.CheckConstraint(
            "volume IS NULL OR volume >= 0", name="ck_option_chain_quotes_volume_non_negative"
        ),
        sa.CheckConstraint(
            "open_interest IS NULL OR open_interest >= 0",
            name="ck_option_chain_quotes_open_interest_non_negative",
        ),
        sa.CheckConstraint(
            "(bid IS NULL OR ask IS NULL) OR (bid <= ask)",
            name="ck_option_chain_quotes_bid_ask_valid",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["option_chain_snapshots.id"],
            name=op.f("fk_option_chain_quotes_snapshot_id_option_chain_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["derivative_contracts.id"],
            name=op.f("fk_option_chain_quotes_contract_id_derivative_contracts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["previous_snapshot_id"],
            ["option_chain_snapshots.id"],
            name=op.f("fk_option_chain_quotes_previous_snapshot_id_option_chain_snapshots"),
            ondelete="SET NULL",
        ),
        postgresql_partition_by="RANGE (trade_date)",
    )

    # 3. Create initial monthly partitions (current month 2026-09 + next 3 calendar months)
    op.execute("""
        CREATE TABLE IF NOT EXISTS option_chain_quotes_2026_09
        PARTITION OF option_chain_quotes
        FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS option_chain_quotes_2026_10
        PARTITION OF option_chain_quotes
        FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS option_chain_quotes_2026_11
        PARTITION OF option_chain_quotes
        FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS option_chain_quotes_2026_12
        PARTITION OF option_chain_quotes
        FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
    """)

    # 4. Create supporting indexes on option_chain_quotes
    op.create_index(
        "ix_opt_quotes_snapshot_date_contract",
        "option_chain_quotes",
        ["snapshot_id", "trade_date", "contract_id"],
        unique=False,
    )
    op.create_index(
        "ix_opt_quotes_contract_trade_date",
        "option_chain_quotes",
        ["contract_id", "trade_date"],
        unique=False,
    )
    op.create_index(
        "ix_opt_quotes_trade_date_snapshot",
        "option_chain_quotes",
        ["trade_date", "snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    # 1. Drop supporting indexes on partitioned quote table
    op.drop_index("ix_opt_quotes_trade_date_snapshot", table_name="option_chain_quotes")
    op.drop_index("ix_opt_quotes_contract_trade_date", table_name="option_chain_quotes")
    op.drop_index("ix_opt_quotes_snapshot_date_contract", table_name="option_chain_quotes")

    # 2. Drop child quote partitions explicitly
    op.execute("DROP TABLE IF EXISTS option_chain_quotes_2026_12")
    op.execute("DROP TABLE IF EXISTS option_chain_quotes_2026_11")
    op.execute("DROP TABLE IF EXISTS option_chain_quotes_2026_10")
    op.execute("DROP TABLE IF EXISTS option_chain_quotes_2026_09")

    # 3. Drop option_chain_quotes parent table
    op.drop_table("option_chain_quotes")

    # 4. Drop option_chain_snapshots indexes
    op.drop_index("ix_opt_snapshots_batch_id", table_name="option_chain_snapshots")
    op.drop_index("ix_opt_snapshots_underlying_avail_at", table_name="option_chain_snapshots")
    op.drop_index("ix_opt_snapshots_underlying_mkt_ts", table_name="option_chain_snapshots")
    op.drop_index("uq_opt_snapshots_single", table_name="option_chain_snapshots")
    op.drop_index("uq_opt_snapshots_all", table_name="option_chain_snapshots")

    # 5. Drop option_chain_snapshots table
    op.drop_table("option_chain_snapshots")
