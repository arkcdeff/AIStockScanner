"""market data foundation

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sectors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sectors")),
        sa.UniqueConstraint("name", name=op.f("uq_sectors_name")),
    )
    op.create_index(op.f("ix_sectors_name"), "sectors", ["name"])

    op.add_column("stocks", sa.Column("series", sa.String(length=8), nullable=True))
    op.add_column("stocks", sa.Column("sector_id", sa.Integer(), nullable=True))
    op.add_column("stocks", sa.Column("industry", sa.String(length=120), nullable=True))
    op.create_foreign_key(
        op.f("fk_stocks_sector_id_sectors"), "stocks", "sectors", ["sector_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index(op.f("ix_stocks_series"), "stocks", ["series"])
    op.create_index(op.f("ix_stocks_sector_id"), "stocks", ["sector_id"])
    op.create_index(op.f("ix_stocks_industry"), "stocks", ["industry"])

    # Replace the old symbol-only uniqueness with exchange + symbol.
    op.drop_constraint(op.f("uq_stocks_symbol"), "stocks", type_="unique")
    op.create_unique_constraint("uq_stock_exchange_symbol", "stocks", ["exchange", "symbol"])
    op.drop_column("stocks", "sector")

    op.add_column("daily_prices", sa.Column("previous_close", sa.Numeric(18, 4), nullable=True))
    op.add_column("daily_prices", sa.Column("last_price", sa.Numeric(18, 4), nullable=True))
    op.add_column("daily_prices", sa.Column("turnover", sa.Numeric(24, 4), nullable=True))
    op.add_column("daily_prices", sa.Column("trade_count", sa.BigInteger(), nullable=True))
    op.create_index("ix_daily_prices_date_stock", "daily_prices", ["trade_date", "stock_id"])

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), nullable=False),
        sa.Column("rows_updated", sa.Integer(), nullable=False),
        sa.Column("rows_rejected", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )
    op.create_index(op.f("ix_import_jobs_source"), "import_jobs", ["source"])
    op.create_index(op.f("ix_import_jobs_status"), "import_jobs", ["status"])
    op.create_index(op.f("ix_import_jobs_trade_date"), "import_jobs", ["trade_date"])


def downgrade() -> None:
    op.drop_index(op.f("ix_import_jobs_trade_date"), table_name="import_jobs")
    op.drop_index(op.f("ix_import_jobs_status"), table_name="import_jobs")
    op.drop_index(op.f("ix_import_jobs_source"), table_name="import_jobs")
    op.drop_table("import_jobs")

    op.drop_index("ix_daily_prices_date_stock", table_name="daily_prices")
    op.drop_column("daily_prices", "trade_count")
    op.drop_column("daily_prices", "turnover")
    op.drop_column("daily_prices", "last_price")
    op.drop_column("daily_prices", "previous_close")

    op.add_column("stocks", sa.Column("sector", sa.String(length=100), nullable=True))
    op.drop_constraint("uq_stock_exchange_symbol", "stocks", type_="unique")
    op.create_unique_constraint(op.f("uq_stocks_symbol"), "stocks", ["symbol"])
    op.drop_index(op.f("ix_stocks_industry"), table_name="stocks")
    op.drop_index(op.f("ix_stocks_sector_id"), table_name="stocks")
    op.drop_index(op.f("ix_stocks_series"), table_name="stocks")
    op.drop_constraint(op.f("fk_stocks_sector_id_sectors"), "stocks", type_="foreignkey")
    op.drop_column("stocks", "industry")
    op.drop_column("stocks", "sector_id")
    op.drop_column("stocks", "series")

    op.drop_index(op.f("ix_sectors_name"), table_name="sectors")
    op.drop_table("sectors")
