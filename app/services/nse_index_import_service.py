"""Official NSE Index Price Ingestion Service — Stage 2 Phase G.4.

Provides transactional, validated, and idempotent ingestion of official NSE
index closing records (ind_close_all_DDMMYYYY.csv) into MarketIndexPrice.
Reuses the authoritative NSEIndexParser and strictly populates existing
MarketIndexPrice records without creating duplicate index/date rows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.nse_index_parser import NSEIndexParser, ParsedIndexRecord
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IndexFileResult:
    """Audit result for a single index file import."""

    file_name: str
    trade_date: date | None = None
    total_rows_parsed: int = 0
    target_indices_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    rejected_count: int = 0
    errors: list[str] = field(default_factory=list)
    success: bool = True
    dry_run: bool = False


@dataclass(slots=True)
class IndexBatchImportResult:
    """Aggregate result for multiple index files import."""

    files_processed: int = 0
    files_failed: int = 0
    total_parsed: int = 0
    inserted_total: int = 0
    updated_total: int = 0
    unchanged_total: int = 0
    rejected_total: int = 0
    file_results: list[IndexFileResult] = field(default_factory=list)
    success: bool = True
    dry_run: bool = False


class NSEIndexImportService:
    """Service layer for importing official NSE index price datasets."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = MarketIndexPriceRepository(session)
        self._index_cache: dict[str, int] | None = None

    async def _ensure_index_cache(self) -> dict[str, int]:
        """Load and cache mapping from MarketIndex.code to MarketIndex.id."""
        if self._index_cache is None:
            stmt = select(MarketIndex.code, MarketIndex.id)
            res = await self.session.execute(stmt)
            self._index_cache = {code: mid for code, mid in res.all()}
        return self._index_cache

    async def import_index_file(
        self,
        file_path: str | Path,
        *,
        dry_run: bool = False,
    ) -> IndexFileResult:
        """Parse and import a single official NSE index CSV file."""
        p = Path(file_path)
        result = IndexFileResult(file_name=p.name, dry_run=dry_run)

        try:
            parse_res = NSEIndexParser.parse_file(p)
        except Exception as exc:
            logger.error("Failed to parse index file %s: %s", p.name, exc)
            result.errors.append(str(exc))
            result.success = False
            return result

        result.total_rows_parsed = parse_res.total_rows
        result.rejected_count = len(parse_res.rejected_rows)

        if not parse_res.records:
            return result

        index_cache = await self._ensure_index_cache()
        trade_date = parse_res.records[0].trade_date
        result.trade_date = trade_date

        # Fetch existing records for this trade_date
        target_records: list[ParsedIndexRecord] = []
        for rec in parse_res.records:
            if rec.index_code in index_cache:
                target_records.append(rec)
            else:
                result.rejected_count += 1
                result.errors.append(f"Index code '{rec.index_code}' not found in catalog")

        result.target_indices_count = len(target_records)
        if not target_records:
            return result

        # Batch query existing records for this trade date
        index_ids = [index_cache[r.index_code] for r in target_records]
        stmt = select(MarketIndexPrice).where(
            MarketIndexPrice.market_index_id.in_(index_ids),
            MarketIndexPrice.trade_date == trade_date,
        )
        existing_res = await self.session.execute(stmt)
        existing_by_index_id = {mip.market_index_id: mip for mip in existing_res.scalars().all()}

        records_to_insert: list[MarketIndexPrice] = []

        for rec in target_records:
            idx_id = index_cache[rec.index_code]
            existing = existing_by_index_id.get(idx_id)

            if existing is not None:
                # Check for changes
                has_changes = (
                    existing.open != rec.open
                    or existing.high != rec.high
                    or existing.low != rec.low
                    or existing.close != rec.close
                    or (rec.volume is not None and existing.volume != rec.volume)
                    or (rec.change is not None and existing.change != rec.change)
                    or (rec.change_percent is not None and existing.change_percent != rec.change_percent)
                    or (rec.previous_close is not None and existing.previous_close != rec.previous_close)
                )
                if has_changes:
                    if not dry_run:
                        existing.open = rec.open
                        existing.high = rec.high
                        existing.low = rec.low
                        existing.close = rec.close
                        existing.previous_close = rec.previous_close
                        existing.change = rec.change
                        existing.change_percent = rec.change_percent
                        existing.volume = rec.volume
                        existing.source_name = rec.source_name
                        existing.source_reference = rec.source_reference
                        existing.freshness_status = "recent"
                    result.updated_count += 1
                else:
                    result.unchanged_count += 1
            else:
                # New record
                if not dry_run:
                    new_mip = MarketIndexPrice(
                        market_index_id=idx_id,
                        trade_date=rec.trade_date,
                        open=rec.open,
                        high=rec.high,
                        low=rec.low,
                        close=rec.close,
                        previous_close=rec.previous_close,
                        change=rec.change,
                        change_percent=rec.change_percent,
                        volume=rec.volume,
                        observed_at=rec.trade_date,
                        fetched_at=rec.trade_date,
                        source_name=rec.source_name,
                        source_reference=rec.source_reference,
                        freshness_status="recent",
                    )
                    records_to_insert.append(new_mip)
                result.inserted_count += 1

        if not dry_run and records_to_insert:
            self.session.add_all(records_to_insert)

        if not dry_run:
            await self.session.commit()

        return result

    async def import_index_directory(
        self,
        dir_path: str | Path,
        *,
        dry_run: bool = False,
    ) -> IndexBatchImportResult:
        """Batch import all official NSE index CSV files from a directory."""
        d = Path(dir_path)
        if not d.is_dir():
            raise NotADirectoryError(f"Directory not found: {d}")

        files = sorted(d.glob("ind_close_all_*.csv"))
        batch_result = IndexBatchImportResult(dry_run=dry_run)

        for f in files:
            file_res = await self.import_index_file(f, dry_run=dry_run)
            batch_result.files_processed += 1
            batch_result.file_results.append(file_res)
            if not file_res.success:
                batch_result.files_failed += 1
                batch_result.success = False

            batch_result.total_parsed += file_res.total_rows_parsed
            batch_result.inserted_total += file_res.inserted_count
            batch_result.updated_total += file_res.updated_count
            batch_result.unchanged_total += file_res.unchanged_count
            batch_result.rejected_total += file_res.rejected_count

        return batch_result
