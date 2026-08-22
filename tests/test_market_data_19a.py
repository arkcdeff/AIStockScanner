"""Sprint 19.1A tests: NSE ingestion, format detection, backfill, coverage, EOD."""
# ruff: noqa: E501
import io
import re
import zipfile
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.core.config import settings
from app.data.nse_parser import (
    FORMAT_CM_UDIFF,
    FORMAT_DELIVERY,
    FORMAT_LEGACY,
    FORMAT_UDIFF,
    FORMAT_UNKNOWN,
    detect_format,
    parse_bhavcopy,
    parse_bhavcopy_detailed,
)
from app.db.session import get_session_factory
from app.services.nse_import import MODE_UPLOAD, NSEImportService
from tests.auth_helpers import analyst_client, viewer_client

# --------------------------------------------------------------------------- fixtures

OWNER_CSV = b"""SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN
RELIANCE,EQ,1500,1530,1490,1520,1519,1498,100000,152000000,27-JUL-2026,5000,INE002A01018
TCS,EQ,3000,3050,2990,3040,3038,2980,50000,152000000,27-JUL-2026,3000,INE467B01029
SGBNOV26VI,GB,6000,6100,5900,6050,6040,5980,100,600000,27-JUL-2026,10,INE002A01023
DIBOND,GB,100,110,95,105,104,100,50,5000,27-JUL-2026,2,INE000000002
"""

COMBINED_CSV = b"""SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,DELIV_QTY,DELIV_PER
RELIANCE,EQ,1500,1530,1490,1520,1519,1498,100000,152000000,27-JUL-2026,5000,INE002A01018,60000,60.00
TCS,EQ,3000,3050,2990,3040,3038,2980,50000,152000000,27-JUL-2026,3000,INE467B01029,25000,50.00
"""

UDIFF_CSV = b"""TCKRSYMB,SCTYSRS,TRADDT,OPNPRIC,HGHPRIC,LWPRIC,CLSPRIC,PRVSCLSGPRIC,TTLTRADGVOL,TtlTradgVal,TTLNBOFTRAD,ISIN
INFY,EQ,2026-07-30,1400,1425,1395,1420,1390,10000,14500000,1200,INE009A01021
"""

DELIVERY_CSV = b"""SYMBOL,SERIES,DELIV_QTY,DELIV_PER
RELIANCE,EQ,60000,60.00
"""


