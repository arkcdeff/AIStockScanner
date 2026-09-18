"""What Changed Interpretation Configuration — G.11-D-3 Deterministic Rules & Semantics.

G.11-D-3 Stock Intelligence Interpretation / Evidence Narrative Layer.

Architecture:

    G.11-D-1  WHAT CHANGED
        ↓
    G.11-D-2  HOW THE EVIDENCE RELATES
        ↓
    G.11-D-3  WHAT THE EVIDENCE MEANS   ← this layer
        ↓
    AI       HOW TO EXPLAIN THE DETERMINISTIC INTERPRETATION

Core principle: Evidence → Interpretation → Narrative (NOT Indicator → Prediction).

D-3 consumes only the certified ``CrossDomainSynthesisResult`` from G.11-D-2.
It never recalculates market evidence and never emits trading actions.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Final


class OverallState(StrEnum):
    """Deterministic evidence-state of the interpreted synthesis.

    These are evidence descriptions — they are NOT trading actions.
    """

    BULLISH_EVIDENCE_SUPPORTED = "BULLISH_EVIDENCE_SUPPORTED"
    BEARISH_EVIDENCE_SUPPORTED = "BEARISH_EVIDENCE_SUPPORTED"
    PARTIALLY_SUPPORTED_BULLISH_BIAS = "PARTIALLY_SUPPORTED_BULLISH_BIAS"
    PARTIALLY_SUPPORTED_BEARISH_BIAS = "PARTIALLY_SUPPORTED_BEARISH_BIAS"
    MIXED_EVIDENCE = "MIXED_EVIDENCE"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEUTRAL_OBSERVATION = "NEUTRAL_OBSERVATION"


class DirectionalBias(StrEnum):
    """Deterministic directional reading of the evidence (descriptive, never prescriptive)."""

    BULLISH_EVIDENCE = "BULLISH_EVIDENCE"
    BEARISH_EVIDENCE = "BEARISH_EVIDENCE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    NO_DIRECTIONAL_SIGNAL = "NO_DIRECTIONAL_SIGNAL"


class EvidenceClassification(StrEnum):
    """Deterministic classification of a D-2 relationship for interpretation.

    CRITICAL: DIVERGENCE is always CONFLICTING — never PRIMARY.
    """

    PRIMARY = "PRIMARY"
    SUPPORTING = "SUPPORTING"
    CONFLICTING = "CONFLICTING"
    CONTEXT = "CONTEXT"
    MISSING = "MISSING"


class LifecycleChangeDescriptor(StrEnum):
    """Optional transition descriptor for a pattern whose semantics changed."""

    CHANGED = "CHANGED"


# ---------------------------------------------------------------------------
# Domain semantics
# ---------------------------------------------------------------------------

# Context-only domains (from certified D-2 semantics). These must NEVER:
#   increase directional convergence, establish direction, or imply intent.
CONTEXT_ONLY_DOMAINS: Final[frozenset[str]] = frozenset(
    {"CORPORATE", "INSTITUTIONAL", "DEAL"}
)

# Deterministic ranking of D-2 relationship strengths (higher = stronger evidence).
STRENGTH_RANK: Final[dict[str, int]] = {
    "MAJOR": 3,
    "SIGNIFICANT": 2,
    "NORMAL": 1,
    "CONTEXT_ONLY": 0,
}

# D-2 metadata keys whose values were computed by certified D-2. D-3 reads these
# values to derive price direction — it NEVER recalculates them from raw data.
PRICE_DIRECTION_METADATA_KEYS: Final[tuple[str, ...]] = (
    "price_change_pct",
    "stock_change_pct",
)

# D-2 muted-price pattern marker (metadata["pattern"]) — preserved as CONTEXT_ONLY.
MUTED_PRICE_PATTERN: Final[str] = "VOLUME_EXPANSION_WITH_MUTED_PRICE"


# ---------------------------------------------------------------------------
# Watch items — what evidence would make the interpretation clearer.
# These are NEVER trading instructions.
# ---------------------------------------------------------------------------

WATCH_PRICE_CONFIRMATION_REQUIRED: Final[str] = "PRICE_CONFIRMATION_REQUIRED"
WATCH_VOLUME_CONFIRMATION_REQUIRED: Final[str] = "VOLUME_CONFIRMATION_REQUIRED"
WATCH_TREND_ALIGNMENT_REQUIRED: Final[str] = "TREND_ALIGNMENT_REQUIRED"
WATCH_DERIVATIVE_DATA_REQUIRED: Final[str] = "DERIVATIVE_DATA_REQUIRED"
WATCH_SECTOR_CONFIRMATION_REQUIRED: Final[str] = "SECTOR_CONFIRMATION_REQUIRED"
WATCH_CONFLICT_RESOLUTION_REQUIRED: Final[str] = "CONFLICT_RESOLUTION_REQUIRED"

# Directional domains whose absence produces a specific watch item / limitation.
DOMAIN_WATCH_MAP: Final[dict[str, str]] = {
    "TECHNICAL": WATCH_TREND_ALIGNMENT_REQUIRED,
    "TREND": WATCH_TREND_ALIGNMENT_REQUIRED,
    "DERIVATIVES": WATCH_DERIVATIVE_DATA_REQUIRED,
    "SECTOR": WATCH_SECTOR_CONFIRMATION_REQUIRED,
}

DOMAIN_LIMITATION_TEXT: Final[dict[str, str]] = {
    "PRICE": "price evidence unavailable",
    "PARTICIPATION": "participation (volume/delivery) evidence unavailable",
    "DELIVERY": "delivery evidence unavailable",
    "TECHNICAL": "technical evidence unavailable",
    "TREND": "trend evidence unavailable",
    "DERIVATIVES": "derivatives evidence unavailable",
    "SECTOR": "sector evidence unavailable",
}

INSTITUTIONAL_LIMITATION: Final[str] = (
    "institutional evidence is market-wide context only"
)
NO_HISTORY_LIMITATION: Final[str] = "historical comparison unavailable"


# ---------------------------------------------------------------------------
# Interpretation method labels
# ---------------------------------------------------------------------------

METHOD_DETERMINISTIC: Final[str] = "deterministic"
METHOD_AI_ASSISTED: Final[str] = "deterministic+ai_narrative"
