from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.core.config import settings


class NSEDownloadError(Exception):
    pass


class NSEDownloader:
    def __init__(self) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(settings.download_timeout, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            },
        )
        self._session_ready = False

    def _ensure_session(self) -> None:
        if self._session_ready:
            return
        if "nsearchives.nseindia.com" in settings.nse_base_url or "nsearchives.nseindia.com" in settings.nse_bhavcopy_url:
            self._session_ready = True
            return
        try:
            response = self._client.get(settings.nse_base_url)
            response.raise_for_status()
            self._session_ready = True
        except httpx.HTTPStatusError as exc:
            raise NSEDownloadError(f"Failed to initialize NSE session: {exc}") from exc
        except httpx.RequestError as exc:
            raise NSEDownloadError(f"NSE session request failed: {exc}") from exc

    def _bhavcopy_url(self, trade_date: date) -> str:
        month_name = trade_date.strftime("%b").upper()
        if trade_date < date(2024, 7, 8):
            return (
                f"https://nsearchives.nseindia.com/content/historical/EQUITIES/"
                f"{trade_date.year}/{month_name}/cm{trade_date.day:02d}{month_name}{trade_date.year}bhav.csv.zip"
            )
        if "{month_num" in settings.nse_bhavcopy_url:
            return settings.nse_bhavcopy_url.format(
                year=trade_date.year,
                month=month_name,
                month_num=trade_date.month,
                day=trade_date.day,
            )
        try:
            return settings.nse_bhavcopy_url.format(
                year=trade_date.year,
                month=month_name,
                day=trade_date.day,
            )
        except KeyError:
            return (
                f"https://nsearchives.nseindia.com/content/cm/"
                f"BhavCopy_NSE_CM_0_0_0_{trade_date.strftime('%Y%m%d')}_F_0000.csv.zip"
            )

    def _raw_path(self, trade_date: date) -> Path:
        target_dir = settings.raw_data_dir / str(trade_date.year) / f"{trade_date.month:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        if trade_date >= date(2024, 7, 8):
            file_name = f"BhavCopy_NSE_CM_0_0_0_{trade_date.strftime('%Y%m%d')}_F_0000.csv.zip"
        else:
            month = trade_date.strftime("%b").upper()
            file_name = f"cm{trade_date.day:02d}{month}{trade_date.year}bhav.csv.zip"
        return target_dir / file_name

    def _delivery_url(self, trade_date: date) -> str:
        month_name = trade_date.strftime("%b").upper()
        if hasattr(settings, "nse_delivery_url") and settings.nse_delivery_url:
            return settings.nse_delivery_url.format(
                year=trade_date.year,
                month=month_name,
                month_num=trade_date.month,
                day=trade_date.day,
            )
        return (
            f"https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{trade_date.strftime('%d%m%Y')}.csv"
        )

    def _delivery_raw_path(self, trade_date: date) -> Path:
        target_dir = settings.raw_data_dir / str(trade_date.year) / f"{trade_date.month:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"sec_bhavdata_full_{trade_date.strftime('%d%m%Y')}.csv"
        return target_dir / file_name

    def download_delivery(self, trade_date: date) -> Path | None:
        """Download companion security-wise delivery data if available."""
        if trade_date.weekday() >= 5:
            return None
        self._ensure_session()
        url = self._delivery_url(trade_date)
        target_path = self._delivery_raw_path(trade_date)
        try:
            response = self._client.get(url)
            if response.status_code != 200 or not response.content:
                return None
            target_path.write_bytes(response.content)
            checksum_path = target_path.with_suffix(target_path.suffix + ".sha256")
            checksum_path.write_text(self._checksum(response.content))
            logger.info("Downloaded companion delivery for {} to {}", trade_date, target_path)
            return target_path
        except Exception as exc:
            logger.debug("Companion delivery download skipped for {}: {}", trade_date, exc)
            return None

    def _checksum(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def download(self, trade_date: date) -> tuple[Path, dict[str, Any]]:
        if trade_date.weekday() >= 5:
            raise NSEDownloadError(f"{trade_date} is a weekend, market data not available")

        self._ensure_session()
        url = self._bhavcopy_url(trade_date)
        target_path = self._raw_path(trade_date)
        checksum_path = target_path.with_suffix(target_path.suffix + ".sha256")

        try:
            response = self._client.get(url)
            if response.status_code == 404:
                raise NSEDownloadError(
                    f"No bhavcopy available for {trade_date} (holiday or non-trading day)"
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise NSEDownloadError(
                    f"Access denied by NSE for {trade_date}. Check headers or rate limits."
                ) from exc
            raise NSEDownloadError(f"HTTP error downloading {trade_date}: {exc}") from exc
        except httpx.RequestError as exc:
            raise NSEDownloadError(f"Network error downloading {trade_date}: {exc}") from exc

        data = response.content
        if not data:
            raise NSEDownloadError(f"Empty file downloaded for {trade_date}")

        delivery_path = self.download_delivery(trade_date)

        if target_path.exists():
            existing_checksum = self._checksum(target_path.read_bytes())
            if existing_checksum == self._checksum(data):
                logger.info("Bhavcopy for {} already exists with matching checksum", trade_date)
                meta: dict[str, Any] = {
                    "path": str(target_path),
                    "size": target_path.stat().st_size,
                    "checksum": existing_checksum,
                    "downloaded": False,
                    "delivery_path": str(delivery_path) if delivery_path else None,
                }
                return target_path, meta
            target_path.unlink()

        target_path.write_bytes(data)
        checksum_path.write_text(self._checksum(data))

        meta = {
            "path": str(target_path),
            "size": len(data),
            "checksum": self._checksum(data),
            "downloaded": True,
            "delivery_path": str(delivery_path) if delivery_path else None,
        }
        logger.info("Downloaded bhavcopy for {} to {}", trade_date, target_path)
        return target_path, meta

    def find_latest_trade_date(self, max_lookback: int = 10) -> date:
        today = datetime.now(UTC).date()
        for lookback in range(max_lookback):
            candidate = today - timedelta(days=lookback)
            if candidate.weekday() < 5:
                try:
                    self._ensure_session()
                    url = self._bhavcopy_url(candidate)
                    response = self._client.head(url)
                    if response.status_code == 200:
                        return candidate
                except (httpx.RequestError, httpx.HTTPStatusError):
                    continue
        raise NSEDownloadError(f"Could not find latest trading day within {max_lookback} days")

    def download_latest(self) -> tuple[date, Path, dict[str, Any]]:
        trade_date = self.find_latest_trade_date()
        path, meta = self.download(trade_date)
        return trade_date, path, meta

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> NSEDownloader:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
