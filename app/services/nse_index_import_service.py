"""Official NSE Index Price Ingestion Service — Stage 2 Phase G.4.

Provides transactional, validated, and idempotent ingestion of official NSE
index closing records (ind_close_all_DDMMYYYY.csv) into MarketIndexPrice.
Reuses the authoritative NSEIndexParser and strictly populates existing
MarketIndexPrice records without creating duplicate index/date rows.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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

    async def import_vix_chunk(
        self,
        file_path: str | Path,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Import a single official NSE India VIX historical JSON chunk into MarketIndexPrice."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"VIX chunk file not found: {p}")

        raw_data = json.loads(p.read_text(encoding="utf-8"))
        rows = raw_data.get("data", [])

        idx_cache = await self._ensure_index_cache()
        vix_id = idx_cache.get("INDIA_VIX")
        if not vix_id:
            raise RuntimeError("MarketIndex with code 'INDIA_VIX' not found in database.")

        stats = {
            "file": p.name,
            "total_rows": len(rows),
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "errors": [],
        }

        if not rows:
            return stats

        # Fetch existing VIX prices
        trade_dates = []
        parsed_rows = []
        for r in rows:
            try:
                # EOD_TIMESTAMP format: "DD-Mon-YYYY" e.g. "01-JAN-2026"
                ts_str = r.get("EOD_TIMESTAMP", "").strip()
                t_date = datetime.strptime(ts_str, "%d-%b-%Y").date()
                o = Decimal(str(r["EOD_OPEN_INDEX_VAL"]))
                h = Decimal(str(r["EOD_HIGH_INDEX_VAL"]))
                lo = Decimal(str(r["EOD_LOW_INDEX_VAL"]))
                c = Decimal(str(r["EOD_CLOSE_INDEX_VAL"]))
                pc = Decimal(str(r["EOD_PREV_CLOSE"])) if r.get("EOD_PREV_CLOSE") is not None else None
                chg = Decimal(str(r["VIX_PTS_CHG"])) if r.get("VIX_PTS_CHG") is not None else None
                pct = Decimal(str(r["VIX_PERC_CHG"])) if r.get("VIX_PERC_CHG") is not None else None
                trade_dates.append(t_date)
                parsed_rows.append((t_date, o, h, lo, c, pc, chg, pct))
            except Exception as exc:
                stats["errors"].append(f"Failed parsing row {r}: {exc}")

        stmt = select(MarketIndexPrice).where(
            MarketIndexPrice.market_index_id == vix_id,
            MarketIndexPrice.trade_date.in_(trade_dates),
        )
        existing_res = await self.session.execute(stmt)
        existing_by_date = {mip.trade_date: mip for mip in existing_res.scalars().all()}

        records_to_insert: list[MarketIndexPrice] = []

        for t_date, o, h, lo, c, pc, chg, pct in parsed_rows:
            existing = existing_by_date.get(t_date)
            if existing is not None:
                has_changes = (
                    existing.open != o
                    or existing.high != h
                    or existing.low != lo
                    or existing.close != c
                    or (pc is not None and existing.previous_close != pc)
                    or (chg is not None and existing.change != chg)
                    or (pct is not None and existing.change_percent != pct)
                )
                if has_changes:
                    if not dry_run:
                        existing.open = o
                        existing.high = h
                        existing.low = lo
                        existing.close = c
                        existing.previous_close = pc
                        existing.change = chg
                        existing.change_percent = pct
                        existing.source_name = "NSE_HISTORICAL_VIX"
                        existing.source_reference = p.name
                        existing.freshness_status = "recent"
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
            else:
                if not dry_run:
                    new_mip = MarketIndexPrice(
                        market_index_id=vix_id,
                        trade_date=t_date,
                        open=o,
                        high=h,
                        low=lo,
                        close=c,
                        previous_close=pc,
                        change=chg,
                        change_percent=pct,
                        volume=None,
                        observed_at=t_date,
                        fetched_at=t_date,
                        source_name="NSE_HISTORICAL_VIX",
                        source_reference=p.name,
                        freshness_status="recent",
                    )
                    records_to_insert.append(new_mip)
                stats["inserted"] += 1

        if not dry_run and records_to_insert:
            self.session.add_all(records_to_insert)

        if not dry_run:
            await self.session.commit()

        return stats

    async def import_vix_directory(
        self,
        dir_path: str | Path,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Batch import all official NSE India VIX JSON chunks in directory."""
        d = Path(dir_path)
        if not d.is_dir():
            raise NotADirectoryError(f"Directory not found: {d}")

        files = sorted(d.glob("nse_vix_history_*.json"))
        aggregate = {
            "files_processed": 0,
            "total_rows": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "file_results": [],
        }

        for f in files:
            res = await self.import_vix_chunk(f, dry_run=dry_run)
            aggregate["files_processed"] += 1
            aggregate["total_rows"] += res["total_rows"]
            aggregate["inserted"] += res["inserted"]
            aggregate["updated"] += res["updated"]
            aggregate["unchanged"] += res["unchanged"]
            aggregate["file_results"].append(res)

        return aggregate
