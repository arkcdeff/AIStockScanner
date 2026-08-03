"""seed volume and delivery scan definitions

Revision ID: bdc596b3589a
Revises: fda0edd539bd
Create Date: 2026-08-01 17:31:14.768707
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'bdc596b3589a'
down_revision: str | Sequence[str] | None = 'fda0edd539bd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text('''
        INSERT INTO scan_definitions (code, name, description, category, parameters, is_active)
        VALUES
            ('volume_2x_average', 'Volume 2x Average', 'Latest volume >= 2x 20-session average', 'Volume', '{}', true),
            ('volume_5x_average', 'Volume 5x Average', 'Latest volume >= 5x 20-session average', 'Volume', '{}', true),
            ('highest_volume_20d', 'Highest Volume 20D', 'Latest volume is highest in last 20 sessions', 'Volume', '{}', true),
            ('volume_breakout', 'Volume Breakout', 'Latest volume > previous 20-session max', 'Volume', '{}', true),
            ('delivery_above_60', 'Delivery Above 60', 'Latest delivery >= 60%', 'Delivery', '{}', true),
            ('delivery_above_70', 'Delivery Above 70', 'Latest delivery >= 70%', 'Delivery', '{}', true),
            ('delivery_above_average', 'Delivery Above Average', 'Latest delivery > 20-session average', 'Delivery', '{}', true),
            ('delivery_breakout', 'Delivery Breakout', 'Latest delivery > previous 20-session max', 'Delivery', '{}', true),
            ('high_volume_high_delivery', 'High Volume High Delivery', 'Volume >= 2x avg AND delivery >= 60%', 'Combined', '{}', true),
            ('price_breakout_high_delivery', 'Price Breakout High Delivery', 'Close > 20d high AND volume >= avg AND delivery >= 60%', 'Combined', '{}', true),
            ('accumulation', 'Accumulation', 'Close up, volume > avg, delivery > avg', 'Combined', '{}', true)
        ON CONFLICT (code) DO NOTHING
    '''))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text('''
        DELETE FROM scan_definitions
        WHERE code IN (
            'volume_2x_average', 'volume_5x_average', 'highest_volume_20d', 'volume_breakout',
            'delivery_above_60', 'delivery_above_70', 'delivery_above_average', 'delivery_breakout',
            'high_volume_high_delivery', 'price_breakout_high_delivery', 'accumulation'
        )
    '''))
