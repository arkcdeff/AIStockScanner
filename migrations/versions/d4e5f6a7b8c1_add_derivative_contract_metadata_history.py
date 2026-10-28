"""
G.11-OPT-14E-A — DerivativeContractMetadataHistory foundation.

Milestone: G.11-OPT-14E-A — Metadata History Foundation (Phase 1)

One additive, fully-reversible migration creates the per-contract,
effective-dated metadata history table that 14E uses to version mutable
contract metadata (lot_size, tick_size, freeze_quantity, minimum_lot,
is_weekly) without ever mutating a persisted economic contract row.

The table lives beside DerivativeLotSizeHistory without duplicating its
underlying-scoped purpose: the history table here is owned by one canonical
derivative_contracts row and supports the 14E-A as-of and latest lookup APIs.

No derivative_contracts row, option-chain table, or existing migration is
modified by this migration. Downgrade is reversible and removes exactly the
table it created.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c1"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "derivative_contract_metadata_histories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False),
        sa.Column("tick_size", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("freeze_quantity", sa.Integer(), nullable=True),
        sa.Column("minimum_lot", sa.Integer(), nullable=True),
        sa.Column("is_weekly", sa.Boolean(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("available_at", sa.Date(), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=True),
        sa.Column("source_reference", sa.String(length=256), nullable=True),
        sa.Column("provider_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(lot_size > 0)",
            name=op.f("ck_derivative_contract_metadata_histories_lot_size_positive"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_derivative_contract_metadata_histories_validity_range"),
        ),
        sa.CheckConstraint(
            "effective_from <= available_at",
            name=op.f("ck_derivative_contract_metadata_histories_available_at_geq"),
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            ["derivative_contracts.id"],
            name=op.f("fk_derivative_contract_metadata_histories_contract_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_derivative_contract_metadata_histories")
        ),
    )

    op.create_index(
        "ix_derivative_contract_metadata_histories_contract_id_from",
        "derivative_contract_metadata_histories",
        ["contract_id", "effective_from"],
        unique=False,
    )
    op.create_index(
        "ix_derivative_contract_metadata_histories_contract_id_latest",
        "derivative_contract_metadata_histories",
        ["contract_id", "effective_from"],
        unique=False,
    )
    op.create_index(
        "ix_derivative_contract_metadata_histories_available_at",
        "derivative_contract_metadata_histories",
        ["available_at"],
        unique=False,
    )
    op.create_index(
        "ix_derivative_contract_metadata_histories_source_name",
        "derivative_contract_metadata_histories",
        ["source_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_derivative_contract_metadata_histories_source_name",
        table_name="derivative_contract_metadata_histories",
    )
    op.drop_index(
        "ix_derivative_contract_metadata_histories_available_at",
        table_name="derivative_contract_metadata_histories",
    )
    op.drop_index(
        "ix_derivative_contract_metadata_histories_contract_id_latest",
        table_name="derivative_contract_metadata_histories",
    )
    op.drop_index(
        "ix_derivative_contract_metadata_histories_contract_id_from",
        table_name="derivative_contract_metadata_histories",
    )
    op.drop_table("derivative_contract_metadata_histories")
