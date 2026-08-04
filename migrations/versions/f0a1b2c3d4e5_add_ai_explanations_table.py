"""add ai_explanations table

Revision ID: f0a1b2c3d4e5
Revises: e1f2a3b4c5d6
Create Date: 2026-08-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_explanations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["stock_id"], ["stocks.id"], name=op.f("fk_ai_explanations_stock_id_stocks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_explanations")),
        sa.UniqueConstraint(
            "stock_id", "trade_date", "provider", name="uq_ai_explanation_stock_date_provider"
        ),
    )
    op.create_index(op.f("ix_ai_explanations_stock_id"), "ai_explanations", ["stock_id"])
    op.create_index(op.f("ix_ai_explanations_trade_date"), "ai_explanations", ["trade_date"])
    op.create_index(op.f("ix_ai_explanations_prompt_hash"), "ai_explanations", ["prompt_hash"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_explanations_prompt_hash"), table_name="ai_explanations")
    op.drop_index(op.f("ix_ai_explanations_trade_date"), table_name="ai_explanations")
    op.drop_index(op.f("ix_ai_explanations_stock_id"), table_name="ai_explanations")
    op.drop_table("ai_explanations")
