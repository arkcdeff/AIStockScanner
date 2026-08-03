"""add eod pipeline runs table

Revision ID: d1e2f3a4b5c6
Revises: bdc596b3589a
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "bdc596b3589a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eod_pipeline_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_stage", sa.String(length=40), nullable=True),
        sa.Column("import_job_id", sa.Integer(), nullable=True),
        sa.Column("imported_rows", sa.Integer(), nullable=False),
        sa.Column("inserted_rows", sa.Integer(), nullable=False),
        sa.Column("updated_rows", sa.Integer(), nullable=False),
        sa.Column("rejected_rows", sa.Integer(), nullable=False),
        sa.Column("stocks_processed", sa.Integer(), nullable=False),
        sa.Column("indicators_calculated", sa.Integer(), nullable=False),
        sa.Column("scanners_executed", sa.Integer(), nullable=False),
        sa.Column("scanner_matches", sa.Integer(), nullable=False),
        sa.Column("failed_stage", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["import_job_id"], ["import_jobs.id"], name=op.f("fk_eod_pipeline_runs_import_job_id_import_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eod_pipeline_runs")),
    )
    op.create_index(op.f("ix_eod_pipeline_runs_started_at"), "eod_pipeline_runs", ["started_at"])
    op.create_index(op.f("ix_eod_pipeline_runs_status"), "eod_pipeline_runs", ["status"])
    op.create_index(op.f("ix_eod_pipeline_runs_trade_date"), "eod_pipeline_runs", ["trade_date"])


def downgrade() -> None:
    op.drop_index(op.f("ix_eod_pipeline_runs_trade_date"), table_name="eod_pipeline_runs")
    op.drop_index(op.f("ix_eod_pipeline_runs_status"), table_name="eod_pipeline_runs")
    op.drop_index(op.f("ix_eod_pipeline_runs_started_at"), table_name="eod_pipeline_runs")
    op.drop_table("eod_pipeline_runs")
