from __future__ import annotations

from datetime import time
from decimal import Decimal

# The price query must support a 252-session prior breakout window at a
# 20-session momentum reference date. 550 calendar days is deliberately
# bounded while providing a conservative buffer for weekends and holidays.
BREADTH_HISTORY_LOOKBACK_CALENDAR_DAYS = 550
BREADTH_CALCULATION_VERSION = "market_breadth_v1"
BREADTH_BENCHMARK_CODE = "NIFTY_50"
BREADTH_MARKET_CLOSE_TIME = time(15, 30)

SMA_WINDOWS = (20, 50, 200)
HIGH_LOW_WINDOWS: dict[str, int] = {"20d": 20, "52w": 252}
MOMENTUM_SESSION_WINDOWS = (1, 5, 20)

SCORE_MIN = Decimal("-100")
SCORE_MAX = Decimal("100")
QUALITY_MIN = Decimal("0")
QUALITY_MAX = Decimal("100")

# Participation is intentionally tilted toward current A/D and short/medium
# trend participation. Long-term and breakout evidence remain meaningful but
# cannot dominate the current breadth observation.
PARTICIPATION_WEIGHTS: dict[str, Decimal] = {
    "advancing_percent": Decimal("0.35"),
    "percent_above_sma20": Decimal("0.25"),
    "percent_above_sma50": Decimal("0.20"),
    "percent_above_sma200": Decimal("0.15"),
    "new_high_low_balance": Decimal("0.05"),
}

HIGH_LOW_BALANCE_WEIGHTS: dict[str, Decimal] = {
    "20d": Decimal("0.60"),
    "52w": Decimal("0.40"),
}

# Each momentum input is already normalized to -100..+100. Missing horizons
# are omitted and the remaining configured weights are renormalized.
MOMENTUM_WEIGHTS: dict[str, Decimal] = {
    "advancing_percent_change_1": Decimal("0.15"),
    "advancing_percent_change_5": Decimal("0.20"),
    "advancing_percent_change_20": Decimal("0.10"),
    "ad_line_impulse_5": Decimal("0.15"),
    "ad_line_impulse_20": Decimal("0.10"),
    "sma20_change_5": Decimal("0.15"),
    "sma50_change_20": Decimal("0.10"),
    "high_low_change_5": Decimal("0.05"),
}

MOMENTUM_CHANGE_SCALES: dict[str, Decimal] = {
    "advancing_percent_change_1": Decimal("4"),
    "advancing_percent_change_5": Decimal("2"),
    "advancing_percent_change_20": Decimal("1"),
    "sma20_change_5": Decimal("2"),
    "sma50_change_20": Decimal("1"),
    "high_low_change_5": Decimal("2"),
}

BREADTH_SCORE_WEIGHTS: dict[str, Decimal] = {
    "advance_decline": Decimal("0.25"),
    "sma20_participation": Decimal("0.15"),
    "sma50_participation": Decimal("0.15"),
    "sma200_participation": Decimal("0.10"),
    "new_high_low": Decimal("0.10"),
    "momentum": Decimal("0.15"),
    "divergence": Decimal("0.10"),
}

DIVERGENCE_COMPONENT_SCORES: dict[str, Decimal] = {
    "bullish_confirmation": Decimal("100"),
    "positive_divergence": Decimal("50"),
    "neutral": Decimal("0"),
    "negative_divergence": Decimal("-50"),
    "bearish_confirmation": Decimal("-100"),
}

MOMENTUM_DIRECTION_THRESHOLD = Decimal("15")

# Sprint 18 specifies these breadth-only state names. These thresholds apply
# to the final breadth score, not to the future Task 18.9 market composite.
BREADTH_STATE_THRESHOLDS: dict[str, Decimal] = {
    "STRONG": Decimal("60"),
    "POSITIVE": Decimal("25"),
    "WEAK": Decimal("-25"),
    "VERY_WEAK": Decimal("-60"),
}

BREADTH_DIRECTION_THRESHOLDS: dict[str, Decimal] = {
    "strong_bullish": Decimal("60"),
    "bullish": Decimal("25"),
    "bearish": Decimal("-25"),
    "strong_bearish": Decimal("-60"),
}

COMPLETENESS_WEIGHTS: dict[str, Decimal] = {
    "current_close_coverage": Decimal("0.20"),
    "classified_coverage": Decimal("0.20"),
    "sma20_coverage": Decimal("0.10"),
    "sma50_coverage": Decimal("0.10"),
    "sma200_coverage": Decimal("0.15"),
    "high_low_20d_coverage": Decimal("0.10"),
    "high_low_52w_coverage": Decimal("0.10"),
    "benchmark_coverage": Decimal("0.05"),
}

CONFIDENCE_WEIGHTS: dict[str, Decimal] = {
    "completeness": Decimal("0.55"),
    "freshness": Decimal("0.15"),
    "universe_quality": Decimal("0.15"),
    "signal_agreement": Decimal("0.15"),
}

FRESHNESS_QUALITY: dict[str, Decimal] = {
    "recent": Decimal("100"),
    "stale": Decimal("40"),
    "missing": Decimal("0"),
}

# Current-stock-master membership and the lack of a canonical instrument-type
# column prevent production claims of point-in-time index constituency or
# perfect ETF identification. Confidence reflects that limitation explicitly.
CURRENT_MEMBERSHIP_UNIVERSE_QUALITY = Decimal("65")
