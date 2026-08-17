from __future__ import annotations

from dataclasses import dataclass

#: Benchmark used for every relative-performance measurement.
SECTOR_BENCHMARK_CODE = "NIFTY_50"

#: High-level group labels. These describe sector membership for grouping and
#: reporting; they are metadata, not trading advice. NIFTY_BANK and
#: NIFTY_FIN_SERV are shared with the existing index catalog and are never
#: duplicated as separate identities.
SECTOR_GROUP_LABELS: dict[str, str] = {
    "financial": "Banks and financial services",
    "cyclical": "Economically sensitive sectors",
    "defensive": "Demand-stable consumer staples",
    "technology": "Software and technology",
    "commodity": "Metals, energy and commodities",
    "consumption": "Consumer goods (discretionary/staples)",
    "real_estate": "Real estate developers",
    "healthcare": "Healthcare and pharmaceuticals",
    "other": "Sectors without a clear single-group assignment",
}

#: Cyclicality used by the cyclical-vs-defensive context. Sectors that are not
#: confidently cyclical or defensive are left ``None`` (ungrouped) rather than
#: silently assigned. Financials and IT/Services are intentionally ungrouped
#: because a binary cyclical/defensive label would over-simplify them.
CYCLICALITY_LABELS: dict[str, str] = {
    "cyclical": "Rallies along with economic expansion",
    "defensive": "Relatively resilient in downturns",
}


@dataclass(frozen=True, slots=True)
class SectorDefinition:
    """Canonical NSE sector-index definition.

    ``code`` matches the canonical ``MarketIndex.code`` so sector intelligence
    references the existing price domain (``MarketIndexPrice``) directly.
    ``provider_symbol`` is the ticker as published by NSE for reference; it is
    metadata only and does not imply provider support (the NSE provider remains
    honest about what it actually fetches).
    """

    code: str
    name: str
    exchange: str = "NSE"
    category: str = "sector_index"
    provider_symbol: str = ""
    group: str = "other"
    cyclicality: str | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.name:
            raise ValueError("sector code and name are required")
        if self.group not in SECTOR_GROUP_LABELS:
            raise ValueError(f"unknown sector group: {self.group}")
        if self.cyclicality not in (None, "cyclical", "defensive"):
            raise ValueError(f"unknown cyclicality: {self.cyclicality}")


SECTOR_DEFINITIONS: tuple[SectorDefinition, ...] = (
    SectorDefinition(
        "NIFTY_AUTO",
        "Nifty Auto",
        provider_symbol="NIFTY AUTO",
        group="cyclical",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_BANK",
        "Nifty Bank",
        provider_symbol="BANKNIFTY",
        group="financial",
        cyclicality=None,
    ),
    SectorDefinition(
        "NIFTY_FIN_SERV",
        "Nifty Financial Services",
        provider_symbol="FINNIFTY",
        group="financial",
        cyclicality=None,
    ),
    SectorDefinition(
        "NIFTY_FMCG",
        "Nifty FMCG",
        provider_symbol="NIFTY FMCG",
        group="consumption",
        cyclicality="defensive",
    ),
    SectorDefinition(
        "NIFTY_IT",
        "Nifty IT",
        provider_symbol="NIFTY IT",
        group="technology",
        cyclicality=None,
    ),
    SectorDefinition(
        "NIFTY_MEDIA",
        "Nifty Media",
        provider_symbol="NIFTY MEDIA",
        group="cyclical",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_METAL",
        "Nifty Metal",
        provider_symbol="NIFTY METAL",
        group="commodity",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_PHARMA",
        "Nifty Pharma",
        provider_symbol="NIFTY PHARMA",
        group="healthcare",
        cyclicality="defensive",
    ),
    SectorDefinition(
        "NIFTY_PSU_BANK",
        "Nifty PSU Bank",
        provider_symbol="NIFTY PSU BANK",
        group="financial",
        cyclicality=None,
    ),
    SectorDefinition(
        "NIFTY_PRIVATE_BANK",
        "Nifty Private Bank",
        provider_symbol="NIFTY PRIVATE BANK",
        group="financial",
        cyclicality=None,
    ),
    SectorDefinition(
        "NIFTY_REALTY",
        "Nifty Realty",
        provider_symbol="NIFTY REALTY",
        group="real_estate",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_HEALTHCARE",
        "Nifty Healthcare",
        provider_symbol="NIFTY HEALTHCARE",
        group="healthcare",
        cyclicality="defensive",
    ),
    SectorDefinition(
        "NIFTY_CONSUMER_DURABLES",
        "Nifty Consumer Durables",
        provider_symbol="NIFTY CONSUMER DURABLES",
        group="consumption",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_OIL_GAS",
        "Nifty Oil & Gas",
        provider_symbol="NIFTY OIL & GAS",
        group="commodity",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_ENERGY",
        "Nifty Energy",
        provider_symbol="NIFTY ENERGY",
        group="commodity",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_INFRASTRUCTURE",
        "Nifty Infrastructure",
        provider_symbol="NIFTY INFRASTRUCTURE",
        group="cyclical",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_COMMODITIES",
        "Nifty Commodities",
        provider_symbol="NIFTY COMMODITIES",
        group="commodity",
        cyclicality="cyclical",
    ),
    SectorDefinition(
        "NIFTY_SERVICES_SECTOR",
        "Nifty Services Sector",
        provider_symbol="NIFTY SERVICES SECTOR",
        group="other",
        cyclicality=None,
    ),
)

SECTOR_DEFINITIONS_BY_CODE: dict[str, SectorDefinition] = {
    definition.code: definition for definition in SECTOR_DEFINITIONS
}

SECTOR_INDEX_CODES: tuple[str, ...] = tuple(
    definition.code for definition in SECTOR_DEFINITIONS
)

#: Sector codes that already exist in the canonical index catalog under a
#: different category (``indian_index``). Reused rather than duplicated.
SECTOR_CODES_SHARED_WITH_INDEX_CATALOG: frozenset[str] = frozenset(
    {"NIFTY_BANK", "NIFTY_FIN_SERV"}
)


def sector_group_codes() -> dict[str, tuple[str, ...]]:
    """Map each high-level group to its sector codes (deterministic order)."""
    grouped: dict[str, list[str]] = {}
    for definition in SECTOR_DEFINITIONS:
        grouped.setdefault(definition.group, []).append(definition.code)
    return {group: tuple(codes) for group, codes in grouped.items()}


def cyclical_codes() -> tuple[str, ...]:
    return tuple(
        definition.code
        for definition in SECTOR_DEFINITIONS
        if definition.cyclicality == "cyclical"
    )


def defensive_codes() -> tuple[str, ...]:
    return tuple(
        definition.code
        for definition in SECTOR_DEFINITIONS
        if definition.cyclicality == "defensive"
    )