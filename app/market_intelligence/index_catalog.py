from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.sector_universe import (
    SECTOR_CODES_SHARED_WITH_INDEX_CATALOG,
    SECTOR_DEFINITIONS,
)
from app.models.market_index import MarketIndex


@dataclass(frozen=True, slots=True)
class IndexCatalogEntry:
    code: str
    name: str
    country: str
    exchange: str
    category: str
    currency: str | None
    timezone: str
    provider_symbol: str


INDIAN_INDEX_CODES: tuple[str, ...] = (
    "NIFTY_50",
    "NIFTY_BANK",
    "NIFTY_FIN_SERV",
    "NIFTY_MID_SELECT",
    "NIFTY_MIDCAP_100",
    "NIFTY_SMALLCAP_100",
    "NIFTY_NEXT_50",
    "SENSEX",
)

US_INDEX_CODES: tuple[str, ...] = ("SP500", "NASDAQ_100", "DOW_JONES")

ASIAN_INDEX_CODES: tuple[str, ...] = (
    "NIKKEI_225",
    "HANG_SENG",
    "SHANGHAI_COMPOSITE",
    "KOSPI",
)

GLOBAL_INTELLIGENCE_INDEX_CODES: tuple[str, ...] = US_INDEX_CODES + ASIAN_INDEX_CODES

GIFT_NIFTY_INDEX_CODE = "GIFT_NIFTY"

INDIA_VIX_INDEX_CODE = "INDIA_VIX"
USD_INR_INDEX_CODE = "USD_INR"
BRENT_CRUDE_INDEX_CODE = "BRENT_CRUDE"
MARKET_FACTOR_INDEX_CODES: tuple[str, ...] = (
    INDIA_VIX_INDEX_CODE,
    USD_INR_INDEX_CODE,
    BRENT_CRUDE_INDEX_CODE,
)


CORE_INDEX_CATALOG: tuple[IndexCatalogEntry, ...] = (
    IndexCatalogEntry(
        "NIFTY_50",
        "Nifty 50",
        "INDIA",
        "NSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "NIFTY50",
    ),
    IndexCatalogEntry(
        "NIFTY_BANK",
        "Nifty Bank",
        "INDIA",
        "NSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "BANKNIFTY",
    ),
    IndexCatalogEntry(
        "NIFTY_FIN_SERV",
        "Nifty Financial Services",
        "INDIA",
        "NSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "FINNIFTY",
    ),
    IndexCatalogEntry(
        "NIFTY_MID_SELECT",
        "Nifty Midcap Select",
        "INDIA",
        "NSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "MIDCPNIFTY",
    ),
    IndexCatalogEntry(
        "NIFTY_MIDCAP_100",
        "Nifty Midcap 100",
        "INDIA",
        "NSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "NIFTIMIDCP100",
    ),
    IndexCatalogEntry(
        "NIFTY_SMALLCAP_100",
        "Nifty Smallcap 100",
        "INDIA",
        "NSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "NIFTISMALLCAP100",
    ),
    IndexCatalogEntry(
        "NIFTY_NEXT_50",
        "Nifty Next 50",
        "INDIA",
        "NSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "NIFTINEXT50",
    ),
    IndexCatalogEntry(
        "SENSEX",
        "BSE Sensex",
        "INDIA",
        "BSE",
        "indian_index",
        "INR",
        "Asia/Kolkata",
        "SENSEX",
    ),
    IndexCatalogEntry(
        GIFT_NIFTY_INDEX_CODE,
        "GIFT Nifty Futures",
        "INDIA",
        "NSE_IX",
        "index_futures",
        "USD",
        "Asia/Kolkata",
        "NIFTY",
    ),
    IndexCatalogEntry(
        INDIA_VIX_INDEX_CODE,
        "India VIX",
        "INDIA",
        "NSE",
        "volatility_index",
        None,
        "Asia/Kolkata",
        "INDIAVIX",
    ),
    IndexCatalogEntry(
        USD_INR_INDEX_CODE,
        "USD/INR Spot",
        "INDIA",
        "FX_SPOT",
        "currency_spot",
        "INR",
        "Asia/Kolkata",
        "USDINR_SPOT",
    ),
    IndexCatalogEntry(
        BRENT_CRUDE_INDEX_CODE,
        "Europe Brent Spot",
        "GLOBAL",
        "PHYSICAL_SPOT",
        "commodity_spot",
        "USD",
        "UTC",
        "BRENT_SPOT_EUROPE",
    ),
    IndexCatalogEntry(
        "SP500",
        "S&P 500",
        "USA",
        "NYSE/NASDAQ",
        "global_index",
        "USD",
        "America/New_York",
        "^spx",
    ),
    IndexCatalogEntry(
        "NASDAQ_100",
        "Nasdaq 100",
        "USA",
        "NASDAQ",
        "global_index",
        "USD",
        "America/New_York",
        "^ndx",
    ),
    IndexCatalogEntry(
        "DOW_JONES",
        "Dow Jones Industrial Average",
        "USA",
        "NYSE/NASDAQ",
        "global_index",
        "USD",
        "America/New_York",
        "^dji",
    ),
    IndexCatalogEntry(
        "NIKKEI_225",
        "Nikkei 225",
        "JAPAN",
        "TSE",
        "asian_index",
        "JPY",
        "Asia/Tokyo",
        "^nkx",
    ),
    IndexCatalogEntry(
        "HANG_SENG",
        "Hang Seng Index",
        "HONG_KONG",
        "HKEX",
        "asian_index",
        "HKD",
        "Asia/Hong_Kong",
        "^hsi",
    ),
    IndexCatalogEntry(
        "SHANGHAI_COMPOSITE",
        "Shanghai Composite",
        "CHINA",
        "SSE",
        "asian_index",
        "CNY",
        "Asia/Shanghai",
        "^shc",
    ),
    IndexCatalogEntry(
        "KOSPI",
        "Korea Composite Stock Price Index",
        "SOUTH_KOREA",
        "KRX",
        "asian_index",
        "KRW",
        "Asia/Seoul",
        "^kospi",
    ),
)


