"""What Changed Screening Configuration — G.11-D-4 Universe Screener.

G.11-D-4 Multi-Stock Universe Screener / Opportunity Ranking Layer.

Architecture:

    G.11-D-1  WHAT CHANGED
        ↓
    G.11-D-2  HOW THE EVIDENCE RELATES
        ↓
    G.11-D-3  WHAT THE EVIDENCE MEANS
        ↓
    G.11-D-4  WHICH STOCKS MATCH A FILTER ACROSS A UNIVERSE  ← this layer

Core principle: Stateless, on-demand, bounded async concurrency.
No new DB schema. No AI at screening layer. PIT-safe universe resolution.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Final

from app.services.what_changed_interpretation_config import DirectionalBias, OverallState


class ScreeningUniverse(StrEnum):
    """Predefined stock universes resolvable from the DB at a given date (PIT-safe).

    Index codes must match exactly the ``code`` values in the ``market_indices`` table
    (as seeded from ``app/market_intelligence/index_catalog.py``).
    """

    NIFTY_50 = "NIFTY_50"
    NIFTY_NEXT_50 = "NIFTY_NEXT_50"
    NIFTY_MIDCAP_100 = "NIFTY_MIDCAP_100"
    NIFTY_BANK = "NIFTY_BANK"
    FNO = "FNO"
    WATCHLIST = "WATCHLIST"


class ScreeningFilterOperator(StrEnum):
    """Logical operator combining multiple filter criteria."""

    AND = "AND"
    OR = "OR"


class ScreeningOutcomeStatus(StrEnum):
    """Per-symbol outcome status in a screening run."""

    MATCHED = "MATCHED"
    NO_MATCH = "NO_MATCH"
    ERROR = "ERROR"
    NO_DATA = "NO_DATA"


# ---------------------------------------------------------------------------
# Bounded concurrency
# ---------------------------------------------------------------------------

#: Maximum concurrent D-3 workers per screening request.
SCREENING_MAX_WORKERS: Final[int] = 5

#: Hard cap on custom symbol list. Requests exceeding this raise HTTP 422.
SCREENING_MAX_CUSTOM_SYMBOLS: Final[int] = 100

#: Watchlist soft cap — truncated silently, not rejected.
SCREENING_WATCHLIST_SOFT_CAP: Final[int] = 100

#: Default comparison window forwarded to D-3 interpret().
SCREENING_DEFAULT_COMPARISON_WINDOW: Final[int] = 1

# ---------------------------------------------------------------------------
# Filter preset definitions (label → criteria description)
# ---------------------------------------------------------------------------

#: OverallState values that represent bullish-leaning evidence.
BULLISH_STATES: Final[frozenset[OverallState]] = frozenset(
    {
        OverallState.BULLISH_EVIDENCE_SUPPORTED,
        OverallState.PARTIALLY_SUPPORTED_BULLISH_BIAS,
    }
)

#: OverallState values that represent bearish-leaning evidence.
BEARISH_STATES: Final[frozenset[OverallState]] = frozenset(
    {
        OverallState.BEARISH_EVIDENCE_SUPPORTED,
        OverallState.PARTIALLY_SUPPORTED_BEARISH_BIAS,
    }
)

#: DirectionalBias values that indicate strong directional evidence.
DIRECTIONAL_BIASES: Final[frozenset[DirectionalBias]] = frozenset(
    {
        DirectionalBias.BULLISH_EVIDENCE,
        DirectionalBias.BEARISH_EVIDENCE,
    }
)

# ---------------------------------------------------------------------------
# Convergence thresholds
# ---------------------------------------------------------------------------

#: Minimum convergence ratio to be considered "strong convergence".
CONVERGENCE_HIGH_THRESHOLD: Final[float] = 0.6

#: Minimum convergence ratio to be considered "moderate convergence".
CONVERGENCE_MODERATE_THRESHOLD: Final[float] = 0.3
