"""UI smoke tests for the Sprint 16.8 browser pages.

Covers the market data, indicators, scanner library/run, pipeline, scheduler,
analytics, AI, and stocks HTML pages plus their HTMX partial endpoints.
"""

import pytest

from tests.auth_helpers import viewer_client


@pytest.mark.asyncio
async def test_market_data_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/market-data")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Market" in response.text


@pytest.mark.asyncio
async def test_market_data_prices_partial() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/market-data/prices")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_market_data_imports_partial() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/market-data/imports")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_indicators_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/indicators")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Indicator" in response.text


@pytest.mark.asyncio
async def test_indicators_status_partial() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/indicators/status")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_scanner_library_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/scanner-library")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Scanner" in response.text


@pytest.mark.asyncio
async def test_scanner_run_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/scanner-run")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Scanner" in response.text


@pytest.mark.asyncio
async def test_pipeline_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/pipeline")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Pipeline" in response.text


@pytest.mark.asyncio
async def test_scheduler_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/scheduler")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Scheduler" in response.text


@pytest.mark.asyncio
async def test_scheduler_status_partial() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/scheduler/status")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_analytics_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/analytics")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Analytics" in response.text


@pytest.mark.asyncio
async def test_analytics_table_partial() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/analytics/table?kind=top-rated")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_ai_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/ai")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "AI" in response.text


@pytest.mark.asyncio
async def test_ai_recent_partial() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/ai/recent")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_stocks_page() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/stocks")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Stocks" in response.text


@pytest.mark.asyncio
async def test_stocks_table_partial() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/stocks/table")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]