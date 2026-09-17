"""What Changed Synthesis Configuration — G.11-D-2 Cross-Domain Deterministic Rules & Semantics."""
from __future__ import annotations

from enum import StrEnum
from typing import Final


class RelationshipType(StrEnum):
    """Deterministic relationship classification between independent domains."""

    CONFIRMATION = "CONFIRMATION"
    PARTIAL_CONFIRMATION = "PARTIAL_CONFIRMATION"
    CONFLICT = "CONFLICT"
    DIVERGENCE = "DIVERGENCE"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEW_CROSS_DOMAIN_PATTERN = "NEW_CROSS_DOMAIN_PATTERN"
    RESOLVED_CROSS_DOMAIN_PATTERN = "RESOLVED_CROSS_DOMAIN_PATTERN"


class RelationshipStrength(StrEnum):
    """Deterministic importance ranking for cross-domain relationships."""

    MAJOR = "MAJOR"
    SIGNIFICANT = "SIGNIFICANT"
    NORMAL = "NORMAL"
    CONTEXT_ONLY = "CONTEXT_ONLY"


class EvidenceQuality(StrEnum):
    """Overall evidence quality across available domains."""

    CONFIRMED = "CONFIRMED"
    PARTIALLY_CONFIRMED = "PARTIALLY_CONFIRMED"
    CONFLICTING = "CONFLICTING"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    CONTEXT_ONLY = "CONTEXT_ONLY"


class PatternLifecycle(StrEnum):
    """Temporal evolution state of a cross-domain relationship across comparison windows."""

    NEW = "NEW"
    CONTINUING = "CONTINUING"
    STRENGTHENING = "STRENGTHENING"
    WEAKENING = "WEAKENING"
    RESOLVED = "RESOLVED"


# Core independent evidence domains (domain-level weighting for convergence score)
DOMAIN_PRICE: Final[str] = "PRICE"
DOMAIN_PARTICIPATION: Final[str] = "PARTICIPATION"  # Volume and Delivery
DOMAIN_TECHNICAL: Final[str] = "TECHNICAL"          # Oscillators (RSI, etc.)
DOMAIN_TREND: Final[str] = "TREND"                  # Moving averages & SuperTrend
DOMAIN_DERIVATIVES: Final[str] = "DERIVATIVES"      # Futures OI & Options
DOMAIN_CORPORATE: Final[str] = "CORPORATE"          # Announcements & Corporate Actions
DOMAIN_INSTITUTIONAL: Final[str] = "INSTITUTIONAL"  # Market-wide cash flows (context only)
DOMAIN_SECTOR: Final[str] = "SECTOR"                # Sectoral index relative move

ALL_INDEPENDENT_DOMAINS: Final[tuple[str, ...]] = (
    DOMAIN_PRICE,
    DOMAIN_PARTICIPATION,
    DOMAIN_TECHNICAL,
    DOMAIN_TREND,
    DOMAIN_DERIVATIVES,
    DOMAIN_CORPORATE,
    DOMAIN_INSTITUTIONAL,
    DOMAIN_SECTOR,
)

# Deterministic sorting hierarchy for relationships
SYNTHESIS_STRENGTH_SORT_ORDER: Final[dict[str, int]] = {
    RelationshipStrength.MAJOR: 0,
    RelationshipStrength.SIGNIFICANT: 1,
    RelationshipStrength.NORMAL: 2,
    RelationshipStrength.CONTEXT_ONLY: 3,
}

SYNTHESIS_RELATIONSHIP_SORT_ORDER: Final[dict[str, int]] = {
    RelationshipType.CONFIRMATION: 0,
    RelationshipType.PARTIAL_CONFIRMATION: 1,
    RelationshipType.CONFLICT: 2,
    RelationshipType.DIVERGENCE: 3,
    RelationshipType.NEW_CROSS_DOMAIN_PATTERN: 4,
    RelationshipType.RESOLVED_CROSS_DOMAIN_PATTERN: 5,
    RelationshipType.CONTEXT_ONLY: 6,
    RelationshipType.INSUFFICIENT_EVIDENCE: 7,
}

# Thresholds for cross-domain synthesis
FUTURES_OI_CHANGE_SIGNIFICANT_PCT: Final[float] = 3.0
SECTOR_MOVE_SIGNIFICANT_PCT: Final[float] = 0.5
