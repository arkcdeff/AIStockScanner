"""Exchange, segment, and series constants for historical data research.

These are pure Python constants — no database table.
Centralises the domain vocabulary used by SecurityAlias, CorporateAction,
IndexMembership, and all historical query services.

BSE support is architecturally ready; ingestion is deferred to a later task.
F&O segment constants are present for forward-compatibility but no derivative
data is imported or queried in 19.1C.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Exchanges
# ---------------------------------------------------------------------------

EXCHANGE_NSE = "NSE"
EXCHANGE_BSE = "BSE"

SUPPORTED_EXCHANGES: frozenset[str] = frozenset({EXCHANGE_NSE, EXCHANGE_BSE})

# ---------------------------------------------------------------------------
# Market segments / instrument types
# ---------------------------------------------------------------------------

SEGMENT_CASH = "CASH"
SEGMENT_INDEX = "INDEX"
SEGMENT_FUTURES = "FUTURES"   # future use — 19.1D
SEGMENT_OPTIONS = "OPTIONS"   # future use — 19.1D

# Segments fully implemented in 19.1C
IMPLEMENTED_SEGMENTS: frozenset[str] = frozenset({SEGMENT_CASH, SEGMENT_INDEX})

# ---------------------------------------------------------------------------
# NSE bhavcopy series — cash-equity eligible for scanner universe
# ---------------------------------------------------------------------------

#: Series that are imported and considered part of the equity cash universe.
NSE_CASH_ELIGIBLE_SERIES: frozenset[str] = frozenset({"EQ", "BE", "BZ", "SM", "ST"})

#: Series that are explicitly skipped during import (government bonds, SGBs etc.)
NSE_SKIP_SERIES: frozenset[str] = frozenset({"GB", "GS", "SGB", "N8"})

# ---------------------------------------------------------------------------
# Security lifecycle status values
# ---------------------------------------------------------------------------

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_SUSPENDED = "suspended"
LIFECYCLE_DELISTED = "delisted"
LIFECYCLE_INACTIVE = "inactive"
LIFECYCLE_UNKNOWN = "unknown"

LIFECYCLE_STATUSES: tuple[str, ...] = (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_SUSPENDED,
    LIFECYCLE_DELISTED,
    LIFECYCLE_INACTIVE,
    LIFECYCLE_UNKNOWN,
)

# ---------------------------------------------------------------------------
# Corporate action types
# ---------------------------------------------------------------------------

CA_SPLIT = "split"
CA_BONUS = "bonus"
CA_DIVIDEND = "dividend"
CA_RIGHTS = "rights"
CA_MERGER = "merger"
CA_DEMERGER = "demerger"
CA_BUYBACK = "buyback"
CA_SYMBOL_CHANGE = "symbol_change"
CA_NAME_CHANGE = "name_change"

CORPORATE_ACTION_TYPES: tuple[str, ...] = (
    CA_SPLIT,
    CA_BONUS,
    CA_DIVIDEND,
    CA_RIGHTS,
    CA_MERGER,
    CA_DEMERGER,
    CA_BUYBACK,
    CA_SYMBOL_CHANGE,
    CA_NAME_CHANGE,
)

#: Corporate action types for which deterministic price adjustment is implemented in 19.1C.
ADJUSTABLE_ACTION_TYPES: frozenset[str] = frozenset({CA_SPLIT, CA_BONUS})

#: Adjustment types deferred — implementation requires additional financial methodology decisions.
DEFERRED_ADJUSTMENT_TYPES: frozenset[str] = frozenset({
    CA_DIVIDEND,   # total-return vs. price-return convention varies by use case
    CA_RIGHTS,     # requires subscription price and ratio from authoritative source
    CA_MERGER,     # requires exchange ratio and both security identities
    CA_DEMERGER,   # requires demerger ratio and new security identity
})

# ---------------------------------------------------------------------------
# Data quality issue codes
# ---------------------------------------------------------------------------

DQ_MISSING_PRICE = "MISSING_PRICE"
DQ_MISSING_DELIVERY = "MISSING_DELIVERY"
DQ_OHLC_INVALID = "OHLC_INVALID"
DQ_NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
DQ_DUPLICATE_PRICE = "DUPLICATE_PRICE"
DQ_IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
DQ_INVALID_VALIDITY_RANGE = "INVALID_VALIDITY_RANGE"
DQ_MEMBERSHIP_OVERLAP = "MEMBERSHIP_OVERLAP"
DQ_CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
DQ_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
DQ_PROVENANCE_MISSING = "PROVENANCE_MISSING"
DQ_ZERO_VOLUME = "ZERO_VOLUME"

# Derivative Contract Issue Codes (19.1D-1)
DQ_DERIVATIVE_UNKNOWN_UNDERLYING = "DERIVATIVE_UNKNOWN_UNDERLYING"
DQ_DERIVATIVE_INVALID_EXPIRY = "DERIVATIVE_INVALID_EXPIRY"
DQ_DERIVATIVE_INVALID_STRIKE = "DERIVATIVE_INVALID_STRIKE"
DQ_DERIVATIVE_INVALID_OPTION_TYPE = "DERIVATIVE_INVALID_OPTION_TYPE"
DQ_DERIVATIVE_INVALID_LOT_SIZE = "DERIVATIVE_INVALID_LOT_SIZE"
DQ_DERIVATIVE_DUPLICATE_CONTRACT = "DERIVATIVE_DUPLICATE_CONTRACT"
DQ_DERIVATIVE_UNSUPPORTED_FORMAT = "DERIVATIVE_UNSUPPORTED_FORMAT"
DQ_DERIVATIVE_MISSING_SOURCE_ID = "DERIVATIVE_MISSING_SOURCE_ID"

# Derivative Daily Price Issue Codes (19.1D-2)
DQ_DERIVATIVE_PRICE_INVALID_OHLC = "DERIVATIVE_PRICE_INVALID_OHLC"
DQ_DERIVATIVE_PRICE_NEGATIVE_VOLUME = "DERIVATIVE_PRICE_NEGATIVE_VOLUME"
DQ_DERIVATIVE_PRICE_NEGATIVE_OI = "DERIVATIVE_PRICE_NEGATIVE_OI"
DQ_DERIVATIVE_PRICE_POST_EXPIRY = "DERIVATIVE_PRICE_POST_EXPIRY"
DQ_DERIVATIVE_PRICE_UNKNOWN_CONTRACT = "DERIVATIVE_PRICE_UNKNOWN_CONTRACT"
DQ_DERIVATIVE_PRICE_DUPLICATE = "DERIVATIVE_PRICE_DUPLICATE"
DQ_DERIVATIVE_PRICE_MISSING_SETTLEMENT = "DERIVATIVE_PRICE_MISSING_SETTLEMENT"
DQ_DERIVATIVE_PRICE_BAD_TURNOVER = "DERIVATIVE_PRICE_BAD_TURNOVER"

DATA_QUALITY_ISSUE_CODES: frozenset[str] = frozenset({
    DQ_MISSING_PRICE,
    DQ_MISSING_DELIVERY,
    DQ_OHLC_INVALID,
    DQ_NEGATIVE_VOLUME,
    DQ_DUPLICATE_PRICE,
    DQ_IDENTITY_AMBIGUOUS,
    DQ_INVALID_VALIDITY_RANGE,
    DQ_MEMBERSHIP_OVERLAP,
    DQ_CORPORATE_ACTION_UNRESOLVED,
    DQ_SOURCE_UNAVAILABLE,
    DQ_PROVENANCE_MISSING,
    DQ_ZERO_VOLUME,
    DQ_DERIVATIVE_UNKNOWN_UNDERLYING,
    DQ_DERIVATIVE_INVALID_EXPIRY,
    DQ_DERIVATIVE_INVALID_STRIKE,
    DQ_DERIVATIVE_INVALID_OPTION_TYPE,
    DQ_DERIVATIVE_INVALID_LOT_SIZE,
    DQ_DERIVATIVE_DUPLICATE_CONTRACT,
    DQ_DERIVATIVE_UNSUPPORTED_FORMAT,
    DQ_DERIVATIVE_MISSING_SOURCE_ID,
    DQ_DERIVATIVE_PRICE_INVALID_OHLC,
    DQ_DERIVATIVE_PRICE_NEGATIVE_VOLUME,
    DQ_DERIVATIVE_PRICE_NEGATIVE_OI,
    DQ_DERIVATIVE_PRICE_POST_EXPIRY,
    DQ_DERIVATIVE_PRICE_UNKNOWN_CONTRACT,
    DQ_DERIVATIVE_PRICE_DUPLICATE,
    DQ_DERIVATIVE_PRICE_MISSING_SETTLEMENT,
    DQ_DERIVATIVE_PRICE_BAD_TURNOVER,
})

DQ_SEVERITY_WARNING = "warning"
DQ_SEVERITY_ERROR = "error"
