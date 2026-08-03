"""seed scan definitions

Revision ID: fda0edd539bd
Revises: c64742f7da53
Create Date: 2026-08-01 16:43:06.811192
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'fda0edd539bd'
down_revision: str | Sequence[str] | None = 'c64742f7da53'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text('''
        INSERT INTO scan_definitions (code, name, description, category, parameters, is_active)
        VALUES
            ('ema_bullish_crossover', 'EMA Bullish Crossover', 'EMA 20 crosses above EMA 50', 'Trend', '{}', true),
            ('ema_bearish_crossover', 'EMA Bearish Crossover', 'EMA 20 crosses below EMA 50', 'Trend', '{}', true),
            ('golden_cross', 'Golden Cross', 'EMA 50 crosses above EMA 200', 'Trend', '{}', true),
            ('death_cross', 'Death Cross', 'EMA 50 crosses below EMA 200', 'Trend', '{}', true),
            ('price_above_ema20', 'Price Above EMA 20', 'Close price above EMA 20', 'Trend', '{}', true),
            ('price_below_ema20', 'Price Below EMA 20', 'Close price below EMA 20', 'Trend', '{}', true),
            ('supertrend_bullish', 'SuperTrend Bullish', 'SuperTrend direction is bullish', 'Trend', '{}', true),
            ('supertrend_bearish', 'SuperTrend Bearish', 'SuperTrend direction is bearish', 'Trend', '{}', true),
            ('rsi_oversold', 'RSI Oversold', 'RSI 14 below 30', 'Momentum', '{}', true),
            ('rsi_overbought', 'RSI Overbought', 'RSI 14 above 70', 'Momentum', '{}', true),
            ('macd_bullish', 'MACD Bullish Crossover', 'MACD line crosses above signal line', 'Momentum', '{}', true),
            ('macd_bearish', 'MACD Bearish Crossover', 'MACD line crosses below signal line', 'Momentum', '{}', true),
            ('stochastic_oversold', 'Stochastic Oversold', 'Stochastic %K below 20', 'Momentum', '{}', true),
            ('stochastic_overbought', 'Stochastic Overbought', 'Stochastic %K above 80', 'Momentum', '{}', true),
            ('macd_above_zero', 'MACD Above Zero', 'MACD line above zero', 'Momentum', '{}', true),
            ('macd_below_zero', 'MACD Below Zero', 'MACD line below zero', 'Momentum', '{}', true),
            ('bollinger_band_squeeze', 'Bollinger Band Squeeze', 'Bollinger bandwidth below 5%', 'Volatility', '{}', true),
            ('price_above_upper_bollinger', 'Price Above Upper Bollinger', 'Close price above upper Bollinger Band', 'Volatility', '{}', true),
            ('price_below_lower_bollinger', 'Price Below Lower Bollinger', 'Close price below lower Bollinger Band', 'Volatility', '{}', true),
            ('atr_high_volatility', 'ATR High Volatility', 'ATR percentage above 3%', 'Volatility', '{}', true),
            ('obv_rising', 'OBV Rising', 'OBV increasing from previous day', 'Volume', '{}', true),
            ('vwap_bullish', 'VWAP Bullish', 'Close price above VWAP', 'Volume', '{}', true),
            ('vwap_bearish', 'VWAP Bearish', 'Close price below VWAP', 'Volume', '{}', true),
            ('strong_trend_adx', 'Strong Trend ADX', 'ADX above 25', 'Trend Strength', '{}', true),
            ('weak_trend_adx', 'Weak Trend ADX', 'ADX below 20', 'Trend Strength', '{}', true)
        ON CONFLICT (code) DO NOTHING
    '''))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text('''
        DELETE FROM scan_definitions
        WHERE code IN (
            'ema_bullish_crossover', 'ema_bearish_crossover', 'golden_cross', 'death_cross',
            'price_above_ema20', 'price_below_ema20', 'supertrend_bullish', 'supertrend_bearish',
            'rsi_oversold', 'rsi_overbought', 'macd_bullish', 'macd_bearish',
            'stochastic_oversold', 'stochastic_overbought', 'macd_above_zero', 'macd_below_zero',
            'bollinger_band_squeeze', 'price_above_upper_bollinger', 'price_below_lower_bollinger',
            'atr_high_volatility', 'obv_rising', 'vwap_bullish', 'vwap_bearish',
            'strong_trend_adx', 'weak_trend_adx'
        )
    '''))
