from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.market_intelligence.index_catalog import (
    CORE_INDEX_CATALOG,
    SECTOR_INDEX_CATALOG,
    seed_market_index_catalog,
)
from app.market_intelligence.providers.base import MarketProvider, ProviderResult
from app.market_intelligence.providers.global_index_provider import (
    GLOBAL_PROVIDER_SYMBOLS,
    GlobalIndexProvider,
)
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.index_historical import IndexHistoricalService

NOW = datetime(2025, 1, 8, 12, 0, tzinfo=UTC)
CSV = """Date,Open,High,Low,Close,Volume
2025-01-06,100,103,99,102,1000
2025-01-07,102,105,101,104,1200
"""


def _provider(handler) -> tuple[GlobalIndexProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GlobalIndexProvider(client, now=lambda: NOW), client


@pytest.mark.asyncio
async def test_provider_supports_exact_required_codes_and_is_separate_from_nse():
    provider, client = _provider(lambda request: httpx.Response(200, text=CSV))
    try:
        assert set(provider.supports_indices) == set(GLOBAL_PROVIDER_SYMBOLS)
        assert "NIFTY_50" not in provider.supports_indices
        assert "SENSEX" not in provider.supports_indices
    finally:
        await client.aclose()


def test_provider_symbols_match_stooq_index_identifiers():
    assert GLOBAL_PROVIDER_SYMBOLS == {
        "SP500": "^spx",
        "NASDAQ_100": "^ndx",
        "DOW_JONES": "^dji",
        "NIKKEI_225": "^nkx",
        "HANG_SENG": "^hsi",
        "SHANGHAI_COMPOSITE": "^shc",
        "KOSPI": "^kospi",
    }


@pytest.mark.asyncio
async def test_fetch_range_normalizes_decimal_ohlcv_and_provenance():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text=CSV)

    provider, client = _provider(handler)
    try:
        result = await provider.fetch_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
    finally:
        await client.aclose()

    assert result.status == "success"
    assert result.freshness == "recent"
    assert result.observed_at == "2025-01-07"
    assert result.fetched_at == NOW.isoformat()
    assert len(result.data) == 2
    assert result.data[0]["open"] == Decimal("100")
    assert result.data[1]["close"] == Decimal("104")
    assert result.data[1]["volume"] == 1200
    assert result.data[0]["source_reference"].startswith("https://stooq.com/")
    assert captured[0].url.params["s"] == "^spx"
    assert captured[0].url.params["d1"] == "20250106"
    assert captured[0].url.params["d2"] == "20250107"


@pytest.mark.asyncio
async def test_single_date_fetch_returns_one_normalized_row():
    provider, client = _provider(lambda request: httpx.Response(200, text=CSV))
    try:
        result = await provider.fetch(index_code="SP500", trade_date=date(2025, 1, 7))
    finally:
        await client.aclose()
    assert result.status == "success"
    assert isinstance(result.data, dict)
    assert result.data["trade_date"] == date(2025, 1, 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 429])
async def test_provider_truthfully_reports_blocked(status_code: int):
    provider, client = _provider(lambda request: httpx.Response(status_code))
    try:
        result = await provider.fetch_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
    finally:
        await client.aclose()
    assert result.status == "blocked"
    assert result.freshness == "missing"
    assert result.error_code == f"HTTP_{status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 500, 503])
async def test_provider_truthfully_reports_upstream_unavailable(status_code: int):
    provider, client = _provider(lambda request: httpx.Response(status_code))
    try:
        result = await provider.fetch_range("KOSPI", date(2025, 1, 6), date(2025, 1, 7))
    finally:
        await client.aclose()
    assert result.status == "unavailable"
    assert result.freshness == "missing"


@pytest.mark.asyncio
async def test_provider_reports_malformed_csv_as_failed():
    provider, client = _provider(
        lambda request: httpx.Response(200, text="Date,Close\n2025-01-07,5")
    )
    try:
        result = await provider.fetch_range("SP500", date(2025, 1, 7), date(2025, 1, 7))
    finally:
        await client.aclose()
    assert result.status == "failed"
    assert result.error_code == "parse_error"


@pytest.mark.asyncio
async def test_provider_reports_empty_weekend_or_holiday_as_unavailable():
    provider, client = _provider(
        lambda request: httpx.Response(200, text="Date,Open,High,Low,Close\n")
    )
    try:
        result = await provider.fetch(index_code="SP500", trade_date=date(2025, 1, 5))
    finally:
        await client.aclose()
    assert result.status == "unavailable"
    assert result.error_code == "no_data"


@pytest.mark.asyncio
async def test_provider_rejects_unsupported_code_without_http_call():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=CSV)

    provider, client = _provider(handler)
    try:
        result = await provider.fetch_range("SENSEX", date(2025, 1, 6), date(2025, 1, 7))
    finally:
        await client.aclose()
    assert result.status == "unavailable"
    assert result.error_code == "unsupported_index"
    assert calls == 0


