"""UI metadata derived from the existing deterministic scanner inputs.

These values describe the indicator/history prerequisites already encoded by
``IndicatorEngine`` and ``ScannerRegistry``. They do not change scanner rules.
"""

from __future__ import annotations

from typing import NamedTuple


class ScannerRequirement(NamedTuple):
    required_sessions: int
    indicator_inputs: str


SCANNER_REQUIREMENTS: dict[str, ScannerRequirement] = {
    "accumulation": ScannerRequirement(21, "price, volume, delivery, 20-session averages"),
    "atr_high_volatility": ScannerRequirement(14, "ATR(14)"),
    "bollinger_band_squeeze": ScannerRequirement(20, "Bollinger Bands(20)"),
    "death_cross": ScannerRequirement(201, "EMA(50), EMA(200), previous session"),
    "delivery_above_60": ScannerRequirement(1, "delivery percentage"),
    "delivery_above_70": ScannerRequirement(1, "delivery percentage"),
    "delivery_above_average": ScannerRequirement(21, "delivery and 20-session average"),
    "delivery_breakout": ScannerRequirement(21, "delivery and 20-session maximum"),
    "ema_bearish_crossover": ScannerRequirement(51, "EMA(20), EMA(50), previous session"),
    "ema_bullish_crossover": ScannerRequirement(51, "EMA(20), EMA(50), previous session"),
    "golden_cross": ScannerRequirement(201, "EMA(50), EMA(200), previous session"),
    "high_volume_high_delivery": ScannerRequirement(21, "volume average and delivery"),
    "highest_volume_20d": ScannerRequirement(21, "volume and 20-session maximum"),
    "macd_above_zero": ScannerRequirement(35, "MACD(12,26,9)"),
    "macd_bearish": ScannerRequirement(35, "MACD signal and previous session"),
    "macd_below_zero": ScannerRequirement(35, "MACD(12,26,9)"),
    "macd_bullish": ScannerRequirement(35, "MACD signal and previous session"),
    "obv_rising": ScannerRequirement(2, "OBV and previous session"),
    "price_above_ema20": ScannerRequirement(20, "EMA(20)"),
    "price_above_upper_bollinger": ScannerRequirement(20, "upper Bollinger Band(20)"),
    "price_below_ema20": ScannerRequirement(20, "EMA(20)"),
    "price_below_lower_bollinger": ScannerRequirement(20, "lower Bollinger Band(20)"),
    "price_breakout_high_delivery": ScannerRequirement(21, "price, volume, delivery, 20-session high"),
    "rsi_overbought": ScannerRequirement(14, "RSI(14)"),
    "rsi_oversold": ScannerRequirement(14, "RSI(14)"),
    "stochastic_overbought": ScannerRequirement(16, "Stochastic(14,3,3)"),
    "stochastic_oversold": ScannerRequirement(16, "Stochastic(14,3,3)"),
    "strong_trend_adx": ScannerRequirement(28, "ADX(14)"),
    "supertrend_bearish": ScannerRequirement(7, "SuperTrend(7,3)"),
    "supertrend_bullish": ScannerRequirement(7, "SuperTrend(7,3)"),
    "volume_2x_average": ScannerRequirement(21, "volume and 20-session average"),
    "volume_5x_average": ScannerRequirement(21, "volume and 20-session average"),
    "volume_breakout": ScannerRequirement(21, "volume and 20-session maximum"),
    "vwap_bearish": ScannerRequirement(1, "VWAP"),
    "vwap_bullish": ScannerRequirement(1, "VWAP"),
    "weak_trend_adx": ScannerRequirement(28, "ADX(14)"),
}

SCANNER_HISTORY_THRESHOLDS = tuple(
    sorted({requirement.required_sessions for requirement in SCANNER_REQUIREMENTS.values()})
)


def scanner_requirement(code: str) -> ScannerRequirement:
    """Return explicit metadata, conservatively defaulting to one session."""
    return SCANNER_REQUIREMENTS.get(code, ScannerRequirement(1, "stored indicator row"))
