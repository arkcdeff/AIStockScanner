"""What Changed Configuration — Deterministic Thresholds & Semantics."""
from __future__ import annotations

from typing import Final

# Supported comparison windows (in exchange trading sessions)
SUPPORTED_COMPARISON_WINDOWS: Final[tuple[int, ...]] = (1, 3, 5, 10, 20)
DEFAULT_COMPARISON_WINDOW: Final[int] = 1

# Price change thresholds (in percentage)
PRICE_CHANGE_NORMAL_PCT: Final[float] = 2.0
PRICE_CHANGE_SIGNIFICANT_PCT: Final[float] = 5.0
PRICE_CHANGE_MAJOR_PCT: Final[float] = 10.0

# Volume change thresholds (in percentage)
VOLUME_CHANGE_NORMAL_PCT: Final[float] = 50.0
VOLUME_CHANGE_SIGNIFICANT_PCT: Final[float] = 100.0  # 2x surge
VOLUME_CHANGE_MAJOR_PCT: Final[float] = 300.0        # 4x surge

# Delivery percentage point changes
DELIVERY_PCT_POINT_JUMP_THRESHOLD: Final[float] = 15.0  # +15 percentage points

# Technical indicator thresholds
RSI_OVERSOLD_THRESHOLD: Final[float] = 30.0
RSI_OVERBOUGHT_THRESHOLD: Final[float] = 70.0
RSI_SIGNIFICANT_POINT_CHANGE: Final[float] = 10.0
RSI_NORMAL_POINT_CHANGE: Final[float] = 5.0

ADX_TREND_THRESHOLD: Final[float] = 25.0

# Bulk and Block deal value thresholds (in INR Crore)
DEAL_VALUE_MAJOR_CRORE: Final[float] = 50.0  # >= 50 Cr is MAJOR

# Institutional net flow thresholds (in INR Crore)
INSTITUTIONAL_NET_FLOW_SIGNIFICANT_CRORE: Final[float] = 1000.0

# Deterministic sorting hierarchy
SIGNIFICANCE_SORT_ORDER: Final[dict[str, int]] = {
    "MAJOR": 0,
    "SIGNIFICANT": 1,
    "NORMAL": 2,
    "MINOR": 3,
}

DOMAIN_SORT_ORDER: Final[dict[str, int]] = {
    "PRICE": 0,
    "VOLUME": 1,
    "DELIVERY": 2,
    "TREND": 3,
    "TECHNICAL": 4,
    "SCANNER": 5,
    "DEAL": 6,
    "CORPORATE": 7,
    "INSTITUTIONAL": 8,
}