def _build_sector_catalog_entries() -> tuple[IndexCatalogEntry, ...]:
    """Sector-index catalog entries derived from the canonical sector universe.

    NIFTY_BANK and NIFTY_FIN_SERV already exist in ``CORE_INDEX_CATALOG`` with
    the same provider symbols; they are intentionally not duplicated here so
    the catalog seeder keeps a single identity per index.
    """
    entries: list[IndexCatalogEntry] = []
    for definition in SECTOR_DEFINITIONS:
        if definition.code in SECTOR_CODES_SHARED_WITH_INDEX_CATALOG:
            continue
        entries.append(
            IndexCatalogEntry(
                code=definition.code,
                name=definition.name,
                country="INDIA",
                exchange=definition.exchange,
                category=definition.category,
                currency="INR",
                timezone="Asia/Kolkata",
                provider_symbol=definition.provider_symbol,
            )
        )
    return tuple(entries)


SECTOR_INDEX_CATALOG: tuple[IndexCatalogEntry, ...] = _build_sector_catalog_entries()

INDEX_CATALOG: tuple[IndexCatalogEntry, ...] = (
    CORE_INDEX_CATALOG + SECTOR_INDEX_CATALOG
)

INDEX_CATALOG_BY_CODE = {entry.code: entry for entry in INDEX_CATALOG}

# G.11-OPT-14C: catalog codes whose economic identity is already carried by an
# existing authoritative MarketIndex row. The derivatives pipeline resolves
# underlyings by the provider short code (``NIFTY``, ``BANKNIFTY``), so when such
# a row exists the seeder must reconcile onto it rather than create a second
# identity for the same index.
CATALOG_CODE_ALIASES: dict[str, tuple[str, ...]] = {
    "NIFTY_50": ("NIFTY",),
    "NIFTY_BANK": ("BANKNIFTY",),
}


async def seed_market_index_catalog(session: AsyncSession) -> list[MarketIndex]:
    """Create or reconcile the canonical catalog without creating duplicates.

    The caller owns the transaction. Existing rows are updated in place so the
    original Task 18.2 table and relationships remain the single source of truth.
    """
    codes = tuple(INDEX_CATALOG_BY_CODE)
    alias_codes = tuple(
        alias for entry_code in codes for alias in CATALOG_CODE_ALIASES.get(entry_code, ())
    )
    result = await session.scalars(
        select(MarketIndex).where(MarketIndex.code.in_(codes + alias_codes))
    )
    existing = {row.code: row for row in result.all()}
    catalog_rows: list[MarketIndex] = []

    for entry in INDEX_CATALOG:
        row = existing.get(entry.code)
        if row is None:
            # G.11-OPT-14C: reconcile onto the authoritative short-code row
            # instead of creating a duplicate identity for the same index. The
            # resolved row keeps its existing code.
            for alias in CATALOG_CODE_ALIASES.get(entry.code, ()):
                if alias in existing:
                    row = existing[alias]
                    break
        if row is None:
            row = MarketIndex(code=entry.code, name=entry.name, country=entry.country)
            session.add(row)
            existing[entry.code] = row
        row.name = entry.name
        row.country = entry.country
        row.exchange = entry.exchange
        row.category = entry.category
        row.currency = entry.currency
        row.timezone = entry.timezone
        row.provider_symbol = entry.provider_symbol
        row.is_active = True
        catalog_rows.append(row)

    await session.flush()
    return catalog_rows
