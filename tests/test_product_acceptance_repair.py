"""Focused regressions for Stage 1R product-truth acceptance repairs."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS, is_product_symbol
from app.db.session import get_session_factory
from app.models.ai_explanation import AIExplanation
from app.models.daily_price import DailyPrice
from app.models.eod_pipeline_run import EODPipelineRun
from app.models.import_job import ImportJob
from app.models.scan_definition import ScanDefinition
from app.models.stock import Stock
from app.models.watchlist import Watchlist, WatchlistStock
from app.services.market_data_service import MarketDataService
from app.services.scanner_readiness import SCANNER_REQUIREMENTS
from app.services.scanner_workspace import ScannerWorkspaceService
from app.services.stock import StockService
from tests.auth_helpers import superuser_client, viewer_client

EXPECTED_FIXTURE_SYMBOLS = {
    "REPRO1",
    "ANLX1",
    "ANALYTX",
    "APIT1",
    "RESL1",
    "RESL2",
    "BENCH1",
    "MMVERIFY",
}


def _price(stock_id: int, trade_date: date) -> DailyPrice:
    return DailyPrice(
        stock_id=stock_id,
        trade_date=trade_date,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        previous_close=Decimal("100"),
        volume=1000,
    )


def test_fixture_policy_is_centralized_and_case_insensitive() -> None:
    assert set(SYNTHETIC_FIXTURE_SYMBOLS) == EXPECTED_FIXTURE_SYMBOLS
    assert not is_product_symbol("analytx")
    assert is_product_symbol("SBIN")


@pytest.mark.asyncio
async def test_stock_explorer_excludes_fixtures_and_reports_sector_gap() -> None:
    async with get_session_factory()() as session:
        session.add_all(
            [
                Stock(symbol="ANALYTX", company_name="Fixture", exchange="NSE"),
                Stock(symbol="REALX", company_name="Real Equity", exchange="NSE"),
            ]
        )
        await session.commit()
        result = await StockService(session).list_explorer()

    assert [row["symbol"] for row in result["rows"]] == ["REALX"]
    assert result["summary"]["stocks"] == 1
    assert result["summary"]["sector_mapped"] == 0


@pytest.mark.asyncio
async def test_fixture_stock_detail_is_not_a_product_route() -> None:
    async with get_session_factory()() as session:
        session.add(Stock(symbol="ANALYTX", company_name="Fixture", exchange="NSE"))
        await session.commit()

    async with viewer_client(browser=True) as client:
        response = await client.get("/stocks/ANALYTX")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_weekend_coverage_ignores_fixture_prices() -> None:
    friday = date(2026, 8, 7)
    saturday = date(2026, 8, 8)
    async with get_session_factory()() as session:
        fixture = Stock(symbol="ANALYTX", company_name="Fixture", exchange="NSE")
        product = Stock(symbol="REALX", company_name="Real Equity", exchange="NSE")
        session.add_all([fixture, product])
        await session.flush()
        session.add_all([_price(product.id, friday), _price(fixture.id, saturday)])
        await session.commit()
        rows = await MarketDataService(session).coverage(friday, saturday)

    assert rows[0]["status"] == "partial"
    assert rows[0]["price_count"] == 1
    assert rows[1]["status"] == "weekend"
    assert rows[1]["price_count"] == 0


def test_scanner_rows_preserve_missing_values_and_scanner_identity() -> None:
    row = SimpleNamespace(
        symbol="SBIN",
        company_name="State Bank of India",
        sector=None,
        price=None,
        volume=None,
        avg_volume_20d=None,
        delivery_percentage=None,
        rsi_14=None,
        macd=None,
        adx=None,
        ema_20=None,
        ema_50=None,
        ema_200=None,
        scanner_score=None,
        metrics={},
        scan_date=date(2026, 8, 4),
        scanner_name="VWAP Bullish",
        scanner_code="vwap_bullish",
    )

    result = ScannerWorkspaceService(None)._row_to_dict(row)  # type: ignore[arg-type]

    assert result["price"] is None
    assert result["rsi"] is None
    assert result["macd"] is None
    assert result["adx"] is None
    assert result["ema_trend"] == "Not calculated"
    assert result["scanner_name"] == "VWAP Bullish"
    assert result["scanner_code"] == "vwap_bullish"


@pytest.mark.asyncio
async def test_every_seeded_scanner_has_explicit_readiness_metadata() -> None:
    async with get_session_factory()() as session:
        codes = set(await session.scalars(select(ScanDefinition.code)))

    assert codes == set(SCANNER_REQUIREMENTS)
    assert SCANNER_REQUIREMENTS["golden_cross"].required_sessions == 201
    assert SCANNER_REQUIREMENTS["vwap_bullish"].required_sessions == 1


@pytest.mark.asyncio
async def test_zero_row_success_is_presented_as_no_data() -> None:
    async with get_session_factory()() as session:
        session.add(
            ImportJob(
                source="NSE",
                file_name="empty.csv",
                trade_date=date(2026, 8, 4),
                status="SUCCESS",
                rows_read=0,
                rows_inserted=0,
                rows_updated=0,
                rows_rejected=0,
                details={},
            )
        )
        await session.commit()

    async with viewer_client(browser=True) as client:
        response = await client.get("/market-data")

    assert response.status_code == 200
    assert "NO DATA" in response.text
    assert "not usable imported data" in response.text


@pytest.mark.asyncio
async def test_mock_ai_rows_do_not_appear_in_product_ai_page() -> None:
    async with get_session_factory()() as session:
        stock = Stock(symbol="REALX", company_name="Real Equity", exchange="NSE")
        session.add(stock)
        await session.flush()
        session.add(
            AIExplanation(
                stock_id=stock.id,
                trade_date=date(2026, 8, 4),
                provider="mock",
                model="mock-model",
                prompt_hash="a" * 64,
                response="Mock-only product acceptance text",
                confidence=80,
            )
        )
        await session.commit()

    async with viewer_client(browser=True) as client:
        response = await client.get("/ai")
        dashboard = await client.get("/dashboard")

    assert response.status_code == 200
    assert "Mock-only product acceptance text" not in response.text
    assert "REALX" not in response.text
    assert ">mock<" not in dashboard.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("/stocks", "Stocks"),
        ("/market-data?workspace=coverage", "Data Health"),
        ("/backtests", "Backtests"),
        ("/indicators", "Indicators"),
    ],
)
async def test_full_pages_have_one_active_navigation_item(path: str, label: str) -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.text.count('class="sidebar-link active"') == 1
    assert response.text.count('class="sidebar-link mobile-active"') == 1
    assert label in response.text


@pytest.mark.asyncio
async def test_admin_scheduler_has_one_active_navigation_item() -> None:
    async with superuser_client(browser=True) as client:
        response = await client.get("/scheduler")

    assert response.status_code == 200
    assert response.text.count('class="sidebar-link active"') == 1
    assert "Automatic scheduling disabled" in response.text


@pytest.mark.asyncio
async def test_dashboard_eager_loads_and_filters_watchlist_fixture_entries() -> None:
    async with get_session_factory()() as session:
        fixture = Stock(symbol="ANALYTX", company_name="Fixture", exchange="NSE")
        product = Stock(symbol="REALX", company_name="Real Equity", exchange="NSE")
        watchlist = Watchlist(name="Acceptance Watchlist", is_default=True)
        session.add_all([fixture, product, watchlist])
        await session.flush()
        session.add_all(
            [
                WatchlistStock(watchlist_id=watchlist.id, stock_id=fixture.id),
                WatchlistStock(watchlist_id=watchlist.id, stock_id=product.id),
            ]
        )
        await session.commit()

    async with viewer_client(browser=True) as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert "1 symbols" in response.text
    assert "ANALYTX" not in response.text


@pytest.mark.asyncio
async def test_pipeline_page_renders_stored_failure_reason_inside_run_loop() -> None:
    async with get_session_factory()() as session:
        session.add(
            EODPipelineRun(
                status="failed",
                current_stage="import",
                failed_stage="import",
                error_message="Sanitized stored import failure",
            )
        )
        await session.commit()

    async with viewer_client(browser=True) as client:
        response = await client.get("/pipeline")

    assert response.status_code == 200
    assert "Sanitized stored import failure" in response.text


@pytest.mark.asyncio
async def test_insufficient_analytics_and_market_intelligence_are_explicit() -> None:
    async with viewer_client(browser=True) as client:
        analytics = await client.get("/analytics")
        intelligence = await client.get("/market-intelligence")

    assert analytics.status_code == 200
    assert "Analytics not ready" in analytics.text
    assert intelligence.status_code == 200
    assert "Market intelligence not ready" in intelligence.text
    assert "not a market signal" in intelligence.text