@pytest.mark.asyncio
async def test_provider_requires_aware_injected_clock():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=CSV))
    )
    provider = GlobalIndexProvider(client, now=lambda: datetime(2025, 1, 8, 12, 0))
    try:
        with pytest.raises(ValueError, match="aware"):
            await provider.fetch_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
    finally:
        await client.aclose()


class FakeRangeProvider(MarketProvider):
    name = "fake_primary"
    supports_indices = ["SP500"]

    def __init__(self, result: ProviderResult) -> None:
        self.result = result
        self.range_calls = 0
        self.fetch_calls = 0

    async def fetch(self, **kwargs: Any) -> ProviderResult:
        self.fetch_calls += 1
        return self.result

    async def fetch_range(
        self, index_code: str, start_date: date, end_date: date
    ) -> ProviderResult:
        self.range_calls += 1
        return self.result


def _range_result(provider_name: str = "fake_primary") -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        status="success",
        observed_at="2025-01-07T16:00:00-05:00",
        fetched_at=NOW.isoformat(),
        market_date="2025-01-07",
        freshness="recent",
        data=[
            {
                "trade_date": date(2025, 1, 6),
                "open": Decimal("100"),
                "high": Decimal("103"),
                "low": Decimal("99"),
                "close": Decimal("102"),
                "volume": 1000,
                "source_reference": "mock://primary/sp500",
            },
            {
                "trade_date": date(2025, 1, 7),
                "open": Decimal("102"),
                "high": Decimal("105"),
                "low": Decimal("101"),
                "close": Decimal("104"),
                "volume": 1200,
                "source_reference": "mock://primary/sp500",
            },
        ],
    )


@pytest.mark.asyncio
async def test_catalog_seeder_is_idempotent_and_preserves_exact_required_catalog():
    factory = get_session_factory()
    async with factory() as session:
        first = await seed_market_index_catalog(session)
        second = await seed_market_index_catalog(session)
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(MarketIndex))
        codes = set((await session.scalars(select(MarketIndex.code))).all())
    catalog_size = len(CORE_INDEX_CATALOG) + len(SECTOR_INDEX_CATALOG)
    assert len(first) == catalog_size
    assert len(second) == catalog_size
    assert count == catalog_size
    assert "GIFT_NIFTY" in codes
    assert {
        "SP500",
        "NASDAQ_100",
        "DOW_JONES",
        "NIKKEI_225",
        "HANG_SENG",
        "SHANGHAI_COMPOSITE",
        "KOSPI",
    } <= codes
    assert {
        "NIFTY_50",
        "NIFTY_BANK",
        "NIFTY_FIN_SERV",
        "NIFTY_MIDCAP_100",
        "NIFTY_SMALLCAP_100",
        "NIFTY_NEXT_50",
        "SENSEX",
    } <= codes
    # Sector indices are folded into the canonical catalog (Task 18.7).
    assert {entry.code for entry in SECTOR_INDEX_CATALOG} <= codes


@pytest.mark.asyncio
async def test_range_ingestion_uses_one_provider_call_and_normalizes_previous_close():
    provider = FakeRangeProvider(_range_result())
    factory = get_session_factory()
    async with factory() as session:
        await seed_market_index_catalog(session)
        service = IndexHistoricalService(session, (provider,))
        results = await service.ingest_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
        await session.commit()
        prices = list(
            (
                await session.scalars(
                    select(MarketIndexPrice).order_by(MarketIndexPrice.trade_date)
                )
            ).all()
        )
    assert provider.range_calls == 1
    assert provider.fetch_calls == 0
    assert [result.status for result in results] == ["success", "success"]
    assert prices[0].previous_close is None
    assert prices[1].previous_close == Decimal("102.0000")
    assert prices[1].change == Decimal("2.0000")
    assert prices[1].source_name == "fake_primary"
    assert prices[1].source_reference == "mock://primary/sp500"
    assert prices[1].freshness_status == "recent"


@pytest.mark.asyncio
async def test_range_ingestion_skips_existing_dates_and_is_idempotent():
    provider = FakeRangeProvider(_range_result())
    factory = get_session_factory()
    async with factory() as session:
        await seed_market_index_catalog(session)
        service = IndexHistoricalService(session, (provider,))
        await service.ingest_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
        second = await service.ingest_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(MarketIndexPrice))
    assert provider.range_calls == 2
    assert count == 2
    assert all(result.status == "cached" for result in second)


