"""add_derivative_participant_tables — Sprint 19.1D-7

Revision ID: e91f4a5678ab
Revises: c82e3f4a5678
Create Date: 2026-08-20

Creates derivative_participant_positions and fii_derivative_statistics tables for storing
participant-category position/volume facts and FII derivatives statistics.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e91f4a5678ab"
down_revision: str | None = "c82e3f4a5678"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create derivative_participant_positions table
    op.create_table(
        "derivative_participant_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("participant_type", sa.String(length=32), nullable=False),
        sa.Column("instrument_bucket", sa.String(length=32), nullable=False),
        sa.Column("long_contracts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("short_contracts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reported_total_long", sa.BigInteger(), nullable=True),
        sa.Column("reported_total_short", sa.BigInteger(), nullable=True),
        sa.Column("available_at", sa.Date(), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False, server_default="NSE_INDIA"),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_date",
            "dataset_type",
            "participant_type",
            "instrument_bucket",
            name="uq_derivative_participant_positions_identity",
        ),
        sa.CheckConstraint(
            "long_contracts >= 0",
            name="ck_participant_position_long_non_negative",
        ),
        sa.CheckConstraint(
            "short_contracts >= 0",
            name="ck_participant_position_short_non_negative",
        ),
    )
    op.create_index(
        "ix_derivative_participant_positions_report_date",
        "derivative_participant_positions",
        ["report_date"],
    )
    op.create_index(
        "ix_derivative_participant_positions_available_at",
        "derivative_participant_positions",
        ["available_at"],
    )
    op.create_index(
        "ix_participant_pos_lookup",
        "derivative_participant_positions",
        ["report_date", "dataset_type"],
    )

    # 2. Create fii_derivative_statistics table
    op.create_table(
        "fii_derivative_statistics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("instrument_category", sa.String(length=32), nullable=False),
        sa.Column("buy_contracts", sa.BigInteger(), nullable=True),
        sa.Column("buy_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("sell_contracts", sa.BigInteger(), nullable=True),
        sa.Column("sell_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("open_interest_contracts", sa.BigInteger(), nullable=True),
        sa.Column("open_interest_amount", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("available_at", sa.Date(), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False, server_default="NSE_INDIA"),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "report_date",
            "instrument_category",
            name="uq_fii_derivative_statistics_identity",
        ),
    )
    op.create_index(
        "ix_fii_derivative_statistics_report_date",
        "fii_derivative_statistics",
        ["report_date"],
    )
    op.create_index(
        "ix_fii_derivative_statistics_available_at",
        "fii_derivative_statistics",
        ["available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fii_derivative_statistics_available_at", table_name="fii_derivative_statistics")
    op.drop_index("ix_fii_derivative_statistics_report_date", table_name="fii_derivative_statistics")
    op.drop_table("fii_derivative_statistics")

    op.drop_index("ix_participant_pos_lookup", table_name="derivative_participant_positions")
    op.drop_index("ix_derivative_participant_positions_available_at", table_name="derivative_participant_positions")
    op.drop_index("ix_derivative_participant_positions_report_date", table_name="derivative_participant_positions")
    op.drop_table("derivative_participant_positions")
