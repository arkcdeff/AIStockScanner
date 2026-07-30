"""create core tables

Revision ID: 0001
Revises:
Create Date: 2026-07-30
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_definitions")),
        sa.UniqueConstraint("code", name=op.f("uq_scan_definitions_code")),
    )
    op.create_index(op.f("ix_scan_definitions_category"), "scan_definitions", ["category"])
    op.create_index(op.f("ix_scan_definitions_code"), "scan_definitions", ["code"])
    op.create_index(op.f("ix_scan_definitions_is_active"), "scan_definitions", ["is_active"])

    op.create_table(
        "stocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("isin", sa.String(length=20), nullable=True),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stocks")),
        sa.UniqueConstraint("isin", name=op.f("uq_stocks_isin")),
        sa.UniqueConstraint("symbol", name=op.f("uq_stocks_symbol")),
    )
    op.create_index(op.f("ix_stocks_exchange"), "stocks", ["exchange"])
    op.create_index(op.f("ix_stocks_is_active"), "stocks", ["is_active"])
    op.create_index(op.f("ix_stocks_sector"), "stocks", ["sector"])
    op.create_index(op.f("ix_stocks_symbol"), "stocks", ["symbol"])

    op.create_table(
        "daily_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("delivery_quantity", sa.BigInteger(), nullable=True),
        sa.Column("delivery_percentage", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], name=op.f("fk_daily_prices_stock_id_stocks"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_prices")),
        sa.UniqueConstraint("stock_id", "trade_date", name="uq_daily_price_stock_date"),
    )
    op.create_index(op.f("ix_daily_prices_stock_id"), "daily_prices", ["stock_id"])
    op.create_index(op.f("ix_daily_prices_trade_date"), "daily_prices", ["trade_date"])

    op.create_table(
        "scan_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_definition_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("scan_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["scan_definition_id"], ["scan_definitions.id"], name=op.f("fk_scan_results_scan_definition_id_scan_definitions"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], name=op.f("fk_scan_results_stock_id_stocks"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_results")),
        sa.UniqueConstraint("scan_definition_id", "stock_id", "scan_date", name="uq_scan_result_scan_stock_date"),
    )
    op.create_index(op.f("ix_scan_results_scan_date"), "scan_results", ["scan_date"])
    op.create_index(op.f("ix_scan_results_scan_definition_id"), "scan_results", ["scan_definition_id"])
    op.create_index(op.f("ix_scan_results_stock_id"), "scan_results", ["stock_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_scan_results_stock_id"), table_name="scan_results")
    op.drop_index(op.f("ix_scan_results_scan_definition_id"), table_name="scan_results")
    op.drop_index(op.f("ix_scan_results_scan_date"), table_name="scan_results")
    op.drop_table("scan_results")
    op.drop_index(op.f("ix_daily_prices_trade_date"), table_name="daily_prices")
    op.drop_index(op.f("ix_daily_prices_stock_id"), table_name="daily_prices")
    op.drop_table("daily_prices")
    op.drop_index(op.f("ix_stocks_symbol"), table_name="stocks")
    op.drop_index(op.f("ix_stocks_sector"), table_name="stocks")
    op.drop_index(op.f("ix_stocks_is_active"), table_name="stocks")
    op.drop_index(op.f("ix_stocks_exchange"), table_name="stocks")
    op.drop_table("stocks")
    op.drop_index(op.f("ix_scan_definitions_is_active"), table_name="scan_definitions")
    op.drop_index(op.f("ix_scan_definitions_code"), table_name="scan_definitions")
    op.drop_index(op.f("ix_scan_definitions_category"), table_name="scan_definitions")
    op.drop_table("scan_definitions")
