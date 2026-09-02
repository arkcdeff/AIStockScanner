"""Market-data orchestration for uploads, downloads, backfill, and coverage.

This service reuses the existing :class:`NSEHistoricalService`,
:class:`NSEDownloader`, and :class:`NSEImportService` so that the web UI,
REST API, CLI, and EOD scheduler all share one code path for acquiring and
ingesting NSE EOD data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.models.daily_price import DailyPrice
from app.models.import_job import ImportJob
from app.models.indicator_result import IndicatorResult
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.repositories.market_data import MarketDataRepository
from app.services.nse_downloader import NSEDownloader, NSEDownloadError
from app.services.nse_historical import NSEHistoricalService
from app.services.nse_import import (
    MODE_LATEST_DOWNLOAD,
    NSEImportService,
)
from app.services.scanner_readiness import SCANNER_HISTORY_THRESHOLDS

# Session-count thresholds used for scanner-readiness reporting.
READINESS_THRESHOLDS = (20, 50, 100, 200)


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    archive_path: str
    checksum: str


class MarketDataService:
    """High-level operations over NSE market data used by UI/API/CLI/scheduler."""

    def __init__(self, session: AsyncSession, *, request_delay: float = 0.0) -> None:
        self.session = session
        self.request_delay = request_delay

    # ------------------------------------------------------------------ archives

    @staticmethod
    def _safe_name(file_name: str) -> str:
        base = Path(file_name or "bhavcopy.csv").name
        clean = "".join(ch for ch in base if ch.isalnum() or ch in "._-")
        return clean or "bhavcopy.csv"

    def archive_bytes(self, file_name: str, payload: bytes) -> ArchiveResult:
        """Archive raw upload bytes under data/raw/nse and return path + checksum."""
        now = datetime.now(UTC)
        target_dir = settings.raw_data_dir / str(now.year) / f"{now.month:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._safe_name(file_name)
        target_path = target_dir / safe_name
        if target_path.exists() and target_path.read_bytes() == payload:
            checksum = hashlib.sha256(payload).hexdigest()
        else:
            target_path.write_bytes(payload)
            checksum = hashlib.sha256(payload).hexdigest()
            target_path.with_suffix(target_path.suffix + ".sha256").write_text(checksum)
        return ArchiveResult(archive_path=str(target_path), checksum=checksum)

    # ------------------------------------------------------------------ imports

    async def import_upload(
        self,
        *,
        file_name: str,
        payload: bytes,
        mode: str,
    ) -> Any:
        """Archive + import an uploaded bhavcopy/delivery CSV or ZIP."""
        archive = self.archive_bytes(file_name, payload)
        return await NSEImportService(self.session).import_bytes(
            bhavcopy=payload,
            bhavcopy_name=file_name,
            mode=mode,
            archive_path=archive.archive_path,
            checksum=archive.checksum,
        )

    async def download_and_import_date(self, trade_date: date) -> Any:
        """Download + import a single trade date (mode DATE_DOWNLOAD)."""
        service = NSEHistoricalService(self.session, request_delay=self.request_delay)
        return await service.fetch_date(trade_date)

    async def download_and_import_latest(self) -> Any:
        """Download + import the latest available trading day (mode LATEST_DOWNLOAD)."""
        with NSEDownloader() as downloader:
            try:
                trade_date, raw_path, _meta = downloader.download_latest()
            except NSEDownloadError as exc:
                return {"status": "failed", "message": str(exc)}
        job = await NSEImportService(self.session).import_files(
            raw_path, mode=MODE_LATEST_DOWNLOAD, checksum=""
        )
        return {
            "status": job.status,
            "trade_date": job.trade_date,
            "rows_inserted": job.rows_inserted,
            "rows_updated": job.rows_updated,
            "rows_rejected": job.rows_rejected,
            "format": (job.details or {}).get("format"),
            "message": job.error_message,
        }

    async def backfill(self, start_date: date, end_date: date) -> Any:
        """Fetch and import all dates missing in the range (mode RANGE_BACKFILL)."""
        service = NSEHistoricalService(self.session, request_delay=self.request_delay)
        summary = await service.fetch_missing_dates(start_date, end_date)
        return summary.as_dict()

    async def missing_dates(self, start_date: date, end_date: date) -> list[date]:
        service = NSEHistoricalService(self.session, request_delay=self.request_delay)
        return await service.find_missing_dates(start_date, end_date)

    # ------------------------------------------------------------------ coverage

    async def coverage(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """Per-trading-day coverage rows: prices, delivery, indicators, scanners."""
        price_rows = (
            await self.session.execute(
                select(
                    DailyPrice.trade_date,
                    func.count(DailyPrice.id),
                    func.count(DailyPrice.delivery_percentage),
                    func.count(func.distinct(DailyPrice.stock_id)),
                )
                .join(Stock, DailyPrice.stock_id == Stock.id)
                .where(
                    DailyPrice.trade_date >= start_date,
                    DailyPrice.trade_date <= end_date,
                    Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                )
                .group_by(DailyPrice.trade_date)
            )
        ).all()
        price_map = {row[0]: (row[1], row[2], row[3]) for row in price_rows}
        indicator_rows = (
            await self.session.execute(
                select(IndicatorResult.trade_date, func.count(IndicatorResult.id))
                .join(Stock, IndicatorResult.stock_id == Stock.id)
                .where(
                    IndicatorResult.trade_date >= start_date,
                    IndicatorResult.trade_date <= end_date,
                    Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                )
                .group_by(IndicatorResult.trade_date)
            )
        ).all()
        indicator_map = {row[0]: row[1] for row in indicator_rows}
        scan_rows = (
            await self.session.execute(
                select(ScanResult.scan_date, func.count(ScanResult.id))
                .join(Stock, ScanResult.stock_id == Stock.id)
                .where(
                    ScanResult.scan_date >= start_date,
                    ScanResult.scan_date <= end_date,
                    Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                )
                .group_by(ScanResult.scan_date)
            )
        ).all()
        scan_map = {row[0]: row[1] for row in scan_rows}

        rows: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            price_count, delivery_count, stock_count = price_map.get(current, (0, 0, 0))
            indicator_count = indicator_map.get(current, 0)
            scan_count = scan_map.get(current, 0)
            if current.weekday() >= 5:
                status = "weekend"
            elif price_count == 0:
                status = "missing"
            elif delivery_count > 0:
                status = "complete"
            else:
                status = "partial"
            rows.append(
                {
                    "trade_date": current,
                    "weekday": current.strftime("%a"),
                    "status": status,
                    "price_count": price_count,
                    "stock_count": stock_count,
                    "delivery_count": delivery_count,
                    "indicator_count": indicator_count,
                    "scan_count": scan_count,
                }
            )
            current += timedelta(days=1)
        return rows

    # ------------------------------------------------------------------ readiness

    async def scanner_readiness(self) -> dict[str, Any]:
        """Stocks with enough history for scanners + delivery coverage at latest date."""
        latest = await self.session.scalar(
            select(func.max(DailyPrice.trade_date))
            .join(Stock, DailyPrice.stock_id == Stock.id)
            .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
        )
        session_counts = (
            await self.session.execute(
                select(
                    DailyPrice.stock_id, func.count(func.distinct(DailyPrice.trade_date))
                )
                .join(Stock, DailyPrice.stock_id == Stock.id)
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .group_by(DailyPrice.stock_id)
            )
        ).all()
        readiness: dict[str, int] = {str(t): 0 for t in READINESS_THRESHOLDS}
        eligible_sessions: dict[str, int] = {
            str(t): 0 for t in SCANNER_HISTORY_THRESHOLDS
        }
        max_sessions = 0
        for _stock_id, count in session_counts:
            max_sessions = max(max_sessions, int(count))
            for threshold in READINESS_THRESHOLDS:
                if count >= threshold:
                    readiness[str(threshold)] += 1
            for threshold in SCANNER_HISTORY_THRESHOLDS:
                if count >= threshold:
                    eligible_sessions[str(threshold)] += 1

        latest_prices = delivery_coverage = latest_indicators = 0
        if latest is not None:
            latest_prices = (
                await self.session.scalar(
                    select(func.count(DailyPrice.id))
                    .join(Stock, DailyPrice.stock_id == Stock.id)
                    .where(
                        DailyPrice.trade_date == latest,
                        Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                    )
                )
                or 0
            )
            delivery_coverage = (
                await self.session.scalar(
                    select(func.count(DailyPrice.id))
                    .join(Stock, DailyPrice.stock_id == Stock.id)
                    .where(
                        DailyPrice.trade_date == latest,
                        DailyPrice.delivery_percentage.is_not(None),
                        Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                    )
                )
                or 0
            )
            latest_indicators = (
                await self.session.scalar(
                    select(func.count(IndicatorResult.id))
                    .join(Stock, IndicatorResult.stock_id == Stock.id)
                    .where(
                        IndicatorResult.trade_date == latest,
                        Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                    )
                )
                or 0
            )

        total_product_stocks = (
            await self.session.scalar(
                select(func.count(Stock.id)).where(
                    Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)
                )
            )
            or 0
        )

        return {
            "latest_trade_date": latest,
            "readiness": readiness,
            "latest_price_count": latest_prices,
            "latest_delivery_count": delivery_coverage,
            "latest_indicator_count": latest_indicators,
            "stocks_with_history": len(session_counts),
            "total_product_stocks": total_product_stocks,
            "max_sessions": max_sessions,
            "eligible_sessions": eligible_sessions,
        }

    # ------------------------------------------------------------------ status

    async def data_status(self) -> dict[str, Any]:
        repo = MarketDataRepository(self.session)
        summary = await repo.product_summary()
        latest_import = await self.session.scalar(
            select(ImportJob)
            .where(ImportJob.status == "completed")
            .order_by(ImportJob.completed_at.desc())
            .limit(1)
        )
        today = datetime.now(UTC).date()
        window_start = today - timedelta(days=settings.eod_scheduler_catchup_max_days)
        missing = await self.missing_dates(window_start, today)
        return {
            **summary,
            "latest_import": (
                {
                    "id": latest_import.id,
                    "trade_date": latest_import.trade_date,
                    "mode": (latest_import.details or {}).get("mode"),
                    "format": (latest_import.details or {}).get("format"),
                    "rows_inserted": latest_import.rows_inserted,
                    "rows_updated": latest_import.rows_updated,
                    "completed_at": latest_import.completed_at,
                }
                if latest_import
                else None
            ),
            "missing_dates": [d.isoformat() for d in missing],
            "missing_count": len(missing),
        }
