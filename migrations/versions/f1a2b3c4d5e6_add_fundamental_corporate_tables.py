"""add fundamental corporate tables

Revision ID: f1a2b3c4d5e6
Revises: e91f4a5678ab
Create Date: 2026-08-20 17:42:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: str | None = 'e91f4a5678ab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. financial_statements
    op.create_table(
        'financial_statements',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('stock_id', sa.Integer(), nullable=False),
        sa.Column('period_type', sa.String(length=16), nullable=False),
        sa.Column('statement_basis', sa.String(length=16), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('reported_at', sa.Date(), nullable=True),
        sa.Column('available_at', sa.Date(), nullable=False),
        sa.Column('revision_type', sa.String(length=16), nullable=False, server_default='AS_REPORTED'),
        sa.Column('currency', sa.String(length=8), nullable=False, server_default='INR'),
        sa.Column('unit', sa.String(length=16), nullable=False, server_default='INR_CRORE'),
        sa.Column('revenue', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('operating_income', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('ebit', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('pbt', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('pat', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('eps', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('total_assets', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('total_liabilities', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('total_equity', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('total_debt', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('cash_and_equivalents', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('operating_cash_flow', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('investing_cash_flow', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('financing_cash_flow', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('free_cash_flow', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('shares_outstanding', sa.Numeric(precision=18, scale=0), nullable=True),
        sa.Column('source_name', sa.String(length=64), nullable=False, server_default='OFFICIAL_DISCLOSURE'),
        sa.Column('source_reference', sa.String(length=255), nullable=True),
        sa.Column('idempotency_hash', sa.String(length=64), nullable=False),
        sa.CheckConstraint("period_type IN ('QUARTERLY', 'ANNUAL')", name='ck_financial_statements_period_type'),
        sa.CheckConstraint("statement_basis IN ('CONSOLIDATED', 'STANDALONE')", name='ck_financial_statements_basis'),
        sa.CheckConstraint("revision_type IN ('AS_REPORTED', 'LATEST_RESTATED')", name='ck_financial_statements_revision'),
        sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_hash'),
        sa.UniqueConstraint('stock_id', 'period_type', 'statement_basis', 'period_end', 'revision_type', name='uq_financial_statements_identity')
    )
    op.create_index('ix_financial_statements_pit', 'financial_statements', ['available_at'], unique=False)
    op.create_index('ix_financial_statements_stock_period', 'financial_statements', ['stock_id', 'period_end'], unique=False)

    # 2. corporate_announcements
    op.create_table(
        'corporate_announcements',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('stock_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('announcement_date', sa.Date(), nullable=False),
        sa.Column('available_at', sa.Date(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('corporate_action_id', sa.Integer(), nullable=True),
        sa.Column('source_name', sa.String(length=64), nullable=False, server_default='NSE_ANNOUNCEMENT'),
        sa.Column('source_reference', sa.String(length=255), nullable=True),
        sa.Column('idempotency_hash', sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "category IN ('FINANCIAL_RESULTS', 'BOARD_MEETING', 'DIVIDEND', 'BONUS', 'STOCK_SPLIT', "
            "'RIGHTS', 'BUYBACK', 'MERGER', 'DEMERGER', 'ACQUISITION', 'ORDER_WIN', 'CAPEX', "
            "'FUND_RAISE', 'MANAGEMENT_CHANGE', 'CREDIT_RATING', 'LITIGATION', 'REGULATORY', 'OTHER')",
            name='ck_corporate_announcements_category'
        ),
        sa.ForeignKeyConstraint(['corporate_action_id'], ['corporate_actions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_hash')
    )
    op.create_index('ix_corporate_announcements_pit', 'corporate_announcements', ['available_at'], unique=False)
    op.create_index('ix_corporate_announcements_stock_date', 'corporate_announcements', ['stock_id', 'announcement_date'], unique=False)

    # 3. shareholding_patterns
    op.create_table(
        'shareholding_patterns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('stock_id', sa.Integer(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('reported_at', sa.Date(), nullable=True),
        sa.Column('available_at', sa.Date(), nullable=False),
        sa.Column('promoter_holding_pct', sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column('fii_holding_pct', sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column('dii_holding_pct', sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column('public_holding_pct', sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column('promoter_pledged_pct', sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column('promoter_holding_change', sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column('source_name', sa.String(length=64), nullable=False, server_default='OFFICIAL_SHAREHOLDING'),
        sa.Column('source_reference', sa.String(length=255), nullable=True),
        sa.Column('idempotency_hash', sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_hash'),
        sa.UniqueConstraint('stock_id', 'period_end', name='uq_shareholding_patterns_identity')
    )
    op.create_index('ix_shareholding_patterns_pit', 'shareholding_patterns', ['available_at'], unique=False)
    op.create_index('ix_shareholding_patterns_stock_period', 'shareholding_patterns', ['stock_id', 'period_end'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_shareholding_patterns_stock_period', table_name='shareholding_patterns')
    op.drop_index('ix_shareholding_patterns_pit', table_name='shareholding_patterns')
    op.drop_table('shareholding_patterns')

    op.drop_index('ix_corporate_announcements_stock_date', table_name='corporate_announcements')
    op.drop_index('ix_corporate_announcements_pit', table_name='corporate_announcements')
    op.drop_table('corporate_announcements')

    op.drop_index('ix_financial_statements_stock_period', table_name='financial_statements')
    op.drop_index('ix_financial_statements_pit', table_name='financial_statements')
    op.drop_table('financial_statements')
