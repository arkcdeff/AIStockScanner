from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.config import settings
from app.services.nse_downloader import NSEDownloader
from app.services.nse_historical import NSEHistoricalService


def test_downloader_url_routing_modern():
    downloader = NSEDownloader()
    url = downloader._bhavcopy_url(date(2026, 8, 31))
    assert "nsearchives.nseindia.com" in url
    assert "BhavCopy_NSE_CM_0_0_0_20260831_F_0000.csv.zip" in url


def test_downloader_url_routing_legacy():
    downloader = NSEDownloader()
    url = downloader._bhavcopy_url(date(2023, 5, 15))
    assert "nsearchives.nseindia.com" in url
    assert "cm15MAY2023bhav.csv.zip" in url


def test_downloader_delivery_url():
    downloader = NSEDownloader()
    url = downloader._delivery_url(date(2026, 8, 31))
    assert "sec_bhavdata_full_31082026.csv" in url


def test_downloader_session_skip_for_nsearchives():
    downloader = NSEDownloader()
    downloader._ensure_session()
    assert downloader._session_ready is True


def test_downloader_download_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    downloader = NSEDownloader()

    def handler(request: httpx.Request) -> httpx.Response:
        if "sec_bhavdata_full" in str(request.url):
            return httpx.Response(200, text="SYMBOL,SERIES,DELIV_QTY,DELIV_PER\nINFY,EQ,100,50.0\n")
        return httpx.Response(404)

    downloader._client = httpx.Client(transport=httpx.MockTransport(handler))
    deliv_path = downloader.download_delivery(date(2026, 8, 31))
    assert deliv_path is not None
    assert deliv_path.exists()
    assert "INFY" in deliv_path.read_text()


@pytest.mark.asyncio
async def test_historical_service_passes_delivery_path(monkeypatch):
    fake_session = MagicMock()
    service = NSEHistoricalService(fake_session)

    called_with: dict = {}

    async def mock_import_files(self, bhavcopy_path, delivery_path=None, **kwargs):
        called_with["bhavcopy_path"] = bhavcopy_path
        called_with["delivery_path"] = delivery_path
        mock_job = MagicMock()
        mock_job.rows_inserted = 10
        mock_job.rows_updated = 2
        return mock_job

    monkeypatch.setattr("app.services.nse_import.NSEImportService.import_files", mock_import_files)
    b_path = Path("fake_bhav.zip")
    d_path = Path("fake_deliv.csv")
    ins, upd = await service._import_raw(b_path, delivery_path=d_path)
    assert ins == 10
    assert upd == 2
    assert called_with["delivery_path"] == d_path
