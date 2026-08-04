"""add technical analysis table

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-08-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eod_pipeline_runs",
        sa.Column("analytics_calculated", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_table(
        "technical_analysis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("trend_score", sa.Integer(), nullable=False),
        sa.Column("momentum_score", sa.Integer(), nullable=False),
        sa.Column("volume_score", sa.Integer(), nullable=False),
        sa.Column("delivery_score", sa.Integer(), nullable=False),
        sa.Column("volatility_score", sa.Integer(), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("support", sa.Numeric(18, 4), nullable=True),
        sa.Column("resistance", sa.Numeric(18, 4), nullable=True),
        sa.Column("pivot", sa.Numeric(18, 4), nullable=True),
        sa.Column("pivot_r1", sa.Numeric(18, 4), nullable=True),
        sa.Column("pivot_r2", sa.Numeric(18, 4), nullable=True),
        sa.Column("pivot_r3", sa.Numeric(18, 4), nullable=True),
        sa.Column("pivot_s1", sa.Numeric(18, 4), nullable=True),
        sa.Column("pivot_s2", sa.Numeric(18, 4), nullable=True),
        sa.Column("pivot_s3", sa.Numeric(18, 4), nullable=True),
        sa.Column("fib_pivot", sa.Numeric(18, 4), nullable=True),
        sa.Column("fib_r1", sa.Numeric(18, 4), nullable=True),
        sa.Column("fib_r2", sa.Numeric(18, 4), nullable=True),
        sa.Column("fib_r3", sa.Numeric(18, 4), nullable=True),
        sa.Column("fib_s1", sa.Numeric(18, 4), nullable=True),
        sa.Column("fib_s2", sa.Numeric(18, 4), nullable=True),
        sa.Column("fib_s3", sa.Numeric(18, 4), nullable=True),
        sa.Column("relative_volume", sa.Float(), nullable=True),
        sa.Column("delivery_strength", sa.Float(), nullable=True),
        sa.Column("trend_rating", sa.String(length=24), nullable=True),
        sa.Column("momentum_rating", sa.String(length=24), nullable=True),
        sa.Column("volatility_rating", sa.String(length=24), nullable=True),
        sa.Column("is_trending", sa.Boolean(), nullable=False),
        sa.Column("is_breakout", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["stock_id"], ["stocks.id"], name=op.f("fk_technical_analysis_stock_id_stocks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_technical_analysis")),
        sa.UniqueConstraint(
            "stock_id", "trade_date", name="uq_technical_analysis_stock_date"
        ),
    )
    op.create_index(op.f("ix_technical_analysis_stock_id"), "technical_analysis", ["stock_id"])
    op.create_index(op.f("ix_technical_analysis_trade_date"), "technical_analysis", ["trade_date"])


def downgrade() -> None:
    op.drop_index(op.f("ix_technical_analysis_trade_date"), table_name="technical_analysis")
    op.drop_index(op.f("ix_technical_analysis_stock_id"), table_name="technical_analysis")
    op.drop_table("technical_analysis")
    op.drop_column("eod_pipeline_runs", "analytics_calculated")
