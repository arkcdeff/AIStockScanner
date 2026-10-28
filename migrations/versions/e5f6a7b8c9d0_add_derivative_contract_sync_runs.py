"""Add derivative_contract_sync_runs (contract reconciliation run audit).

Milestone: G.11-OPT-14E-B — Contract Reconciliation & Lifecycle Engine

One additive, fully-reversible table makes a contract-master reconciliation
auditable in-database: which underlying, which publication date, provider source,
payload hash (+ how that hash was computed), dry-run vs committed, the counts by
reconciliation outcome, and the abort reason if the payload failed closed.

Nothing else is touched. No existing table, column, index, constraint or row is
altered; ``derivative_contracts``, ``derivative_lot_size_histories``,
``derivative_contract_metadata_histories`` and every option-chain / derivative
observation table are left exactly as they are. Downgrade removes only the table
this migration created.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c1
Create Date: 2026-10-07 15:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "derivative_contract_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("underlying", sa.String(length=32), nullable=False),
        sa.Column("underlying_index_id", sa.Integer(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "dry_run", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "committed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("provider_source", sa.String(length=64), nullable=True),
        sa.Column("source_name", sa.String(length=64), nullable=True),
        sa.Column("source_reference", sa.String(length=256), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("payload_hash_method", sa.String(length=64), nullable=True),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("newly_listed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metadata_changed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metadata_drift", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expired_absent", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "missing_while_unexpired", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("stale", sa.Integer(), server_default="0", nullable=False),
        sa.Column("conflicts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "metadata_facts_written", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("abort_reason", sa.String(length=256), nullable=True),
        sa.Column("validation_summary", sa.JSON(), nullable=True),
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
            "status IN ('DRY_RUN', 'COMMITTED', 'NO_WRITE_NEEDED', 'ABORTED')",
            name=op.f("ck_derivative_contract_sync_runs_status"),
        ),
        sa.CheckConstraint(
            "dry_run = false OR committed = false",
            name=op.f("ck_derivative_contract_sync_runs_dry_run_not_committed"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_derivative_contract_sync_runs")),
    )

    op.create_index(
        "ix_derivative_contract_sync_runs_underlying_as_of",
        "derivative_contract_sync_runs",
        ["underlying", "as_of_date"],
        unique=False,
    )
    op.create_index(
        "ix_derivative_contract_sync_runs_payload_hash",
        "derivative_contract_sync_runs",
        ["payload_hash"],
        unique=False,
    )
    op.create_index(
        "ix_derivative_contract_sync_runs_status",
        "derivative_contract_sync_runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_derivative_contract_sync_runs_status",
        table_name="derivative_contract_sync_runs",
    )
    op.drop_index(
        "ix_derivative_contract_sync_runs_payload_hash",
        table_name="derivative_contract_sync_runs",
    )
    op.drop_index(
        "ix_derivative_contract_sync_runs_underlying_as_of",
        table_name="derivative_contract_sync_runs",
    )
    op.drop_table("derivative_contract_sync_runs")