@pytest.mark.asyncio
async def test_range_ingestion_uses_stored_close_before_range_boundary(db_session):
    provider = FakeRangeProvider(_range_result())
    session = db_session
    rows = await seed_market_index_catalog(session)
    sp500 = next(row for row in rows if row.code == "SP500")
    session.add(
        MarketIndexPrice(
            market_index_id=sp500.id,
            trade_date=date(2025, 1, 3),
            open=Decimal("98"),
            high=Decimal("101"),
            low=Decimal("97"),
            close=Decimal("100"),
            source_name="database_seed",
            freshness_status="stale",
        )
    )
    await session.flush()
    service = IndexHistoricalService(session, (provider,))
    await service.ingest_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
    first = await session.scalar(
        select(MarketIndexPrice).where(MarketIndexPrice.trade_date == date(2025, 1, 6))
    )
    assert first is not None
    assert first.previous_close == Decimal("100.0000")
    assert first.change == Decimal("2.0000")
    assert first.change_percent == Decimal("2.0000")


@pytest.mark.asyncio
async def test_single_date_ingestion_skips_existing_without_provider_call():
    provider = FakeRangeProvider(_range_result())
    factory = get_session_factory()
    async with factory() as session:
        await seed_market_index_catalog(session)
        service = IndexHistoricalService(session, (provider,))
        await service.ingest_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
        cached = await service.ingest_date("SP500", date(2025, 1, 7))
    assert cached.status == "cached"
    assert provider.fetch_calls == 0


@pytest.mark.asyncio
async def test_single_date_ingestion_uses_latest_stored_previous_close(db_session):
    single = _range_result().model_copy(update={"data": _range_result().data[1]})
    provider = FakeRangeProvider(single)
    session = db_session
    rows = await seed_market_index_catalog(session)
    sp500 = next(row for row in rows if row.code == "SP500")
    session.add(
        MarketIndexPrice(
            market_index_id=sp500.id,
            trade_date=date(2025, 1, 6),
            open=Decimal("100"),
            high=Decimal("103"),
            low=Decimal("99"),
            close=Decimal("102"),
            source_name="database_seed",
            freshness_status="recent",
        )
    )
    await session.flush()
    service = IndexHistoricalService(session, (provider,))
    await service.ingest_date("SP500", date(2025, 1, 7))
    inserted = await session.scalar(
        select(MarketIndexPrice).where(MarketIndexPrice.trade_date == date(2025, 1, 7))
    )
    assert inserted is not None
    assert inserted.previous_close == Decimal("102.0000")
    assert inserted.change == Decimal("2.0000")


@pytest.mark.asyncio
async def test_ingestion_falls_through_primary_to_secondary_provider(db_session):
    primary = FakeRangeProvider(
        ProviderResult(provider_name="primary", status="unavailable", freshness="missing")
    )
    secondary = FakeRangeProvider(_range_result("secondary"))
    secondary.name = "secondary"
    session = db_session
    await seed_market_index_catalog(session)
    service = IndexHistoricalService(session, (primary, secondary))
    results = await service.ingest_range("SP500", date(2025, 1, 6), date(2025, 1, 7))
    assert primary.range_calls == 1
    assert secondary.range_calls == 1
    assert all(result.status == "success" for result in results)


@pytest.mark.asyncio
async def test_single_date_provider_failure_returns_stale_db_fallback(db_session):
    provider = FakeRangeProvider(
        ProviderResult(provider_name="primary", status="failed", freshness="missing")
    )
    session = db_session
    rows = await seed_market_index_catalog(session)
    sp500 = next(row for row in rows if row.code == "SP500")
    session.add(
        MarketIndexPrice(
            market_index_id=sp500.id,
            trade_date=date(2025, 3, 10),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            source_name="database_seed",
            freshness_status="recent",
        )
    )
    await session.flush()
    result = await IndexHistoricalService(session, (provider,)).ingest_date(
        "SP500", date(2025, 3, 11)
    )
    assert result.status == "stale"
    assert result.freshness == "stale"
    assert result.market_date == "2025-03-10"


@pytest.mark.asyncio
async def test_ingestion_returns_missing_when_no_provider_or_db_data_exists(db_session):
    session = db_session
    await seed_market_index_catalog(session)
    result = await IndexHistoricalService(session, ()).ingest_date("SP500", date(2025, 3, 18))
    assert result.status == "unavailable"
    assert result.freshness == "missing"
    assert result.error_code == "no_provider"


@pytest.mark.asyncio
async def test_ingestion_rejects_oversized_or_reversed_ranges():
    factory = get_session_factory()
    async with factory() as session:
        await seed_market_index_catalog(session)
        service = IndexHistoricalService(session, ())
        with pytest.raises(ValueError, match="on or before"):
            await service.ingest_range("SP500", date(2025, 1, 8), date(2025, 1, 7))
        with pytest.raises(ValueError, match="366"):
            await service.ingest_range("SP500", date(2023, 1, 1), date(2025, 1, 7))