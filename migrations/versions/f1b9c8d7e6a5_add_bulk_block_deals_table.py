"""Add bulk_block_deals table for G.10 Cash Market Bulk & Block Deals.

Revision ID: f1b9c8d7e6a5
Revises: 478d723971bc
Create Date: 2026-09-10 12:30:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f1b9c8d7e6a5'
down_revision: str | Sequence[str] | None = '478d723971bc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'bulk_block_deals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('trade_date', sa.Date(), nullable=False),
        sa.Column('deal_type', sa.String(length=8), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False),
        sa.Column('security_name', sa.String(length=255), nullable=False),
        sa.Column('client_name', sa.String(length=255), nullable=False),
        sa.Column('buy_sell', sa.String(length=4), nullable=False),
        sa.Column('quantity', sa.BigInteger(), nullable=False),
        sa.Column('trade_price', sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column('deal_value', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('remarks', sa.String(length=255), nullable=True),
        sa.Column('stock_id', sa.Integer(), nullable=True),
        sa.Column('source_line_index', sa.Integer(), nullable=False),
        sa.Column('deal_row_hash', sa.String(length=64), nullable=False),
        sa.Column('is_current', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('supersedes_id', sa.BigInteger(), nullable=True),
        sa.Column('available_at', sa.Date(), nullable=False),
        sa.Column('source_name', sa.String(length=64), server_default='NSE_INDIA', nullable=False),
        sa.Column('source_reference', sa.String(length=255), nullable=False),
        sa.Column('source_checksum', sa.String(length=64), nullable=False),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("deal_type IN ('BULK', 'BLOCK')", name='ck_bulk_block_deals_deal_type'),
        sa.CheckConstraint("buy_sell IN ('BUY', 'SELL')", name='ck_bulk_block_deals_buy_sell'),
        sa.CheckConstraint('quantity > 0', name='ck_bulk_block_deals_quantity_pos'),
        sa.CheckConstraint('trade_price > 0', name='ck_bulk_block_deals_price_pos'),
        sa.CheckConstraint('deal_value > 0', name='ck_bulk_block_deals_value_pos'),
        sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], name=op.f('fk_bulk_block_deals_stock_id_stocks'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['supersedes_id'], ['bulk_block_deals.id'], name=op.f('fk_bulk_block_deals_supersedes_id_bulk_block_deals'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bulk_block_deals')),
    )
    op.create_index(
        'uq_bulk_block_deals_source_line',
        'bulk_block_deals',
        ['source_reference', 'source_line_index'],
        unique=True,
        postgresql_where=sa.text('is_current = TRUE'),
    )
    op.create_index('ix_bulk_block_deals_stock_date', 'bulk_block_deals', ['stock_id', 'trade_date'], unique=False)
    op.create_index('ix_bulk_block_deals_symbol_date', 'bulk_block_deals', ['symbol', 'trade_date'], unique=False)
    op.create_index('ix_bulk_block_deals_date_type', 'bulk_block_deals', ['trade_date', 'deal_type'], unique=False)
    op.create_index('ix_bulk_block_deals_client', 'bulk_block_deals', ['client_name'], unique=False)
    op.create_index('ix_bulk_block_deals_available_at', 'bulk_block_deals', ['available_at'], unique=False)
    op.create_index(
        'ix_bulk_block_deals_current_type_date',
        'bulk_block_deals',
        ['deal_type', 'trade_date'],
        unique=False,
        postgresql_where=sa.text('is_current = TRUE'),
    )


def downgrade() -> None:
    op.drop_index('ix_bulk_block_deals_current_type_date', table_name='bulk_block_deals')
    op.drop_index('ix_bulk_block_deals_available_at', table_name='bulk_block_deals')
    op.drop_index('ix_bulk_block_deals_client', table_name='bulk_block_deals')
    op.drop_index('ix_bulk_block_deals_date_type', table_name='bulk_block_deals')
    op.drop_index('ix_bulk_block_deals_symbol_date', table_name='bulk_block_deals')
    op.drop_index('ix_bulk_block_deals_stock_date', table_name='bulk_block_deals')
    op.drop_index('uq_bulk_block_deals_source_line', table_name='bulk_block_deals')
    op.drop_table('bulk_block_deals')
