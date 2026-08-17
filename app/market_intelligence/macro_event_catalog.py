"""Canonical macro & scheduled event catalog for Task 18.8.

This module declares the deterministic metadata that drives Task 18.8's
engine: which economic events are tracked for Indian equities, how important
they are, how frequently they recur, whether a higher surprise is supportive
or restrictive for equities, whether a consensus forecast is normally
available, and the surprise magnitude that maps to a full-scale directional
signal.

The catalog is intentionally declarative metadata, not data. Actual,
forecast and previous values arrive only from a validated provider or the
persisted ``EconEvent`` table. No value here is ever invented.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MacroEventDef:
    code: str
    name: str
    category: str
    importance: str
    frequency: str
    direction: str
    has_forecast: bool
    timezone: str
    unit: str
    surprise_full_scale: float


# Direction semantics: ``normal`` means a positive surprise is supportive for
# equities; ``inverted`` means a positive surprise is restrictive (e.g. hotter
# inflation, higher unemployment).
DIRECTION_NORMAL = "normal"
DIRECTION_INVERTED = "inverted"

CATEGORY_MONETARY = "monetary_policy"
CATEGORY_INFLATION = "inflation"
CATEGORY_GROWTH = "growth"
CATEGORY_EMPLOYMENT = "employment"
CATEGORY_BUSINESS_ACTIVITY = "business_activity"
CATEGORY_FISCAL = "fiscal"
CATEGORY_EXTERNAL = "external"
CATEGORY_SECTOR = "sector"

INDIA_TZ = "Asia/Kolkata"
US_TZ = "America/New_York"

UNIT_PERCENT = "PERCENT"
UNIT_PERCENT_CHANGE = "PERCENT_CHANGE"
UNIT_BASIS_POINTS = "BASIS_POINTS"
UNIT_INDEX_POINTS = "INDEX_POINTS"
UNIT_MILLION = "MILLION"
UNIT_CRORE = "CRORE"
UNIT_THOUSAND = "THOUSAND"


# Core (primary) events tracked for Indian equities: RBI/Fed monetary policy,
# India inflation/growth/activity, and the headline US releases that move the
# risk appetite feeding into Indian markets.
CORE_EVENT_DEFS: tuple[MacroEventDef, ...] = (
    MacroEventDef(
        code="RBI_POLICY_DECISION",
        name="RBI Monetary Policy Decision",
        category=CATEGORY_MONETARY,
        importance="high",
        frequency="bi_monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_BASIS_POINTS,
        surprise_full_scale=50.0,
    ),
    MacroEventDef(
        code="RBI_REPO_RATE",
        name="RBI Repo Rate",
        category=CATEGORY_MONETARY,
        importance="high",
        frequency="bi_monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=0.50,
    ),
    MacroEventDef(
        code="INDIA_CPI",
        name="India Consumer Price Index (YoY)",
        category=CATEGORY_INFLATION,
        importance="high",
        frequency="monthly",
        direction=DIRECTION_INVERTED,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=1.0,
    ),
    MacroEventDef(
        code="INDIA_WPI",
        name="India Wholesale Price Index (YoY)",
        category=CATEGORY_INFLATION,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_INVERTED,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=1.0,
    ),
    MacroEventDef(
        code="INDIA_GDP",
        name="India GDP Growth (YoY)",
        category=CATEGORY_GROWTH,
        importance="high",
        frequency="quarterly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=1.0,
    ),
    MacroEventDef(
        code="INDIA_IIP",
        name="India Index of Industrial Production (YoY)",
        category=CATEGORY_GROWTH,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=2.0,
    ),
    MacroEventDef(
        code="INDIA_PMI_MANUFACTURING",
        name="India Manufacturing PMI",
        category=CATEGORY_BUSINESS_ACTIVITY,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_INDEX_POINTS,
        surprise_full_scale=5.0,
    ),
    MacroEventDef(
        code="INDIA_PMI_SERVICES",
        name="India Services PMI",
        category=CATEGORY_BUSINESS_ACTIVITY,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_INDEX_POINTS,
        surprise_full_scale=5.0,
    ),
    MacroEventDef(
        code="FED_POLICY_DECISION",
        name="US Federal Reserve Policy Decision",
        category=CATEGORY_MONETARY,
        importance="high",
        frequency="irregular",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_BASIS_POINTS,
        surprise_full_scale=50.0,
    ),
    MacroEventDef(
        code="FED_FUNDS_RATE",
        name="US Fed Funds Target Range Midpoint",
        category=CATEGORY_MONETARY,
        importance="high",
        frequency="irregular",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=0.50,
    ),
    MacroEventDef(
        code="US_CPI",
        name="US Consumer Price Index (YoY)",
        category=CATEGORY_INFLATION,
        importance="high",
        frequency="monthly",
        direction=DIRECTION_INVERTED,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=1.0,
    ),
    MacroEventDef(
        code="US_PPI",
        name="US Producer Price Index (YoY)",
        category=CATEGORY_INFLATION,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_INVERTED,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=1.0,
    ),
    MacroEventDef(
        code="US_NONFARM_PAYROLLS",
        name="US Non-Farm Payrolls",
        category=CATEGORY_EMPLOYMENT,
        importance="high",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_THOUSAND,
        surprise_full_scale=200.0,
    ),
    MacroEventDef(
        code="US_UNEMPLOYMENT",
        name="US Unemployment Rate",
        category=CATEGORY_EMPLOYMENT,
        importance="high",
        frequency="monthly",
        direction=DIRECTION_INVERTED,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=0.50,
    ),
    MacroEventDef(
        code="US_ISM_MANUFACTURING",
        name="US ISM Manufacturing PMI",
        category=CATEGORY_BUSINESS_ACTIVITY,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_INDEX_POINTS,
        surprise_full_scale=5.0,
    ),
    MacroEventDef(
        code="US_ISM_SERVICES",
        name="US ISM Services PMI",
        category=CATEGORY_BUSINESS_ACTIVITY,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_INDEX_POINTS,
        surprise_full_scale=5.0,
    ),
    MacroEventDef(
        code="US_RETAIL_SALES",
        name="US Retail Sales (MoM)",
        category=CATEGORY_GROWTH,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=US_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=1.0,
    ),
    MacroEventDef(
        code="INDIA_TRADE_BALANCE",
        name="India Trade Balance",
        category=CATEGORY_EXTERNAL,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_CRORE,
        surprise_full_scale=50000.0,
    ),
)


# Sector-relevant scheduled datasets published by Indian official bodies
# (RBI, MoSPI, CCEA, SIAM, etc.) that map onto sector strength.
SECTOR_EVENT_DEFS: tuple[MacroEventDef, ...] = (
    MacroEventDef(
        code="INDIA_GST_COLLECTIONS",
        name="India GST Collections",
        category=CATEGORY_FISCAL,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_CRORE,
        surprise_full_scale=20000.0,
    ),
    MacroEventDef(
        code="INDIA_BANK_CREDIT_GROWTH",
        name="Scheduled Commercial Bank Credit Growth (YoY)",
        category=CATEGORY_SECTOR,
        importance="medium",
        frequency="fortnightly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=2.0,
    ),
    MacroEventDef(
        code="INDIA_CREDIT_DEPOSIT_GROWTH",
        name="Scheduled Commercial Bank Deposit Growth (YoY)",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="fortnightly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=2.0,
    ),
    MacroEventDef(
        code="INDIA_AUTO_DOMESTIC_SALES",
        name="India Domestic Auto Sales",
        category=CATEGORY_SECTOR,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_THOUSAND,
        surprise_full_scale=100.0,
    ),
    MacroEventDef(
        code="INDIA_EIGHT_CORE_INFRA_GROWTH",
        name="India Eight Core Infrastructure Output (YoY)",
        category=CATEGORY_GROWTH,
        importance="medium",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=True,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT,
        surprise_full_scale=5.0,
    ),
    MacroEventDef(
        code="INDIA_COAL_PRODUCTION",
        name="India Coal Production (YoY)",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=10.0,
    ),
    MacroEventDef(
        code="INDIA_STEEL_PRODUCTION",
        name="India Crude Steel Production (YoY)",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=10.0,
    ),
    MacroEventDef(
        code="INDIA_CEMENT_PRODUCTION",
        name="India Cement Production (YoY)",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=10.0,
    ),
    MacroEventDef(
        code="INDIA_ELECTRICITY_PRODUCTION",
        name="India Electricity Generation (YoY)",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=10.0,
    ),
    MacroEventDef(
        code="INDIA_CRUDE_OIL_IMPORTS",
        name="India Crude Oil Imports (YoY)",
        category=CATEGORY_EXTERNAL,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=10.0,
    ),
    MacroEventDef(
        code="INDIA_GOLD_IMPORTS",
        name="India Gold Imports",
        category=CATEGORY_EXTERNAL,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_CRORE,
        surprise_full_scale=10000.0,
    ),
    MacroEventDef(
        code="INDIA_FERTILIZER_PRODUCTION",
        name="India Fertilizer Production",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=10.0,
    ),
    MacroEventDef(
        code="INDIA_PHARMA_EXPORTS",
        name="India Pharmaceutical Exports",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_CRORE,
        surprise_full_scale=5000.0,
    ),
    MacroEventDef(
        code="INDIA_IT_SERVICES_EXPORTS",
        name="India IT Services Exports",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_CRORE,
        surprise_full_scale=5000.0,
    ),
    MacroEventDef(
        code="INDIA_RAILWAY_FREIGHT",
        name="Indian Railways Freight Loadings",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_THOUSAND,
        surprise_full_scale=5000.0,
    ),
    MacroEventDef(
        code="INDIA_PORT_CARGO",
        name="Major Port Cargo Handling (YoY)",
        category=CATEGORY_SECTOR,
        importance="low",
        frequency="monthly",
        direction=DIRECTION_NORMAL,
        has_forecast=False,
        timezone=INDIA_TZ,
        unit=UNIT_PERCENT_CHANGE,
        surprise_full_scale=5.0,
    ),
)


MACRO_EVENT_CATALOG: tuple[MacroEventDef, ...] = (
    CORE_EVENT_DEFS + SECTOR_EVENT_DEFS
)

MACRO_EVENT_BY_CODE: dict[str, MacroEventDef] = {
    definition.code: definition for definition in MACRO_EVENT_CATALOG
}

CORE_EVENT_CODES: frozenset[str] = frozenset(
    definition.code for definition in CORE_EVENT_DEFS
)
SECTOR_EVENT_CODES: frozenset[str] = frozenset(
    definition.code for definition in SECTOR_EVENT_DEFS
)

VALID_MACRO_EVENT_CODES: frozenset[str] = frozenset(MACRO_EVENT_BY_CODE)


def get_macro_event(code: str) -> MacroEventDef | None:
    return MACRO_EVENT_BY_CODE.get(code)


def validate_macro_event_catalog() -> None:
    """Fail fast if the declarative catalog is internally inconsistent."""
    seen: set[str] = set()
    for definition in MACRO_EVENT_CATALOG:
        if definition.code in seen:
            raise ValueError(f"Duplicate macro event code: {definition.code}")
        seen.add(definition.code)
        if definition.importance not in {"high", "medium", "low"}:
            raise ValueError(f"Bad importance for {definition.code}")
        if definition.direction not in {DIRECTION_NORMAL, DIRECTION_INVERTED}:
            raise ValueError(f"Bad direction for {definition.code}")
        if not definition.name.strip():
            raise ValueError(f"Empty name for {definition.code}")
        if not definition.timezone:
            raise ValueError(f"Empty timezone for {definition.code}")
        if not definition.surprise_full_scale > 0:
            raise ValueError(f"Non-positive surprise_full_scale for {definition.code}")
    if seen != VALID_MACRO_EVENT_CODES:
        raise ValueError("Catalog code set diverges from VALID_MACRO_EVENT_CODES")
    if CORE_EVENT_CODES.intersection(SECTOR_EVENT_CODES):
        raise ValueError("Core and sector event sets must be disjoint")


validate_macro_event_catalog()
