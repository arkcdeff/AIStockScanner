"""NSE Historical Extension Service for Stage 2 Phase G.7.

Extends real NSE historical equity price coverage to 200 consecutive
trading sessions ending 2026-08-31 using official NSE bhavcopy and delivery archives.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.nse_parser import (
    PriceRecord,
    load_bytes,
    parse_bhavcopy_detailed,
    parse_delivery,
)
from app.models.daily_price import DailyPrice
from app.models.stock import Stock

logger = logging.getLogger(__name__)


@dataclass
class ExtensionSessionResult:
    trade_date: date
    bhavcopy_file: str
    delivery_file: str | None
    rows_in_file: int
    matched: int = 0
    unmatched: int = 0
    rejected: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    delivery_populated: int = 0


@dataclass
class ExtensionBatchReport:
    sessions_processed: int = 0
    total_source_rows: int = 0
    total_matched: int = 0
    total_unmatched: int = 0
    total_rejected: int = 0
    total_inserted: int = 0
    total_updated: int = 0
    total_unchanged: int = 0
    total_delivery_populated: int = 0
    session_results: list[ExtensionSessionResult] = field(default_factory=list)


class NseHistoricalExtensionService:
    """Service to ingest historical NSE bhavcopies for 200-session expansion."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._stocks_by_isin: dict[str, Stock] = {}
        self._stocks_by_symbol: dict[str, Stock] = {}
        self._cache_loaded = False

    async def _ensure_stock_cache(self) -> None:
        if self._cache_loaded:
            return
        result = await self.session.execute(
            select(Stock).where(Stock.exchange == "NSE")
        )
        stocks = result.scalars().all()
        for s in stocks:
            if s.isin:
                self._stocks_by_isin[s.isin.strip().upper()] = s
            if s.symbol:
                self._stocks_by_symbol[s.symbol.strip().upper()] = s
        self._cache_loaded = True
        logger.info(
            "Stock cache loaded: %d by ISIN, %d by symbol",
            len(self._stocks_by_isin),
            len(self._stocks_by_symbol),
        )

    def _validate_record(self, record: PriceRecord) -> str | None:
        """Validate OHLCV constraints per Step 10. Returns error string if invalid."""
        if record.open is None or record.high is None or record.low is None or record.close is None:
            return "Missing price value"
        if record.volume is None or record.volume < 0:
            return f"Invalid volume: {record.volume}"
        if record.high < record.low:
            return f"High ({record.high}) < Low ({record.low})"
        if record.high < record.open or record.high < record.close:
            return f"High ({record.high}) < max(open, close)"
        if record.low > record.open or record.low > record.close:
            return f"Low ({record.low}) > min(open, close)"
        if record.trade_date > date(2026, 8, 31):
            return f"Future trade date: {record.trade_date}"
        if record.trade_date.weekday() >= 5:
            return f"Weekend trade date: {record.trade_date} ({record.trade_date.strftime('%A')})"
        return None

    async def import_session_data(
        self,
        bhavcopy_payload: bytes,
        bhavcopy_name: str,
        delivery_payload: bytes | None = None,
        delivery_name: str | None = None,
    ) -> ExtensionSessionResult:
        """Import a single historical trading session without creating new Stock rows."""
        await self._ensure_stock_cache()

        delivery_map = (
            parse_delivery(delivery_payload, delivery_name or "delivery.csv")
            if delivery_payload
            else {}
        )
        parsed = parse_bhavcopy_detailed(
            bhavcopy_payload,
            file_name=bhavcopy_name,
            delivery=delivery_map,
            allowed_series={"EQ", "BE", "SM", "ST"},
        )

        trade_date = parsed.records[0].trade_date if parsed.records else date.min
        res = ExtensionSessionResult(
            trade_date=trade_date,
            bhavcopy_file=bhavcopy_name,
            delivery_file=delivery_name,
            rows_in_file=parsed.total_rows,
        )

        valid_records: list[tuple[Stock, PriceRecord]] = []
        for r in parsed.records:
            # 1. Entity resolution: ISIN primary, symbol fallback
            matched_stock: Stock | None = None
            if r.isin:
                matched_stock = self._stocks_by_isin.get(r.isin.strip().upper())
            if matched_stock is None and r.symbol:
                matched_stock = self._stocks_by_symbol.get(r.symbol.strip().upper())

            if matched_stock is None:
                res.unmatched += 1
                continue

            # 2. Validation
            val_err = self._validate_record(r)
            if val_err:
                res.rejected += 1
                continue

            res.matched += 1
            valid_records.append((matched_stock, r))

        if not valid_records:
            return res

        # 3. Check existing prices for idempotency & state detection
        stock_ids = [s.id for s, _ in valid_records]
        existing_rows = (
            await self.session.execute(
                select(
                    DailyPrice.stock_id,
                    DailyPrice.open,
                    DailyPrice.high,
                    DailyPrice.low,
                    DailyPrice.close,
                    DailyPrice.volume,
                    DailyPrice.delivery_quantity,
                    DailyPrice.delivery_percentage,
                ).where(
                    DailyPrice.stock_id.in_(stock_ids),
                    DailyPrice.trade_date == trade_date,
                )
            )
        ).all()
        existing_map = {row[0]: row for row in existing_rows}

        # 4. Prepare batch rows
        price_rows: list[dict[str, Any]] = []
        for stock, r in valid_records:
            if r.delivery_quantity is not None:
                res.delivery_populated += 1

            existing = existing_map.get(stock.id)
            if existing is None:
                res.inserted += 1
            else:
                # Compare fields to determine updated vs unchanged
                same = (
                    existing[1] == r.open
                    and existing[2] == r.high
                    and existing[3] == r.low
                    and existing[4] == r.close
                    and existing[5] == r.volume
                    and (r.delivery_quantity is None or existing[6] == r.delivery_quantity)
                    and (r.delivery_percentage is None or existing[7] == r.delivery_percentage)
                )
                if same:
                    res.unchanged += 1
                else:
                    res.updated += 1

            price_rows.append(
                {
                    "stock_id": stock.id,
                    "trade_date": trade_date,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "previous_close": r.previous_close,
                    "last_price": r.last_price,
                    "volume": r.volume,
                    "turnover": r.turnover,
                    "trade_count": r.trade_count,
                    "delivery_quantity": r.delivery_quantity,
                    "delivery_percentage": r.delivery_percentage,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            )

        # 5. Batch upsert
        BATCH_SIZE = 400
        for i in range(0, len(price_rows), BATCH_SIZE):
            batch = price_rows[i : i + BATCH_SIZE]
            stmt = insert(DailyPrice).values(batch)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_daily_price_stock_date",
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "previous_close": stmt.excluded.previous_close,
                    "last_price": stmt.excluded.last_price,
                    "volume": stmt.excluded.volume,
                    "turnover": stmt.excluded.turnover,
                    "trade_count": stmt.excluded.trade_count,
                    # Preserve existing delivery if excluded is None
                    "delivery_quantity": func.coalesce(
                        stmt.excluded.delivery_quantity, DailyPrice.delivery_quantity
                    ),
                    "delivery_percentage": func.coalesce(
                        stmt.excluded.delivery_percentage, DailyPrice.delivery_percentage
                    ),
                    "updated_at": datetime.now(UTC),
                },
                where=or_(
                    DailyPrice.open.is_distinct_from(stmt.excluded.open),
                    DailyPrice.high.is_distinct_from(stmt.excluded.high),
                    DailyPrice.low.is_distinct_from(stmt.excluded.low),
                    DailyPrice.close.is_distinct_from(stmt.excluded.close),
                    DailyPrice.volume.is_distinct_from(stmt.excluded.volume),
                    DailyPrice.delivery_quantity.is_distinct_from(
                        stmt.excluded.delivery_quantity
                    ),
                ),
            )
            await self.session.execute(stmt)

        await self.session.commit()
        return res

    async def import_from_manifest(
        self,
        manifest_path: str | Path,
        raw_dir: str | Path,
    ) -> ExtensionBatchReport:
        """Process all sessions in the manifest and report aggregate progress."""
        manifest: dict[str, Any] = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

        raw_path = Path(raw_dir)
        report = ExtensionBatchReport()

        sorted_dates = sorted(manifest.keys())
        for d_str in sorted_dates:
            entry = manifest[d_str]
            bhav_info = entry["bhavcopy"]
            deliv_info = entry.get("delivery")

            bhav_file = raw_path / bhav_info["filename"]
            deliv_file = raw_path / deliv_info["filename"] if deliv_info else None

            if not bhav_file.exists():
                logger.warning("Bhavcopy file missing: %s", bhav_file)
                continue

            bhav_bytes, b_name = load_bytes(bhav_file)
            deliv_bytes = None
            d_name = None
            if deliv_file and deliv_file.exists():
                deliv_bytes, d_name = load_bytes(deliv_file)

            res = await self.import_session_data(
                bhavcopy_payload=bhav_bytes,
                bhavcopy_name=b_name,
                delivery_payload=deliv_bytes,
                delivery_name=d_name,
            )

            report.sessions_processed += 1
            report.total_source_rows += res.rows_in_file
            report.total_matched += res.matched
            report.total_unmatched += res.unmatched
            report.total_rejected += res.rejected
            report.total_inserted += res.inserted
            report.total_updated += res.updated
            report.total_unchanged += res.unchanged
            report.total_delivery_populated += res.delivery_populated
            report.session_results.append(res)

        return report
