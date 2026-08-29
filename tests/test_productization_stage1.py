"""Focused regression tests for Productization Stage 1 UI contracts."""

import pytest

from app.db.session import get_session_factory
from app.models.stock import Stock
from tests.auth_helpers import superuser_client, viewer_client


@pytest.mark.asyncio
async def test_data_health_navigation_targets_full_page_workspace() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert 'href="/market-data?workspace=coverage"' in response.text
    assert 'href="/market-data/coverage"' not in response.text


@pytest.mark.asyncio
async def test_permission_aware_sidebar_is_consistent_outside_dashboard() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/stocks")

    assert response.status_code == 200
    assert "Data Operations" in response.text
    assert "Scanner Results" in response.text
    assert 'href="/admin/users"' not in response.text


@pytest.mark.asyncio
async def test_superuser_navigation_exposes_distinct_admin_destinations() -> None:
    async with superuser_client(browser=True) as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert 'href="/admin/users"' in response.text
    assert 'href="/admin/roles"' in response.text
    assert 'href="/admin/permissions"' in response.text


@pytest.mark.asyncio
async def test_stock_explorer_renders_price_and_readiness_contract() -> None:
    async with get_session_factory()() as session:
        session.add(
            Stock(
                symbol="STAGE1",
                company_name="Stage One Product Reality Ltd",
                exchange="NSE",
                is_active=True,
            )
        )
        await session.commit()
    async with viewer_client(browser=True) as client:
        response = await client.get("/stocks")

    assert response.status_code == 200
    assert "Stock Explorer" in response.text
    assert "Previous" in response.text
    assert "Change %" in response.text
    assert "History readiness" in response.text
    assert "insufficient history, not a zero-opportunity signal" in response.text.lower()


@pytest.mark.asyncio
async def test_scanner_journey_has_real_destinations_and_readiness() -> None:
    async with viewer_client(browser=True) as client:
        library = await client.get("/scanner-library")
        run_page = await client.get("/scanner-run")
        results = await client.get("/scanner-results/")

    assert library.status_code == 200
    assert "/scanner-run?scan_code=" in library.text
    assert "Maximum history" in library.text
    assert run_page.status_code == 200
    assert 'name="scan_code"' in run_page.text
    assert results.status_code == 200
    assert "Persisted discovery output" in results.text


@pytest.mark.asyncio
async def test_derivatives_workspace_wraps_existing_research_services() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/market-data/derivatives")

    assert response.status_code == 200
    assert "Derivatives Workbench" in response.text
    assert "Historical / replay mode" in response.text
    assert "Option Chain" in response.text
    assert "PCR / Max Pain / OI" in response.text
    assert "Advanced research" in response.text


@pytest.mark.asyncio
async def test_dashboard_uses_decision_and_data_reality_language() -> None:
    async with viewer_client(browser=True) as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert "Decision Dashboard" in response.text
    assert "Equity scanner readiness is limited" in response.text
    assert "Recent Alert Events" in response.text
    assert "Market Regime" not in response.text
    assert "[{&#39;id&#39;:" not in response.text