def _zip(csv: bytes, name: str = "bhavcopy.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, csv)
    return buffer.getvalue()


# --------------------------------------------------------------------------- format detection & parsing

def test_detect_format_owner_legacy() -> None:
    assert detect_format("20260727_NSE.csv", OWNER_CSV) == FORMAT_LEGACY


def test_detect_format_udiff_and_cm_udiff() -> None:
    assert detect_format("b.csv", UDIFF_CSV) == FORMAT_UDIFF
    cm = b"""TCKRSYMB,SCTYSRS,TRADDT,OPNPRIC,HGHPRIC,LWPRIC,CLSPRIC,PRVSCLSGPRIC,LASTPRIC,TTLTRADGVOL,TTLTRADGVAL,TTLNBOFTRAD,ISIN
RELIANCE,EQ,2024-07-08,1500,1530,1490,1520,1498,1519,100000,152000000,5000,INE002A01018
"""
    assert detect_format("cm.csv", cm) == FORMAT_CM_UDIFF


def test_detect_format_delivery_file() -> None:
    assert detect_format("delivery.csv", DELIVERY_CSV) == FORMAT_DELIVERY


def test_detect_format_unknown() -> None:
    assert detect_format("x.csv", b"foo,bar\n1,2\n") == FORMAT_UNKNOWN
    assert detect_format("x.zip", b"") == FORMAT_UNKNOWN


def test_parse_owner_format_skips_gb_series() -> None:
    records, errors = parse_bhavcopy(OWNER_CSV, "20260727_NSE.csv")
    assert not errors
    assert len(records) == 2
    assert [r.symbol for r in records] == ["RELIANCE", "TCS"]
    assert all(r.series == "EQ" for r in records)
    assert records[0].trade_date.isoformat() == "2026-07-27"
    assert records[0].isin == "INE002A01018"


def test_owner_format_delivery_stays_none() -> None:
    """Delivery data absent in the file must remain None, never fabricated as zero."""
    records, _ = parse_bhavcopy(OWNER_CSV, "20260727_NSE.csv")
    for record in records:
        assert record.delivery_quantity is None
        assert record.delivery_percentage is None


def test_parse_detailed_reports_skipped_series() -> None:
    result = parse_bhavcopy_detailed(OWNER_CSV, "20260727_NSE.csv")
    assert result.detected_format == FORMAT_LEGACY
    assert result.total_rows == 4
    assert result.skipped_series == 2
    assert len(result.records) == 2
    assert result.delivery_rows == 0
    assert result.delivery_missing == 2
    assert result.has_delivery_columns is False


def test_parse_combined_bhavcopy_delivery_same_file() -> None:
    records, errors = parse_bhavcopy(COMBINED_CSV)
    assert not errors
    assert len(records) == 2
    assert records[0].symbol == "RELIANCE"
    assert records[0].delivery_quantity == 60000
    assert records[0].delivery_percentage == Decimal("60.00")
    assert records[1].delivery_percentage == Decimal("50.00")


def test_parse_combined_detailed_reports_delivery() -> None:
    result = parse_bhavcopy_detailed(COMBINED_CSV)
    assert result.has_delivery_columns is True
    assert result.delivery_rows == 2
    assert result.delivery_missing == 0


def test_parse_zip_wrapped_owner_file() -> None:
    payload = _zip(OWNER_CSV)
    result = parse_bhavcopy_detailed(payload, "20260727_NSE.csv.zip")
    assert len(result.records) == 2
    assert result.detected_format == FORMAT_LEGACY


def test_parse_udiff_zip() -> None:
    payload = _zip(UDIFF_CSV, "bhavcopy.csv")
    result = parse_bhavcopy_detailed(payload, "bhavcopy.csv.zip")
    assert len(result.records) == 1
    assert result.records[0].symbol == "INFY"
    assert result.detected_format == FORMAT_UDIFF


# --------------------------------------------------------------------------- importer

@pytest.mark.asyncio
async def test_import_owner_format_records_audit_details() -> None:
    factory = get_session_factory()
    async with factory() as session:
        job = await NSEImportService(session).import_bytes(
            bhavcopy=OWNER_CSV,
            bhavcopy_name="20260727_NSE.csv",
            mode=MODE_UPLOAD,
        )
    assert job.status == "completed"
    assert job.trade_date.isoformat() == "2026-07-27"
    assert job.rows_inserted == 2
    assert job.rows_rejected == 0
    details = job.details
    assert details["mode"] == MODE_UPLOAD
    assert details["format"] == FORMAT_LEGACY
    assert details["rows_discovered"] == 4
    assert details["rows_skipped_series"] == 2
    assert details["delivery_missing"] == 2
    assert details["duration_ms"] >= 0
    assert len(details["checksum"]) == 64


@pytest.mark.asyncio
async def test_import_owner_format_idempotent_upsert() -> None:
    factory = get_session_factory()
    async with factory() as session:
        service = NSEImportService(session)
        job1 = await service.import_bytes(
            bhavcopy=OWNER_CSV, bhavcopy_name="20260727_NSE.csv"
        )
        job2 = await service.import_bytes(
            bhavcopy=OWNER_CSV, bhavcopy_name="20260727_NSE.csv"
        )
    assert job1.status == "completed"
    assert job1.rows_inserted == 2
    assert job2.status == "completed"
    assert job2.rows_inserted == 0
    assert job2.rows_updated == 2


@pytest.mark.asyncio
async def test_import_combined_file_applies_delivery() -> None:
    factory = get_session_factory()
    async with factory() as session:
        job = await NSEImportService(session).import_bytes(
            bhavcopy=COMBINED_CSV, bhavcopy_name="combined.csv"
        )
    assert job.status == "completed"
    assert job.rows_inserted == 2
    assert job.details["has_delivery_columns"] is True
    assert job.details["delivery_rows"] == 2
    assert job.details["delivery_missing"] == 0

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import select

        from app.models.daily_price import DailyPrice
        from app.models.stock import Stock

        rows = (
            await session.execute(
                select(Stock.symbol, DailyPrice.delivery_percentage)
                .join(Stock, Stock.id == DailyPrice.stock_id)
                .order_by(Stock.symbol)
            )
        ).all()
    by_symbol = {row[0]: row[1] for row in rows}
    assert by_symbol["RELIANCE"] == Decimal("60.00")


@pytest.mark.asyncio
async def test_import_rejects_no_equity_records() -> None:
    payload = b"""SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN
SGBNOV26VI,GB,6000,6100,5900,6050,6040,5980,100,600000,27-JUL-2026,10,INE002A01023
"""
    factory = get_session_factory()
    async with factory() as session:
        job = await NSEImportService(session).import_bytes(
            bhavcopy=payload, bhavcopy_name="gb_only.csv"
        )
    assert job.status == "failed"
    assert "No valid equity records" in job.error_message


# --------------------------------------------------------------------------- downloader (MockTransport, zero network)

def _mock_downloader(status_code: int = 200, content: bytes | None = None):
    from app.services.nse_downloader import NSEDownloader

    downloader = NSEDownloader()
    payload = _zip(OWNER_CSV) if content is None else content

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="<html>NSE</html>")
        if status_code != 200:
            return httpx.Response(status_code, text="error")
        return httpx.Response(200, content=payload)

    downloader._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=settings.nse_base_url,
    )
    return downloader


