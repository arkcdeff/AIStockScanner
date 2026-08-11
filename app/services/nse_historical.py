"""Historical NSE EOD data acquisition by date and date range.

This service reuses the existing :class:`NSEDownloader` and
:class:`NSEImportService` to populate missing historical EOD data from
official NSE sources. It is designed to be conservative with NSE requests,
skip weekends/holidays cleanly, and surface 403 blocks without bypassing
NSE anti-bot controls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.services.nse_downloader import NSEDownloader, NSEDownloadError
from app.services.nse_import import NSEImportService

# Maximum number of calendar days allowed per fetch_range request.
MAX_RANGE_DAYS = 366

# Default delay between NSE HTTP requests (seconds).
DEFAULT_REQUEST_DELAY = 1.0


@dataclass(frozen=True, slots=True)
class DateFetchResult:
    """Outcome of fetching a single trade date."""

    trade_date: date
    status: str
    message: str = ""
    raw_path: str | None = None
    rows_inserted: int = 0
    rows_updated: int = 0


@dataclass(slots=True)
class RangeFetchSummary:
    """Summary of a date-range fetch operation."""

    requested_dates: int = 0
    trading_dates_attempted: int = 0
    downloaded: int = 0
    already_present: int = 0
    imported: int = 0
    skipped: int = 0
    blocked: int = 0
    failed: int = 0
    results: list[DateFetchResult] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_dates": self.requested_dates,
            "trading_dates_attempted": self.trading_dates_attempted,
            "downloaded": self.downloaded,
            "already_present": self.already_present,
            "imported": self.imported,
            "skipped": self.skipped,
            "blocked": self.blocked,
            "failed": self.failed,
        }


class NSEHistoricalService:
    """Fetch historical NSE EOD data by date or date range."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        request_delay: float = DEFAULT_REQUEST_DELAY,
        max_retries: int = 2,
    ) -> None:
        self.session = session
        self.request_delay = request_delay
        self.max_retries = max_retries

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _is_weekend(trade_date: date) -> bool:
        return trade_date.weekday() >= 5

    async def _date_has_data(self, trade_date: date) -> bool:
        """Return True if DailyPrice rows already exist for the date."""
        count = await self.session.scalar(
            select(func.count(DailyPrice.id)).where(DailyPrice.trade_date == trade_date)
        )
        return bool(count and count > 0)

    async def _import_raw(self, raw_path: Path) -> tuple[int, int]:
        """Import a raw bhavcopy file and return (inserted, updated)."""
        job = await NSEImportService(self.session).import_files(raw_path)
        return job.rows_inserted or 0, job.rows_updated or 0

    async def _download_one(
        self, downloader: NSEDownloader, trade_date: date
    ) -> tuple[Path, dict[str, Any]]:
        """Download a single date with retry on transient errors only."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return downloader.download(trade_date)
            except NSEDownloadError as exc:
                # 403 / 404 are not transient; do not retry.
                if "403" in str(exc) or "404" in str(exc) or "holiday" in str(exc).lower():
                    raise
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(self.request_delay * (attempt + 1))
        raise NSEDownloadError(f"Download failed after retries: {last_error}")

    # ------------------------------------------------------------------ public API

    async def check_date_available(self, trade_date: date) -> bool:
        """Return True if EOD data already exists in the database for the date."""
        return await self._date_has_data(trade_date)

    async def fetch_date(self, trade_date: date) -> DateFetchResult:
        """Fetch and import EOD data for a single trade date.

        Returns a :class:`DateFetchResult` with one of the statuses:
        ``downloaded``, ``already_present``, ``imported``, ``skipped_weekend``,
        ``skipped_holiday``, ``unavailable``, ``blocked_403``, ``failed``.
        """
        if self._is_weekend(trade_date):
            return DateFetchResult(trade_date, "skipped_weekend", "Weekend")

        if await self._date_has_data(trade_date):
            return DateFetchResult(trade_date, "already_present", "Data already in database")

        try:
            with NSEDownloader() as downloader:
                raw_path, _meta = await self._download_one(downloader, trade_date)
        except NSEDownloadError as exc:
            message = str(exc)
            if "403" in message:
                return DateFetchResult(trade_date, "blocked_403", message)
            if "404" in message or "holiday" in message.lower():
                return DateFetchResult(trade_date, "skipped_holiday", message)
            return DateFetchResult(trade_date, "failed", message)

        inserted, updated = await self._import_raw(raw_path)
        if inserted == 0 and updated == 0:
            return DateFetchResult(
                trade_date, "unavailable", "No valid records imported from bhavcopy"
            )
        return DateFetchResult(
            trade_date,
            "imported",
            f"Imported {inserted} rows, updated {updated}",
            raw_path=str(raw_path),
            rows_inserted=inserted,
            rows_updated=updated,
        )

    async def fetch_range(
        self, start_date: date, end_date: date
    ) -> RangeFetchSummary:
        """Fetch and import EOD data for a bounded date range.

        Processes dates sequentially with a configurable delay between NSE
        requests. Skips weekends and dates already present in the database.
        """
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")
        if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(
                f"Date range exceeds maximum of {MAX_RANGE_DAYS} days"
            )

        summary = RangeFetchSummary()
        current = start_date
        while current <= end_date:
            summary.requested_dates += 1
            if self._is_weekend(current):
                summary.skipped += 1
                summary.results.append(
                    DateFetchResult(current, "skipped_weekend", "Weekend")
                )
                current += timedelta(days=1)
                continue

            summary.trading_dates_attempted += 1
            result = await self.fetch_date(current)
            summary.results.append(result)

            if result.status == "imported":
                summary.imported += 1
                summary.downloaded += 1
            elif result.status == "already_present":
                summary.already_present += 1
            elif result.status == "skipped_weekend":
                summary.skipped += 1
            elif result.status == "skipped_holiday":
                summary.skipped += 1
            elif result.status == "blocked_403":
                summary.blocked += 1
            elif result.status == "failed":
                summary.failed += 1
            elif result.status == "unavailable":
                summary.skipped += 1

            # Conservative delay between NSE requests.
            if current < end_date and self.request_delay > 0:
                await asyncio.sleep(self.request_delay)

            current += timedelta(days=1)

        return summary

    async def fetch_missing_dates(
        self, start_date: date, end_date: date
    ) -> RangeFetchSummary:
        """Fetch only dates missing from the database within the range."""
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")
        if (end_date - start_date).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(
                f"Date range exceeds maximum of {MAX_RANGE_DAYS} days"
            )

        summary = RangeFetchSummary()
        current = start_date
        while current <= end_date:
            summary.requested_dates += 1
            if self._is_weekend(current):
                summary.skipped += 1
                summary.results.append(
                    DateFetchResult(current, "skipped_weekend", "Weekend")
                )
                current += timedelta(days=1)
                continue

            summary.trading_dates_attempted += 1
            if await self._date_has_data(current):
                summary.already_present += 1
                summary.results.append(
                    DateFetchResult(current, "already_present", "Data already in database")
                )
            else:
                result = await self.fetch_date(current)
                summary.results.append(result)
                if result.status == "imported":
                    summary.imported += 1
                    summary.downloaded += 1
                elif result.status == "blocked_403":
                    summary.blocked += 1
                elif result.status == "failed":
                    summary.failed += 1
                else:
                    summary.skipped += 1

            if current < end_date and self.request_delay > 0:
                await asyncio.sleep(self.request_delay)

            current += timedelta(days=1)

        return summary

    async def find_missing_dates(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """Return trading dates in the range that have no DailyPrice data."""
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")

        present_dates = set(
            (
                await self.session.scalars(
                    select(DailyPrice.trade_date)
                    .where(DailyPrice.trade_date >= start_date)
                    .where(DailyPrice.trade_date <= end_date)
                    .distinct()
                )
            ).all()
        )

        missing: list[date] = []
        current = start_date
        while current <= end_date:
            if not self._is_weekend(current) and current not in present_dates:
                missing.append(current)
            current += timedelta(days=1)
        return missing