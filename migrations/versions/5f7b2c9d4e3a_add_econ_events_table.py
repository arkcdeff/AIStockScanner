"""Add econ events table for Task 18.8

Revision ID: 5f7b2c9d4e3a
Revises: 25e6f159a646
Create Date: 2026-08-15 10:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '5f7b2c9d4e3a'
down_revision: str | Sequence[str] | None = '25e6f159a646'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'econ_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_code', sa.String(length=64), nullable=False),
        sa.Column('reference_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actual', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('forecast', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('previous', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('unit', sa.String(length=32), nullable=True),
        sa.Column('source_name', sa.String(length=128), nullable=True),
        sa.Column('source_reference', sa.String(length=256), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('freshness_status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_econ_events')),
        sa.UniqueConstraint('event_code', 'reference_time',
                            name='uq_econ_event_code_reference_time'),
    )
    op.create_index(op.f('ix_econ_events_event_code'), 'econ_events',
                    ['event_code'], unique=False)
    op.create_index(op.f('ix_econ_events_reference_time'), 'econ_events',
                    ['reference_time'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_econ_events_reference_time'),
                  table_name='econ_events')
    op.drop_index(op.f('ix_econ_events_event_code'), table_name='econ_events')
    op.drop_table('econ_events')