def test_downloader_success(monkeypatch, tmp_path) -> None:

    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    with _mock_downloader() as downloader:
        path, meta = downloader.download(date(2026, 7, 27))
    assert meta["downloaded"] is True
    assert meta["size"] > 0
    assert path.exists()


def test_downloader_404_holiday(monkeypatch, tmp_path) -> None:
    from app.services.nse_downloader import NSEDownloadError

    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    with _mock_downloader(status_code=404) as downloader:
        with pytest.raises(NSEDownloadError, match="holiday or non-trading"):
            downloader.download(date(2026, 7, 27))


def test_downloader_403_blocked(monkeypatch, tmp_path) -> None:
    from app.services.nse_downloader import NSEDownloadError

    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    with _mock_downloader(status_code=403) as downloader:
        with pytest.raises(NSEDownloadError, match="Access denied"):
            downloader.download(date(2026, 7, 27))


def test_downloader_network_error(monkeypatch, tmp_path) -> None:
    from app.services.nse_downloader import NSEDownloader, NSEDownloadError

    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    downloader = NSEDownloader()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text="<html>NSE</html>")
        raise httpx.ConnectError("boom", request=request)

    downloader._client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=settings.nse_base_url
    )
    with downloader:
        with pytest.raises(NSEDownloadError, match="Network error"):
            downloader.download(date(2026, 7, 27))


def test_downloader_empty_file(monkeypatch, tmp_path) -> None:
    from app.services.nse_downloader import NSEDownloadError

    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    with _mock_downloader(content=b"") as downloader:
        with pytest.raises(NSEDownloadError, match="Empty file"):
            downloader.download(date(2026, 7, 27))


