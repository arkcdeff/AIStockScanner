"""Service for importing official Sector and Industry Classification.

Implements transactional, idempotent classification mapping for the NSE equity universe.

Invariants:
  - Excludes SYNTHETIC_FIXTURE_SYMBOLS.
  - Rejects dummy/exchange test records.
  - Matches primary key ISIN first; symbol fallback second.
  - Never creates duplicate Stock records.
  - Normalizes Sector and Industry tables.
  - Enforces Stock.industry_id -> Industry.sector_id == Stock.sector_id.
  - Synchronizes legacy Stock.industry to Industry.name.
  - Supports dry-run without persisting changes.
  - 100% idempotent: repeat execution produces zero duplicate rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.data.sector_classification_parser import SectorClassificationParser
from app.models.industry import Industry
from app.models.sector import Sector
from app.models.stock import Stock


@dataclass(slots=True)
class SectorClassificationImportResult:
    """Detailed summary of sector classification import execution."""

    total_source_rows: int = 0
    valid_source_rows: int = 0
    dummy_rows: int = 0
    parse_errors: list[str] = field(default_factory=list)

    sectors_created: int = 0
    sectors_existing: int = 0
    industries_created: int = 0
    industries_existing: int = 0

    stocks_matched_isin: int = 0
    stocks_matched_symbol: int = 0
    total_stocks_matched: int = 0
    stocks_updated: int = 0
    legacy_industry_changed: int = 0

    unmatched_source_records: list[dict[str, Any]] = field(default_factory=list)
    unmatched_product_stocks_count: int = 0
    conflicts: list[str] = field(default_factory=list)
    dry_run: bool = False
    success: bool = False


class SectorClassificationService:
    """Service to parse and ingest official exchange sector & industry classifications."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def import_classification(
        self,
        file_path: str | Path,
        dry_run: bool = False,
    ) -> SectorClassificationImportResult:
        """Parse, validate, and import sector/industry classification dataset."""
        result = SectorClassificationImportResult(dry_run=dry_run)

        # 1. Parse official CSV
        parse_res = SectorClassificationParser.parse_file(file_path)
        result.total_source_rows = parse_res.total_rows
        result.valid_source_rows = parse_res.valid_rows
        result.dummy_rows = len(parse_res.dummy_records)
        result.parse_errors = parse_res.errors

        if parse_res.errors and not parse_res.records:
            result.success = False
            return result

        # 2. Fetch existing sectors and industries
        sec_stmt = select(Sector)
        existing_sectors = list((await self.session.execute(sec_stmt)).scalars().all())
        sectors_by_name: dict[str, Sector] = {s.name.strip(): s for s in existing_sectors}

        ind_stmt = select(Industry)
        existing_industries = list((await self.session.execute(ind_stmt)).scalars().all())
        industries_by_name: dict[str, Industry] = {i.name.strip(): i for i in existing_industries}

        # 3. Fetch eligible product stocks
        stock_stmt = (
            select(Stock)
            .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
        )
        prod_stocks = list((await self.session.execute(stock_stmt)).scalars().all())

        stocks_by_isin: dict[str, Stock] = {s.isin.strip(): s for s in prod_stocks if s.isin}
        stocks_by_symbol: dict[str, Stock] = {s.symbol.strip().upper(): s for s in prod_stocks if s.symbol}

        matched_stock_ids: set[int] = set()
        matched_isins: set[str] = set()

        # 4. Process records
        for rec in parse_res.records:
            if rec.is_dummy:
                continue

            taxonomy_val = rec.industry.strip()
            if not taxonomy_val:
                continue

            # Upsert Sector entity
            if taxonomy_val in sectors_by_name:
                sector = sectors_by_name[taxonomy_val]
                result.sectors_existing += 1
            else:
                sector = Sector(name=taxonomy_val)
                self.session.add(sector)
                await self.session.flush()
                sectors_by_name[taxonomy_val] = sector
                result.sectors_created += 1

            # Upsert Industry entity
            if taxonomy_val in industries_by_name:
                industry = industries_by_name[taxonomy_val]
                if industry.sector_id != sector.id:
                    industry.sector_id = sector.id
                result.industries_existing += 1
            else:
                industry = Industry(name=taxonomy_val, sector_id=sector.id)
                self.session.add(industry)
                await self.session.flush()
                industries_by_name[taxonomy_val] = industry
                result.industries_created += 1

            # Resolve stock: primary key ISIN first
            matched_stock: Stock | None = None

            if rec.isin and rec.isin in stocks_by_isin:
                matched_stock = stocks_by_isin[rec.isin]
                result.stocks_matched_isin += 1
            elif rec.symbol and rec.symbol in stocks_by_symbol:
                matched_stock = stocks_by_symbol[rec.symbol]
                result.stocks_matched_symbol += 1
                result.conflicts.append(
                    f"Symbol fallback match for {rec.symbol}: Source ISIN={rec.isin} vs DB ISIN={matched_stock.isin}"
                )

            if matched_stock:
                matched_stock_ids.add(matched_stock.id)
                if rec.isin:
                    matched_isins.add(rec.isin)

                # Track legacy string change
                if matched_stock.industry and matched_stock.industry != taxonomy_val:
                    result.legacy_industry_changed += 1

                # Update hierarchy
                needs_update = (
                    matched_stock.sector_id != sector.id
                    or matched_stock.industry_id != industry.id
                    or matched_stock.industry != taxonomy_val
                )

                if needs_update:
                    matched_stock.sector_id = sector.id
                    matched_stock.industry_id = industry.id
                    matched_stock.industry = taxonomy_val
                    result.stocks_updated += 1
            else:
                result.unmatched_source_records.append(
                    {
                        "symbol": rec.symbol,
                        "isin": rec.isin,
                        "company_name": rec.company_name,
                        "industry": rec.industry,
                    }
                )

        result.total_stocks_matched = len(matched_stock_ids)
        result.unmatched_product_stocks_count = len(prod_stocks) - len(matched_stock_ids)

        if dry_run:
            logger.info("Dry run requested; rolling back classification changes.")
            await self.session.rollback()
        else:
            logger.info("Committing sector and industry classification changes.")
            await self.session.commit()

        result.success = True
        return result
