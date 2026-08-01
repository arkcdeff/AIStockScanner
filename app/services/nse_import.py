from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.nse_parser import PriceRecord, load_bytes, parse_bhavcopy, parse_delivery
from app.models.daily_price import DailyPrice
from app.models.import_job import ImportJob
from app.models.stock import Stock
from app.services.indicator_engine import IndicatorEngine


class NSEImportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def import_files(
        self,
        bhavcopy_path: str | Path,
        delivery_path: str | Path | None = None,
    ) -> ImportJob:
        bhavcopy, file_name = load_bytes(bhavcopy_path)
        delivery_payload = load_bytes(delivery_path)[0] if delivery_path else None
        return await self.import_bytes(
            bhavcopy=bhavcopy,
            bhavcopy_name=file_name,
            delivery=delivery_payload,
            delivery_name=Path(delivery_path).name if delivery_path else None,
        )

    async def import_bytes(
        self,
        *,
        bhavcopy: bytes,
        bhavcopy_name: str,
        delivery: bytes | None = None,
        delivery_name: str | None = None,
    ) -> ImportJob:
        job = ImportJob(
            file_name=bhavcopy_name,
            status="running",
            started_at=datetime.now(UTC),
            details={"delivery_file": delivery_name},
        )
        self.session.add(job)
        await self.session.flush()

        try:
            delivery_map = parse_delivery(delivery, delivery_name or "delivery.csv") if delivery else {}
            records, errors = parse_bhavcopy(bhavcopy, bhavcopy_name, delivery_map)
            if not records:
                raise ValueError("No valid equity records found in the bhavcopy")

            job.trade_date = records[0].trade_date
            job.rows_read = len(records) + len(errors)
            job.rows_rejected = len(errors)
            inserted, updated = await self._upsert(records)
            job.rows_inserted = inserted
            job.rows_updated = updated
            job.status = "completed"
            job.details = {
                "delivery_file": delivery_name,
                "parse_errors": errors[:100],
                "valid_rows": len(records),
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
                details={"delivery_file": delivery_name},
                started_at=job.started_at,
                completed_at=datetime.now(UTC),
            )
            self.session.add(failed_job)
            await self.session.commit()
            await self.session.refresh(failed_job)
            return failed_job

    async def _upsert(self, records: list[PriceRecord]) -> tuple[int, int]:
        symbols = sorted({record.symbol for record in records})
        existing_symbols = set(
            (await self.session.scalars(select(Stock.symbol).where(Stock.symbol.in_(symbols)))).all()
        )

        stock_rows = [
            {
                "symbol": record.symbol,
                "company_name": record.symbol,
                "exchange": "NSE",
                "series": record.series,
                "isin": record.isin,
                "is_active": True,
            }
            for record in {record.symbol: record for record in records}.values()
        ]
        stock_insert = insert(Stock).values(stock_rows)
        stock_insert = stock_insert.on_conflict_do_update(
            constraint="uq_stock_exchange_symbol",
            set_={
                "series": stock_insert.excluded.series,
                "isin": stock_insert.excluded.isin,
                "is_active": True,
                "updated_at": datetime.now(UTC),
            },
        )
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
        keys = {(stock_ids[r.symbol], r.trade_date) for r in records}
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
        price_insert = insert(DailyPrice).values(price_rows)
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
        )
        await self.session.execute(price_insert)

        updated = sum((stock_ids[r.symbol], r.trade_date) in existing_prices for r in records)
        inserted = len(records) - updated
        _ = existing_symbols
        return inserted, updated
