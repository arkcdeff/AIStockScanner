from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.data.nse_parser import NSEParseError, parse_bhavcopy
from app.db.session import get_session_factory
from app.models.import_job import ImportJob
from app.repositories.market_data import MarketDataRepository
from app.services.nse_import import NSEImportService
from app.main import app


def test_parse_invalid_rows() -> None:
    payload = b"""SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN
RELIANCE,EQ,1500,1530,1490,1520,1519,1498,100000,152000000,30-JUL-2026,5000,INE002A01018
BAD,EQ,-10,5,8,9,9,9,100,900,30-JUL-2026,4,INE000000001
EMPTY,EQ,,,,,,,,,,30-JUL-2026,,
"""
    records, errors = parse_bhavcopy(payload)
    assert len(records) == 1
    assert len(errors) == 2
    assert "Row 3" in errors[0]
    assert "Row 4" in errors[1]


def test_parse_empty_file() -> None:
    with pytest.raises(NSEParseError, match="CSV has no header"):
        parse_bhavcopy(b"")


def test_parse_unsupported_format() -> None:
    import zipfile

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("readme.txt", "not a csv")
    with pytest.raises(NSEParseError, match="ZIP file does not contain a CSV file"):
        parse_bhavcopy(buffer.getvalue(), "data.zip")


@pytest.mark.asyncio
async def test_import_duplicate_upsert() -> None:
    factory = get_session_factory()
    async with factory() as session:
        service = NSEImportService(session)
        trade_date = datetime.now(UTC).strftime("%d-%b-%Y").upper()
        bhavcopy = f"""SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN
RELIANCE,EQ,1500,1530,1490,1520,1519,1498,100000,152000000,{trade_date},5000,INE002A01018
""".encode()
        job1 = await service.import_bytes(bhavcopy=bhavcopy, bhavcopy_name="first.csv")
        assert job1.status == "completed"
        assert job1.rows_inserted == 1
        assert job1.rows_updated == 0

        job2 = await service.import_bytes(bhavcopy=bhavcopy, bhavcopy_name="second.csv")
        assert job2.status == "completed"
        assert job2.rows_inserted == 0
        assert job2.rows_updated == 1


def test_api_download_returns_422_for_weekend() -> None:
    client = TestClient(app)
    saturday = date(2026, 8, 1)
    response = client.post("/api/v1/market-data/download", json={"trade_date": str(saturday)})
    assert response.status_code == 422


def test_api_import_file_empty() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/market-data/import-file",
        files={"file": ("test.csv", BytesIO(b""), "text/csv")},
    )
    assert response.status_code == 400


def test_api_import_file_too_large() -> None:
    client = TestClient(app)
    huge = b"x" * (settings.max_upload_size + 1)
    response = client.post(
        "/api/v1/market-data/import-file",
        files={"file": ("huge.csv", BytesIO(huge), "text/csv")},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_api_imports_and_summary() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/market-data/imports")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

        response = await client.get("/api/v1/market-data/summary")
        assert response.status_code == 200
        body = response.json()
        assert "stocks" in body
        assert "daily_prices" in body