def test_downloader_checksum_dedupe(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    with _mock_downloader() as downloader:
        _path, first = downloader.download(date(2026, 7, 27))
        _path2, second = downloader.download(date(2026, 7, 27))
    assert first["downloaded"] is True
    assert second["downloaded"] is False
    assert second["checksum"] == first["checksum"]


def test_downloader_weekend_rejected(monkeypatch, tmp_path) -> None:
    from app.services.nse_downloader import NSEDownloadError

    monkeypatch.setattr(settings, "raw_data_dir", tmp_path)
    with _mock_downloader() as downloader:
        with pytest.raises(NSEDownloadError, match="weekend"):
            downloader.download(date(2026, 8, 1))


# --------------------------------------------------------------------------- API endpoints

@pytest.mark.asyncio
async def test_api_owner_import_then_coverage() -> None:
    async with analyst_client() as client:
        resp = await client.post(
            "/api/v1/market-data/import-file",
            files={"file": ("20260727_NSE.csv", io.BytesIO(OWNER_CSV), "text/csv")},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "completed"
        assert body["trade_date"] == "2026-07-27"
        assert body["details"]["format"] == FORMAT_LEGACY

        resp = await client.get(
            "/api/v1/market-data/coverage",
            params={"start_date": "2026-07-27", "end_date": "2026-07-27"},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["status"] == "partial"  # prices present, delivery absent
        assert rows[0]["price_count"] == 2

        resp = await client.get("/api/v1/market-data/readiness")
        assert resp.status_code == 200
        readiness = resp.json()
        assert "readiness" in readiness
        assert readiness["latest_trade_date"] == "2026-07-27"
        assert readiness["latest_delivery_count"] == 0

        resp = await client.get("/api/v1/market-data/status")
        assert resp.status_code == 200
        status = resp.json()
        assert "missing_count" in status
        assert "2026-07-27" not in status["missing_dates"]
        assert status["last_trade_date"] == "2026-07-27"


@pytest.mark.asyncio
async def test_api_backfill_validates_range() -> None:
    async with analyst_client() as client:
        resp = await client.post(
            "/api/v1/market-data/backfill",
            json={"start_date": "2026-08-01", "end_date": "2026-07-01"},
        )
        assert resp.status_code == 422
        resp = await client.post(
            "/api/v1/market-data/backfill",
            json={"start_date": "2026-01-01", "end_date": "2027-12-31"},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_api_import_too_large() -> None:
    async with analyst_client() as client:
        huge = b"x" * (settings.max_upload_size + 1)
        resp = await client.post(
            "/api/v1/market-data/import-file",
            files={"file": ("huge.csv", io.BytesIO(huge), "text/csv")},
        )
        assert resp.status_code == 413


# --------------------------------------------------------------------------- browser page + CSRF/security

def _csrf_token(page_text: str) -> str:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', page_text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_market_data_page_renders_tools_and_csrf() -> None:
    async with analyst_client(browser=True) as client:
        page = await client.get("/market-data")
        assert page.status_code == 200
        assert 'name="csrf-token"' in page.text
        assert "Download Latest" in page.text
        assert "Scanner Readiness" in page.text
        assert "Import Bhavcopy" in page.text
        assert "Coverage" in page.text
        assert "Scanner Readiness" in page.text
        assert "nse_download_blocked" not in page.text
        assert 'name="_csrf_token"' in page.text


@pytest.mark.asyncio
async def test_import_post_requires_csrf() -> None:
    async with analyst_client(browser=True) as client:
        resp = await client.post(
            "/market-data/import",
            files={"bhavcopy": ("20260727_NSE.csv", io.BytesIO(OWNER_CSV), "text/csv")},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_import_post_requires_write_permission() -> None:
    async with viewer_client(browser=True) as client:
        page = await client.get("/market-data")
        token = _csrf_token(page.text)
        resp = await client.post(
            "/market-data/import",
            data={"_csrf_token": token},
            files={"bhavcopy": ("20260727_NSE.csv", io.BytesIO(OWNER_CSV), "text/csv")},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file() -> None:
    async with analyst_client(browser=True) as client:
        page = await client.get("/market-data")
        token = _csrf_token(page.text)
        huge = b"x" * (settings.max_upload_size + 1)
        resp = await client.post(
            "/market-data/import",
            data={"_csrf_token": token},
            files={"bhavcopy": ("huge.csv", io.BytesIO(huge), "text/csv")},
        )
        assert resp.status_code == 413


@pytest.mark.asyncio
async def test_upload_owner_format_success_via_page() -> None:
    async with analyst_client(browser=True) as client:
        page = await client.get("/market-data")
        token = _csrf_token(page.text)
        resp = await client.post(
            "/market-data/import",
            data={"_csrf_token": token},
            files={"bhavcopy": ("20260727_NSE.csv", io.BytesIO(OWNER_CSV), "text/csv")},
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/market-data")
        page2 = await client.get(resp.headers["location"])
        assert page2.status_code == 200
        assert "Import status" in page2.text
        assert "legacy" in page2.text


@pytest.mark.asyncio
async def test_coverage_partial_refresh() -> None:
    async with analyst_client(browser=True) as client:
        resp = await client.get("/market-data/coverage", params={"days": 7})
        assert resp.status_code == 200
        assert "No coverage data" in resp.text or "weekend" in resp.text or "missing" in resp.text


# --------------------------------------------------------------------------- EOD pipeline & scheduler

@pytest.mark.asyncio
async def test_pipeline_run_from_file_skips_scanners() -> None:
    from app.services.eod_pipeline import EODPipelineService

    run = await EODPipelineService().run_from_file(
        bhavcopy=OWNER_CSV, bhavcopy_name="20260727_NSE.csv", run_scanners=False
    )
    assert run.status == "completed"
    assert run.scanners_executed == 0


@pytest.mark.asyncio
async def test_scheduler_catchup_disabled_by_default() -> None:
    from app.scheduler.eod_scheduler import EODScheduler

    scheduler = EODScheduler()
    result = await scheduler.run_market_data_catchup()
    assert result["status"] == "disabled"


def test_scheduler_status_reports_retry_settings() -> None:
    from app.scheduler.eod_scheduler import EODScheduler

    status = EODScheduler().status()
    assert status["eod_retry_enabled"] is settings.eod_scheduler_retry_enabled
    assert status["eod_max_retries"] == settings.eod_scheduler_max_retries
    assert status["eod_run_scanners"] is settings.eod_scheduler_run_scanners
    assert status["eod_auto_catchup"] is settings.eod_scheduler_auto_catchup