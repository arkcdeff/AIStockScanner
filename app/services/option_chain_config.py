"""Option Chain Configuration — Indian Index Strike Grids, Tolerances & Partitioning.

Milestone: G.11-OPT-1 (SPEC-R2 & Implementation Specification §30, §31, §36, §42).
Defines authoritative constants, strike grid steps for Indian index underlyings,
timestamp skew tolerances, and partition maintenance policies.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Final

# ===========================================================================
# 1. Authoritative Index Strike Steps (SPEC-R2 §19 & Implementation Spec §30)
# ===========================================================================

DEFAULT_STRIKE_STEPS: Final[dict[str, Decimal]] = {
    # Core Canonical Codes
    "NIFTY": Decimal("50"),
    "BANKNIFTY": Decimal("100"),
    "FINNIFTY": Decimal("50"),
    "MIDCPNIFTY": Decimal("25"),
    "SENSEX": Decimal("100"),
    # Standard Canonical Identifiers & Exchange Aliases
    "NIFTY_50": Decimal("50"),
    "NIFTY50": Decimal("50"),
    "NIFTY_BANK": Decimal("100"),
    "BANK_NIFTY": Decimal("100"),
    "NIFTY_FIN_SERV": Decimal("50"),
    "NIFTY_FINANCIAL_SERVICES": Decimal("50"),
    "NIFTY_MID_SELECT": Decimal("25"),
    "NIFTY_MIDCAP_SELECT": Decimal("25"),
    "NIFTY_NEXT_50": Decimal("100"),
    "NIFTYNEXT50": Decimal("100"),
    "BSE_SENSEX": Decimal("100"),
}


def normalize_index_symbol(symbol: str) -> str:
    """Normalize index symbol for strike grid lookup."""
    return symbol.strip().upper().replace(" ", "_").replace("-", "_")


def get_strike_step(underlying: str) -> Decimal:
    """Resolve the configured strike step for an underlying index.

    Args:
        underlying: Symbol or code of the underlying index (e.g. 'NIFTY', 'BANKNIFTY').

    Returns:
        Decimal strike step (e.g. 50, 100, 25).

    Raises:
        ValueError: If underlying is not recognized in strike step configuration.
    """
    key = normalize_index_symbol(underlying)
    if key in DEFAULT_STRIKE_STEPS:
        return DEFAULT_STRIKE_STEPS[key]
    raise ValueError(f"No configured strike step for underlying index: '{underlying}'")


# ===========================================================================
# 2. Clock-Skew Tolerances (SPEC-R2 §10 & Implementation Spec §31)
# ===========================================================================

# Initial reference clock-skew tolerance (2000 milliseconds = 2.0s)
DEFAULT_CLOCK_SKEW_TOLERANCE_MS: Final[int] = 2000

# Maximum allowable clock-skew before hard rejection (5 minutes = 300,000 ms)
MAX_ALLOWABLE_CLOCK_SKEW_MS: Final[int] = 300_000


# ===========================================================================
# 3. Partition Maintenance Policy (Implementation Spec §36)
# ===========================================================================

# Maintain current month + next 3 calendar months (total 4 calendar months)
DEFAULT_PARTITION_AHEAD_MONTHS: Final[int] = 3
PARTITION_TABLE_PREFIX: Final[str] = "option_chain_quotes"


# ===========================================================================
# 4. Data Quality Statuses (OptionChainSnapshot check constraint)
# ===========================================================================

DQ_STATUS_VALID: Final[str] = "VALID"
DQ_STATUS_WARNING: Final[str] = "WARNING"
DQ_STATUS_ACCEPT_WITH_FLAG: Final[str] = "ACCEPT_WITH_FLAG"
DQ_STATUS_REJECTED: Final[str] = "REJECTED"


# ===========================================================================
# 5. Standard Quality Flag Keys
# ===========================================================================

DQ_FLAG_OFF_GRID_STRIKE: Final[str] = "OFF_GRID_STRIKE"
DQ_FLAG_NON_POSITIVE_STRIKE: Final[str] = "NON_POSITIVE_STRIKE"
DQ_FLAG_INVALID_NUMERIC_STRIKE: Final[str] = "INVALID_NUMERIC_STRIKE"
DQ_FLAG_DUPLICATE_STRIKE: Final[str] = "DUPLICATE_STRIKE"
DQ_FLAG_UNORDERED_STRIKES: Final[str] = "UNORDERED_STRIKES"
DQ_FLAG_IRREGULAR_STRIKE_SPACING: Final[str] = "IRREGULAR_STRIKE_SPACING"
DQ_FLAG_TRUNCATED_BY_SOURCE: Final[str] = "TRUNCATED_BY_SOURCE"
DQ_FLAG_INCOMPLETE_CHAIN: Final[str] = "INCOMPLETE_CHAIN"
DQ_FLAG_STRUCTURAL_COUNT_ANOMALY: Final[str] = "STRUCTURAL_COUNT_ANOMALY"
DQ_FLAG_EMPTY_CHAIN: Final[str] = "EMPTY_CHAIN"
DQ_FLAG_INSUFFICIENT_STRIKES: Final[str] = "INSUFFICIENT_STRIKES"
DQ_FLAG_CLOCK_SKEW_TOLERATED: Final[str] = "CLOCK_SKEW_TOLERATED"
DQ_FLAG_CLOCK_SKEW_WARNING: Final[str] = "CLOCK_SKEW_WARNING"
DQ_FLAG_CLOCK_SKEW_EXCESSIVE: Final[str] = "CLOCK_SKEW_EXCESSIVE"
DQ_FLAG_AVAILABLE_AT_INVERSION: Final[str] = "AVAILABLE_AT_INVERSION"
DQ_FLAG_FUTURE_MARKET_TIMESTAMP: Final[str] = "FUTURE_MARKET_TIMESTAMP"
DQ_FLAG_UNMAPPED_INSTRUMENT: Final[str] = "UNMAPPED_INSTRUMENT"
DQ_FLAG_STALE_QUOTE: Final[str] = "STALE_QUOTE"
DQ_FLAG_MISSING_CONTRACT: Final[str] = "MISSING_CONTRACT"
DQ_FLAG_PROVIDER_DEGRADED: Final[str] = "PROVIDER_DEGRADED"
