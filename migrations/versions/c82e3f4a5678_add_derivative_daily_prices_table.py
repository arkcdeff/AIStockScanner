"""add_derivative_daily_prices_table — Sprint 19.1D-2

Revision ID: c82e3f4a5678
Revises: b71d9e2a3456
Create Date: 2026-08-19

Creates derivative_daily_prices table for storing time-series Facts for F&O contracts.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c82e3f4a5678"
down_revision: str | None = "b71d9e2a3456"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "derivative_daily_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("high", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("low", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("close", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("last_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("settlement_price", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("previous_close", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("volume_contracts", sa.BigInteger(), nullable=True),
        sa.Column("turnover", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("open_interest", sa.BigInteger(), nullable=True),
        sa.Column("change_in_open_interest", sa.BigInteger(), nullable=True),
        sa.Column("underlying_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("available_at", sa.Date(), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=True),
        sa.Column("source_reference", sa.String(length=256), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["derivative_contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contract_id", "trade_date", name="uq_derivative_daily_prices_contract_date"),
        sa.CheckConstraint(
            "open_interest IS NULL OR open_interest >= 0",
            name="ck_derivative_daily_prices_open_interest_non_negative",
        ),
        sa.CheckConstraint(
            "volume_contracts IS NULL OR volume_contracts >= 0",
            name="ck_derivative_daily_prices_volume_non_negative",
        ),
        sa.CheckConstraint(
            "turnover IS NULL OR turnover >= 0",
            name="ck_derivative_daily_prices_turnover_non_negative",
        ),
    )

    op.create_index("ix_derivative_daily_prices_contract_id", "derivative_daily_prices", ["contract_id"])
    op.create_index("ix_derivative_daily_prices_trade_date", "derivative_daily_prices", ["trade_date"])
    op.create_index("ix_derivative_daily_prices_available_at", "derivative_daily_prices", ["available_at"])
    op.create_index("ix_derivative_daily_prices_contract_date", "derivative_daily_prices", ["contract_id", "trade_date"])


def downgrade() -> None:
    op.drop_index("ix_derivative_daily_prices_contract_date", table_name="derivative_daily_prices")
    op.drop_index("ix_derivative_daily_prices_available_at", table_name="derivative_daily_prices")
    op.drop_index("ix_derivative_daily_prices_trade_date", table_name="derivative_daily_prices")
    op.drop_index("ix_derivative_daily_prices_contract_id", table_name="derivative_daily_prices")
    op.drop_table("derivative_daily_prices")
