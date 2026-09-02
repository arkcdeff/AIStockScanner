"""Official NSE Delivery Import Service — Stage 2 Phase G.3.

Provides transactional, validated, and idempotent ingestion of official NSE
security-wise delivery records (sec_bhavdata_full_DDMMYYYY.csv) into DailyPrice.
Reuses the authoritative parse_delivery parser and strictly enriches existing
price records without creating duplicate price rows or mutating OHLCV data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.nse_parser import (
    ALIASES,
    _date,
    _rows,
    _value,
    load_bytes,
    parse_delivery,
)
from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.models.daily_price import DailyPrice
from app.models.stock import Stock

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DeliveryFileResult:
    """Audit result for a single delivery file import."""

    file_name: str
    trade_date: date | None = None
    total_rows_parsed: int = 0
    matched_stocks_count: int = 0
    unmatched_stocks_count: int = 0
    updated_prices_count: int = 0
    unchanged_prices_count: int = 0
    skipped_no_price_count: int = 0
    rejected_validation_count: int = 0
    errors: list[str] = field(default_factory=list)
    success: bool = True
    dry_run: bool = False


@dataclass(slots=True)
class DeliveryImportSummary:
    """Overall summary for a directory or batch of delivery imports."""

    files_processed: int = 0
    files_succeeded: int = 0
    files_failed: int = 0
    total_rows_parsed: int = 0
    total_updated_prices: int = 0
    total_unchanged_prices: int = 0
    total_skipped_no_price: int = 0
    total_rejected_validation: int = 0
    total_unmatched_stocks: int = 0
    file_results: list[DeliveryFileResult] = field(default_factory=list)
    dry_run: bool = False


class DeliveryImportService:
    """Service to ingest and merge official NSE delivery data into DailyPrice."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._stock_map: dict[tuple[str, str], int] | None = None
        self._stock_fallback: dict[str, int] | None = None

    async def _init_stock_maps(self) -> None:
        if self._stock_map is not None:
            return

        stmt = select(Stock.id, Stock.symbol, Stock.series).where(
            Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        stock_map: dict[tuple[str, str], int] = {}
        stock_fallback: dict[str, int] = {}
        for stock_id, symbol, series in rows:
            sym_clean = symbol.strip().upper()
            if series is not None:
                srs_clean = series.strip().upper()
                stock_map[(sym_clean, srs_clean)] = stock_id
            else:
                stock_fallback[sym_clean] = stock_id

        self._stock_map = stock_map
        self._stock_fallback = stock_fallback

    async def import_file(
        self,
        file_path: str | Path,
        trade_date: date | None = None,
        dry_run: bool = False,
    ) -> DeliveryFileResult:
        """Import a single official delivery file, validating and updating DailyPrice."""
        await self._init_stock_maps()
        assert self._stock_map is not None and self._stock_fallback is not None

        path_obj = Path(file_path)
        result = DeliveryFileResult(file_name=path_obj.name, dry_run=dry_run)

        try:
            payload, file_name = load_bytes(path_obj)
        except Exception as exc:
            result.success = False
            result.errors.append(f"Failed to read file: {exc}")
            return result

        try:
            raw_rows = _rows(payload, file_name)
        except Exception as exc:
            result.success = False
            result.errors.append(f"Failed to parse CSV rows: {exc}")
            return result

        if not raw_rows:
            result.errors.append("File is empty or contains only header")
            return result

        # Determine trade date if not explicitly supplied
        if trade_date is None:
            first_date_val = _value(raw_rows[0], ALIASES["trade_date"])
            if first_date_val:
                try:
                    trade_date = _date(first_date_val)
                except Exception as exc:
                    result.success = False
                    result.errors.append(f"Failed to parse trade_date from file: {exc}")
                    return result
            else:
                result.success = False
                result.errors.append("trade_date could not be determined from file")
                return result

        result.trade_date = trade_date

        # Parse delivery records using official parser
        try:
            deliv_records = parse_delivery(payload, file_name)
        except Exception as exc:
            result.success = False
            result.errors.append(f"parse_delivery failed: {exc}")
            return result

        result.total_rows_parsed = len(deliv_records)

        # Query all existing DailyPrice records for this trade_date
        dp_stmt = select(
            DailyPrice.id,
            DailyPrice.stock_id,
            DailyPrice.volume,
            DailyPrice.delivery_quantity,
            DailyPrice.delivery_percentage,
        ).where(DailyPrice.trade_date == trade_date)
        dp_res = await self.session.execute(dp_stmt)
        dp_by_stock_id = {row.stock_id: row for row in dp_res.all()}

        update_mappings: list[dict[str, object]] = []
        seen_stock_ids: set[int] = set()

        for (sym, srs), rec in deliv_records.items():
            # Exclude synthetic fixtures
            if sym in SYNTHETIC_FIXTURE_SYMBOLS:
                continue

            stock_id = self._stock_map.get((sym, srs))
            if stock_id is None:
                stock_id = self._stock_fallback.get(sym)

            if stock_id is None:
                result.unmatched_stocks_count += 1
                continue

            # Prevent secondary series (e.g. rights/partly-paid P1) from overwriting primary EQ
            if stock_id in seen_stock_ids:
                continue
            seen_stock_ids.add(stock_id)

            result.matched_stocks_count += 1

            dp = dp_by_stock_id.get(stock_id)
            if dp is None:
                result.skipped_no_price_count += 1
                continue

            qty = rec.quantity
            pct = rec.percentage

            # Validation rules:
            # 1. quantity >= 0 if present
            # 2. 0 <= percentage <= 100 if present
            # 3. quantity <= volume if volume is positive
            if qty is not None and qty < 0:
                result.rejected_validation_count += 1
                continue
            if pct is not None and (pct < Decimal("0") or pct > Decimal("100")):
                result.rejected_validation_count += 1
                continue
            if qty is not None and dp.volume > 0 and qty > dp.volume:
                result.rejected_validation_count += 1
                continue

            # Idempotency check: only update if value differs
            qty_changed = dp.delivery_quantity != qty
            pct_changed = False
            if dp.delivery_percentage is None and pct is not None:
                pct_changed = True
            elif dp.delivery_percentage is not None and pct is None:
                pct_changed = True
            elif dp.delivery_percentage is not None and pct is not None:
                pct_changed = abs(dp.delivery_percentage - pct) > Decimal("0.0001")

            if qty_changed or pct_changed:
                update_mappings.append(
                    {
                        "id": dp.id,
                        "delivery_quantity": qty,
                        "delivery_percentage": pct,
                    }
                )
            else:
                result.unchanged_prices_count += 1

        result.updated_prices_count = len(update_mappings)

        if not dry_run and update_mappings:
            await self.session.execute(update(DailyPrice), update_mappings)
            await self.session.commit()
        elif dry_run:
            await self.session.rollback()

        return result

    async def import_directory(
        self,
        dir_path: str | Path,
        pattern: str = "sec_bhavdata_full_*.csv",
        dry_run: bool = False,
    ) -> DeliveryImportSummary:
        """Import all delivery files in a directory matching pattern."""
        dir_obj = Path(dir_path)
        files = sorted(dir_obj.glob(pattern))

        summary = DeliveryImportSummary(dry_run=dry_run)

        for file_path in files:
            file_result = await self.import_file(file_path, dry_run=dry_run)
            summary.file_results.append(file_result)
            summary.files_processed += 1

            if file_result.success:
                summary.files_succeeded += 1
            else:
                summary.files_failed += 1

            summary.total_rows_parsed += file_result.total_rows_parsed
            summary.total_updated_prices += file_result.updated_prices_count
            summary.total_unchanged_prices += file_result.unchanged_prices_count
            summary.total_skipped_no_price += file_result.skipped_no_price_count
            summary.total_rejected_validation += file_result.rejected_validation_count
            summary.total_unmatched_stocks += file_result.unmatched_stocks_count

        return summary
