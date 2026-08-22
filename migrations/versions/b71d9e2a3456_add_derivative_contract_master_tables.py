"""add_derivative_contract_master_tables

Revision ID: b71d9e2a3456
Revises: ac9c6a6767be
Create Date: 2026-08-19 18:59:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b71d9e2a3456'
down_revision: str | None = 'ac9c6a6767be'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create derivative_contracts table
    op.create_table(
        'derivative_contracts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('exchange', sa.String(length=16), server_default='NSE', nullable=False),
        sa.Column('segment', sa.String(length=16), server_default='NFO', nullable=False),
        sa.Column('instrument_type', sa.String(length=32), nullable=False),
        sa.Column('underlying_stock_id', sa.Integer(), nullable=True),
        sa.Column('underlying_index_id', sa.Integer(), nullable=True),
        sa.Column('trading_symbol', sa.String(length=64), nullable=False),
        sa.Column('source_contract_id', sa.String(length=64), nullable=True),
        sa.Column('expiry_date', sa.Date(), nullable=False),
        sa.Column('strike_price', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('option_type', sa.String(length=8), nullable=True),
        sa.Column('lot_size', sa.Integer(), nullable=False),
        sa.Column('tick_size', sa.Numeric(precision=18, scale=4), server_default='0.05', nullable=True),
        sa.Column('contract_start_date', sa.Date(), nullable=True),
        sa.Column('last_trading_date', sa.Date(), nullable=True),
        sa.Column('lifecycle_status', sa.String(length=32), server_default='ACTIVE', nullable=False),
        sa.Column('idempotency_hash', sa.String(length=64), nullable=False),
        sa.Column('available_at', sa.Date(), nullable=False),
        sa.Column('source_name', sa.String(length=64), nullable=True),
        sa.Column('source_reference', sa.String(length=256), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "(underlying_stock_id IS NOT NULL AND underlying_index_id IS NULL) OR "
            "(underlying_stock_id IS NULL AND underlying_index_id IS NOT NULL)",
            name=op.f('ck_derivative_contracts_exactly_one_underlying')
        ),
        sa.CheckConstraint('lot_size > 0', name=op.f('ck_derivative_contracts_lot_size_positive')),
        sa.CheckConstraint(
            "instrument_type IN ('FUTURE', 'OPTION')",
            name=op.f('ck_derivative_contracts_instrument_type')
        ),
        sa.CheckConstraint(
            "(instrument_type = 'FUTURE' AND strike_price IS NULL AND option_type IS NULL) OR "
            "(instrument_type = 'OPTION' AND strike_price IS NOT NULL AND strike_price > 0 AND option_type IN ('CE', 'PE'))",
            name=op.f('ck_derivative_contracts_instrument_invariants')
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('ACTIVE', 'EXPIRED', 'INACTIVE', 'UNKNOWN')",
            name=op.f('ck_derivative_contracts_lifecycle_status')
        ),
        sa.ForeignKeyConstraint(['underlying_stock_id'], ['stocks.id'], name=op.f('fk_derivative_contracts_underlying_stock_id_stocks'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['underlying_index_id'], ['market_indices.id'], name=op.f('fk_derivative_contracts_underlying_index_id_market_indices'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_derivative_contracts')),
        sa.UniqueConstraint('idempotency_hash', name=op.f('uq_derivative_contracts_idempotency_hash'))
    )

    op.create_index('ix_derivative_contracts_available_at', 'derivative_contracts', ['available_at'], unique=False)
    op.create_index('ix_derivative_contracts_expiry_date', 'derivative_contracts', ['expiry_date'], unique=False)
    op.create_index('ix_derivative_contracts_instrument_type', 'derivative_contracts', ['instrument_type'], unique=False)
    op.create_index('ix_derivative_contracts_source_contract_id', 'derivative_contracts', ['source_contract_id'], unique=False)
    op.create_index('ix_derivative_contracts_trading_symbol', 'derivative_contracts', ['trading_symbol'], unique=False)
    op.create_index('ix_derivative_contracts_underlying_index', 'derivative_contracts', ['underlying_index_id'], unique=False)
    op.create_index('ix_derivative_contracts_underlying_stock', 'derivative_contracts', ['underlying_stock_id'], unique=False)

    # 2. Create derivative_lot_size_histories table
    op.create_table(
        'derivative_lot_size_histories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('underlying_stock_id', sa.Integer(), nullable=True),
        sa.Column('underlying_index_id', sa.Integer(), nullable=True),
        sa.Column('lot_size', sa.Integer(), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('available_at', sa.Date(), nullable=False),
        sa.Column('source_name', sa.String(length=64), nullable=True),
        sa.Column('source_reference', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "(underlying_stock_id IS NOT NULL AND underlying_index_id IS NULL) OR "
            "(underlying_stock_id IS NULL AND underlying_index_id IS NOT NULL)",
            name=op.f('ck_derivative_lot_size_histories_exactly_one_underlying')
        ),
        sa.CheckConstraint('lot_size > 0', name=op.f('ck_derivative_lot_size_histories_lot_size_positive')),
        sa.CheckConstraint('effective_to IS NULL OR effective_to >= effective_from', name=op.f('ck_derivative_lot_size_histories_validity_range')),
        sa.ForeignKeyConstraint(['underlying_stock_id'], ['stocks.id'], name=op.f('fk_derivative_lot_size_histories_underlying_stock_id_stocks'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['underlying_index_id'], ['market_indices.id'], name=op.f('fk_derivative_lot_size_histories_underlying_index_id_market_indices'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_derivative_lot_size_histories'))
    )

    op.create_index('ix_derivative_lot_size_index_from', 'derivative_lot_size_histories', ['underlying_index_id', 'effective_from'], unique=False)
    op.create_index('ix_derivative_lot_size_stock_from', 'derivative_lot_size_histories', ['underlying_stock_id', 'effective_from'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_derivative_lot_size_stock_from', table_name='derivative_lot_size_histories')
    op.drop_index('ix_derivative_lot_size_index_from', table_name='derivative_lot_size_histories')
    op.drop_table('derivative_lot_size_histories')

    op.drop_index('ix_derivative_contracts_underlying_stock', table_name='derivative_contracts')
    op.drop_index('ix_derivative_contracts_underlying_index', table_name='derivative_contracts')
    op.drop_index('ix_derivative_contracts_trading_symbol', table_name='derivative_contracts')
    op.drop_index('ix_derivative_contracts_source_contract_id', table_name='derivative_contracts')
    op.drop_index('ix_derivative_contracts_instrument_type', table_name='derivative_contracts')
    op.drop_index('ix_derivative_contracts_expiry_date', table_name='derivative_contracts')
    op.drop_index('ix_derivative_contracts_available_at', table_name='derivative_contracts')
    op.drop_table('derivative_contracts')
