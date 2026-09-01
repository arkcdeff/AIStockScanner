"""Product-facing provenance rules for separating fixtures from market data.

The fixture symbols originate in persistence-focused tests and remain valid test
data.  Normal research surfaces must exclude them without deleting audit rows.
"""

from __future__ import annotations

from collections.abc import Collection

SYNTHETIC_FIXTURE_SYMBOLS = frozenset(
    {
        "REPRO1",
        "ANLX1",
        "ANALYTX",
        "APIT1",
        "RESL1",
        "RESL2",
        "BENCH1",
        "MMVERIFY",
    }
)

NON_PRODUCTION_AI_PROVIDERS = frozenset({"mock"})


def is_product_symbol(symbol: str | None) -> bool:
    """Return whether a symbol is eligible for normal product research views."""
    return bool(symbol and symbol.upper() not in SYNTHETIC_FIXTURE_SYMBOLS)


def product_symbols(symbols: Collection[str]) -> list[str]:
    """Filter a symbol collection using the shared product provenance policy."""
    return [symbol for symbol in symbols if is_product_symbol(symbol)]


NON_PRODUCTION_SCANNER_CODES = frozenset({"test_scanner"})
NON_PRODUCTION_SCANNER_CATEGORIES = frozenset({"test"})


def is_product_scanner(code: str | None, category: str | None = None) -> bool:
    """Return whether a scanner definition is eligible for normal product research views."""
    if not code:
        return False
    c_lower = code.strip().lower()
    if c_lower in NON_PRODUCTION_SCANNER_CODES or c_lower.startswith("test_"):
        return False
    if category and category.strip().lower() in NON_PRODUCTION_SCANNER_CATEGORIES:
        return False
    return True
