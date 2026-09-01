from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.nse_parser import (
    PriceRecord,
    load_bytes,
    parse_bhavcopy_detailed,
    parse_delivery,
)
from app.models.daily_price import DailyPrice
from app.models.import_job import ImportJob
from app.models.stock import Stock

# Import-job provenance modes used by the market-data pipeline.
MODE_UPLOAD = "UPLOAD"
MODE_DATE_DOWNLOAD = "DATE_DOWNLOAD"
MODE_LATEST_DOWNLOAD = "LATEST_DOWNLOAD"
MODE_RANGE_BACKFILL = "RANGE_BACKFILL"
MODE_AUTO_EOD = "AUTO_EOD"
MODE_AUTO_CATCHUP = "AUTO_CATCHUP"


class NSEImportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def import_files(
        self,
        bhavcopy_path: str | Path,
        delivery_path: str | Path | None = None,
        *,
        mode: str = MODE_UPLOAD,
        archive_path: str | None = None,
        checksum: str | None = None,
    ) -> ImportJob:
        bhavcopy, file_name = load_bytes(bhavcopy_path)
        delivery_payload = load_bytes(delivery_path)[0] if delivery_path else None
        return await self.import_bytes(
            bhavcopy=bhavcopy,
            bhavcopy_name=file_name,
            delivery=delivery_payload,
            delivery_name=Path(delivery_path).name if delivery_path else None,
            mode=mode,
            archive_path=archive_path,
            checksum=checksum,
        )

    async def import_bytes(
        self,
        *,
        bhavcopy: bytes,
        bhavcopy_name: str,
        delivery: bytes | None = None,
        delivery_name: str | None = None,
        mode: str = MODE_UPLOAD,
        archive_path: str | None = None,
        checksum: str | None = None,
    ) -> ImportJob:
        started = datetime.now(UTC)
        job = ImportJob(
            file_name=bhavcopy_name,
            status="running",
            started_at=started,
            details={
                "delivery_file": delivery_name,
                "mode": mode,
                "checksum": checksum or self._sha256(bhavcopy),
            },
        )
        self.session.add(job)
        await self.session.flush()

        try:
            delivery_map = (
                parse_delivery(delivery, delivery_name or "delivery.csv") if delivery else {}
            )
            parsed = parse_bhavcopy_detailed(bhavcopy, bhavcopy_name, delivery_map)
            if not parsed.records:
                raise ValueError("No valid equity records found in the bhavcopy")

            job.trade_date = parsed.records[0].trade_date
            job.rows_read = len(parsed.records) + len(parsed.errors)
            job.rows_rejected = len(parsed.errors)
            inserted, updated = await self._upsert(parsed.records)
            job.rows_inserted = inserted
            job.rows_updated = updated
            job.status = "completed"
            job.details = {
                "delivery_file": delivery_name,
                "mode": mode,
                "format": parsed.detected_format,
                "rows_discovered": parsed.total_rows,
                "rows_skipped_series": parsed.skipped_series,
                "delivery_rows": parsed.delivery_rows,
                "delivery_missing": parsed.delivery_missing,
                "has_delivery_columns": parsed.has_delivery_columns,
                "archive_path": archive_path,
                "checksum": checksum or self._sha256(bhavcopy),
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
                "parse_errors": parsed.errors[:100],
                "valid_rows": len(parsed.records),
            }
            job.completed_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(job)
            return job
        except Exception as exc:
            await self.session.rollback()
            failed_job = ImportJob(
                file_name=bhavcopy_name,
                status="failed",
                rows_read=job.rows_read,
                rows_rejected=job.rows_rejected,
                error_message=str(exc),
                details={
                    "delivery_file": delivery_name,
                    "mode": mode,
                    "checksum": checksum or self._sha256(bhavcopy),
                    "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
                },
                started_at=job.started_at,
                completed_at=datetime.now(UTC),
            )
            self.session.add(failed_job)
            await self.session.commit()
            await self.session.refresh(failed_job)
            return failed_job

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def _upsert(self, records: list[PriceRecord]) -> tuple[int, int]:
        symbols = sorted({record.symbol for record in records})
        existing_symbols = set(
            (
                await self.session.scalars(select(Stock.symbol).where(Stock.symbol.in_(symbols)))
            ).all()
        )

        # Deduplicate symbols and handle isin conflicts (same isin for different symbol)
        unique_records = {record.symbol: record for record in records}
        # Existing isin -> symbol mapping
        existing_isin_map = {}
        if any(r.isin for r in unique_records.values()):
            isins = [r.isin for r in unique_records.values() if r.isin]
            rows = await self.session.execute(select(Stock.isin, Stock.symbol).where(Stock.isin.in_(isins)))
            existing_isin_map = {isin: sym for isin, sym in rows.all() if isin}
        stock_rows = []
        for sym, record in unique_records.items():
            if record.isin and record.isin in existing_isin_map and existing_isin_map[record.isin] != sym:
                # Isin already owned by different symbol — skip stock insert, will map price to existing stock via isin
                continue
            stock_rows.append(
                {
                    "symbol": record.symbol,
                    "company_name": record.symbol,
                    "exchange": "NSE",
                    "series": record.series,
                    "isin": record.isin,
                    "is_active": True,
                }
            )
        if stock_rows:
            stock_insert = insert(Stock).values(stock_rows)
        stock_insert = stock_insert.on_conflict_do_update(
            constraint="uq_stock_exchange_symbol",
            set_={
                "series": stock_insert.excluded.series,
                "isin": stock_insert.excluded.isin,
                "is_active": True,
                "updated_at": datetime.now(UTC),
            },
            where=or_(
                Stock.series.is_distinct_from(stock_insert.excluded.series),
                Stock.isin.is_distinct_from(stock_insert.excluded.isin),
            ),
        )
        if stock_rows:
            await self.session.execute(stock_insert)
            await self.session.flush()

        stock_ids = dict(
            (
                await self.session.execute(
                    select(Stock.symbol, Stock.id).where(
                        Stock.exchange == "NSE", Stock.symbol.in_(symbols)
                    )
                )
            ).all()
        )
        # Alias via isin for symbols skipped due to isin conflict (e.g., ONIDA -> MIRCELECTR)
        missing_symbols = [s for s in symbols if s not in stock_ids]
        if missing_symbols:
            isin_for_missing = {unique_records[s].isin: s for s in missing_symbols if unique_records[s].isin}
            if isin_for_missing:
                rows = await self.session.execute(select(Stock.isin, Stock.id).where(Stock.isin.in_(list(isin_for_missing.keys()))))
                isin_to_id = {isin: sid for isin, sid in rows.all()}
                for isin, sym in isin_for_missing.items():
                    if isin in isin_to_id:
                        stock_ids[sym] = isin_to_id[isin]
        keys = {(stock_ids[r.symbol], r.trade_date) for r in records if r.symbol in stock_ids}
        existing_prices = set(
            (
                await self.session.execute(
                    select(DailyPrice.stock_id, DailyPrice.trade_date).where(
                        DailyPrice.stock_id.in_([key[0] for key in keys]),
                        DailyPrice.trade_date.in_([key[1] for key in keys]),
                    )
                )
            ).all()
        )

        price_rows: list[dict[str, Any]] = []
        for record in records:
            if record.symbol not in stock_ids:
                continue
            price_rows.append(
                {
                    "stock_id": stock_ids[record.symbol],
                    "trade_date": record.trade_date,
                    "open": record.open,
                    "high": record.high,
                    "low": record.low,
                    "close": record.close,
                    "previous_close": record.previous_close,
                    "last_price": record.last_price,
                    "volume": record.volume,
                    "turnover": record.turnover,
                    "trade_count": record.trade_count,
                    "delivery_quantity": record.delivery_quantity,
                    "delivery_percentage": record.delivery_percentage,
                }
            )
        # Batch to avoid PostgreSQL 32767 bind parameter limit (3150*14 > limit)
        BATCH = 400
        for i in range(0, len(price_rows), BATCH):
            batch = price_rows[i : i + BATCH]
            price_insert = insert(DailyPrice).values(batch)
            price_insert = price_insert.on_conflict_do_update(
                constraint="uq_daily_price_stock_date",
                set_={
                    "open": price_insert.excluded.open,
                    "high": price_insert.excluded.high,
                    "low": price_insert.excluded.low,
                    "close": price_insert.excluded.close,
                    "previous_close": price_insert.excluded.previous_close,
                    "last_price": price_insert.excluded.last_price,
                    "volume": price_insert.excluded.volume,
                    "turnover": price_insert.excluded.turnover,
                    "trade_count": price_insert.excluded.trade_count,
                    "delivery_quantity": price_insert.excluded.delivery_quantity,
                    "delivery_percentage": price_insert.excluded.delivery_percentage,
                    "updated_at": datetime.now(UTC),
                },
                where=or_(
                    DailyPrice.open.is_distinct_from(price_insert.excluded.open),
                    DailyPrice.high.is_distinct_from(price_insert.excluded.high),
                    DailyPrice.low.is_distinct_from(price_insert.excluded.low),
                    DailyPrice.close.is_distinct_from(price_insert.excluded.close),
                    DailyPrice.previous_close.is_distinct_from(price_insert.excluded.previous_close),
                    DailyPrice.last_price.is_distinct_from(price_insert.excluded.last_price),
                    DailyPrice.volume.is_distinct_from(price_insert.excluded.volume),
                    DailyPrice.turnover.is_distinct_from(price_insert.excluded.turnover),
                    DailyPrice.trade_count.is_distinct_from(price_insert.excluded.trade_count),
                    DailyPrice.delivery_quantity.is_distinct_from(price_insert.excluded.delivery_quantity),
                    DailyPrice.delivery_percentage.is_distinct_from(price_insert.excluded.delivery_percentage),
                ),
            )
            await self.session.execute(price_insert)

        updated = sum((stock_ids[r.symbol], r.trade_date) in existing_prices for r in records if r.symbol in stock_ids)
        inserted = len(price_rows) - updated
        # Account for skipped isin-aliased records as updated (they map to existing stock)
        inserted = max(0, inserted)
        _ = existing_symbols
        return inserted, updated
