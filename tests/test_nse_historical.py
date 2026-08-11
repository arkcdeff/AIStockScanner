"""Tests for the historical NSE fetch service."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.stock import Stock
from app.services.nse_historical import (
    MAX_RANGE_DAYS,
    NSEHistoricalService,
)


async def _seed_stock_and_price(trade_date: date, symbol: str = "TEST1") -> None:
    factory = get_session_factory()
    async with factory() as session:
        stock = Stock(
            symbol=symbol,
            company_name="Test",
            exchange="NSE",
            series="EQ",
            is_active=True,
        )
        session.add(stock)
        await session.flush()
        session.add(
            DailyPrice(
                stock_id=stock.id,
                trade_date=trade_date,
                open=100,
                high=110,
                low=95,
                close=105,
                previous_close=100,
                volume=1000,
            )
        )
        await session.commit()


class _FakeDownloader:
    """Stand-in for NSEDownloader that never hits the network."""

    def __init__(self, *, status: str = "ok") -> None:
        self.status = status
        self.calls: list[date] = []

    def __enter__(self) -> _FakeDownloader:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def download(self, trade_date: date):
        self.calls.append(trade_date)
        if self.status == "403":
            from app.services.nse_downloader import NSEDownloadError

            raise NSEDownloadError(
                f"HTTP 403: Access denied by NSE for {trade_date}. "
                "Check headers or rate limits."
            )
        if self.status == "404":
            from app.services.nse_downloader import NSEDownloadError

            raise NSEDownloadError(
                f"No bhavcopy available for {trade_date} (holiday or non-trading day)"
            )
        if self.status == "error":
            from app.services.nse_downloader import NSEDownloadError

            raise NSEDownloadError(f"Network error downloading {trade_date}")
        # Return a real-ish bhavcopy CSV that the import service can parse.
        import io
        import zipfile

        csv = (
            f"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,"
            f"TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
            f"TEST1,EQ,100,110,95,105,104,100,1000,10500000,"
            f"{trade_date.strftime('%d-%b-%Y').upper()},500,INE000000001\n"
        ).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bhavcopy.csv", csv)
        payload = buffer.getvalue()
        raw_dir = Path("data/raw/nse") / str(trade_date.year) / f"{trade_date.month:02d}"
        raw_dir.mkdir(parents=True, exist_ok=True)
        month = trade_date.strftime("%b").upper()
        raw_path = raw_dir / f"cm{trade_date.day:02d}{month}{trade_date.year}bhav.csv.zip"
        raw_path.write_bytes(payload)
        return raw_path, {"downloaded": True, "size": len(payload), "checksum": "fake"}


@pytest.mark.asyncio
async def test_fetch_date_weekend_skipped() -> None:
    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        saturday = date(2026, 8, 1)
        result = await service.fetch_date(saturday)
    assert result.status == "skipped_weekend"


@pytest.mark.asyncio
async def test_fetch_date_already_present() -> None:
    trade_date = date(2026, 7, 15)
    await _seed_stock_and_price(trade_date)
    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        result = await service.fetch_date(trade_date)
    assert result.status == "already_present"


@pytest.mark.asyncio
async def test_fetch_date_imported(monkeypatch) -> None:
    trade_date = date(2026, 7, 15)
    fake = _FakeDownloader(status="ok")

    from app.services import nse_historical

    monkeypatch.setattr(nse_historical, "NSEDownloader", lambda: fake)

    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        result = await service.fetch_date(trade_date)
    assert result.status == "imported"
    assert result.rows_inserted == 1
    assert result.raw_path is not None


@pytest.mark.asyncio
async def test_fetch_date_403_blocked(monkeypatch) -> None:
    trade_date = date(2026, 7, 15)
    fake = _FakeDownloader(status="403")

    from app.services import nse_historical

    monkeypatch.setattr(nse_historical, "NSEDownloader", lambda: fake)

    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        result = await service.fetch_date(trade_date)
    assert result.status == "blocked_403"


@pytest.mark.asyncio
async def test_fetch_date_404_holiday(monkeypatch) -> None:
    trade_date = date(2026, 7, 15)
    fake = _FakeDownloader(status="404")

    from app.services import nse_historical

    monkeypatch.setattr(nse_historical, "NSEDownloader", lambda: fake)

    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        result = await service.fetch_date(trade_date)
    assert result.status == "skipped_holiday"


@pytest.mark.asyncio
async def test_fetch_date_network_error(monkeypatch) -> None:
    trade_date = date(2026, 7, 15)
    fake = _FakeDownloader(status="error")

    from app.services import nse_historical

    monkeypatch.setattr(nse_historical, "NSEDownloader", lambda: fake)

    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0, max_retries=0)
        result = await service.fetch_date(trade_date)
    assert result.status == "failed"


@pytest.mark.asyncio
async def test_fetch_range_weekend_and_import(monkeypatch) -> None:
    start = date(2026, 7, 13)  # Monday
    end = date(2026, 7, 19)  # Sunday
    fake = _FakeDownloader(status="ok")

    from app.services import nse_historical

    monkeypatch.setattr(nse_historical, "NSEDownloader", lambda: fake)

    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        summary = await service.fetch_range(start, end)
    assert summary.requested_dates == 7
    assert summary.imported >= 1
    assert summary.skipped >= 2  # Saturday + Sunday


@pytest.mark.asyncio
async def test_fetch_range_validates_bounds() -> None:
    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        with pytest.raises(ValueError):
            await service.fetch_range(date(2026, 7, 15), date(2026, 7, 1))
        with pytest.raises(ValueError):
            await service.fetch_range(
                date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=MAX_RANGE_DAYS)
            )


@pytest.mark.asyncio
async def test_fetch_range_already_present_skipped() -> None:
    trade_date = date(2026, 7, 15)
    await _seed_stock_and_price(trade_date)
    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        summary = await service.fetch_range(trade_date, trade_date)
    assert summary.already_present == 1


@pytest.mark.asyncio
async def test_fetch_missing_dates_only() -> None:
    existing = date(2026, 7, 15)
    await _seed_stock_and_price(existing)
    fake = _FakeDownloader(status="ok")

    from app.services import nse_historical

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(nse_historical, "NSEDownloader", lambda: fake)

    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        summary = await service.fetch_missing_dates(existing, date(2026, 7, 16))
    assert summary.already_present == 1
    assert summary.imported == 1


@pytest.mark.asyncio
async def test_find_missing_dates() -> None:
    existing = date(2026, 7, 15)
    await _seed_stock_and_price(existing)
    factory = get_session_factory()
    async with factory() as session:
        service = NSEHistoricalService(session, request_delay=0)
        missing = await service.find_missing_dates(existing, date(2026, 7, 17))
    # 15th exists; 16th (Thu) and 17th (Fri) are missing; skip weekend none here.
    assert date(2026, 7, 15) not in missing
    assert date(2026, 7, 16) in missing
    assert date(2026, 7, 17) in missing