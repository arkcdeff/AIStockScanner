import pytest

from tests.auth_helpers import analyst_client


@pytest.mark.asyncio
async def test_scanner_results_page() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/scanner-results/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Scanner Results Workspace" in response.text


@pytest.mark.asyncio
async def test_scanner_results_table_htmx() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/scanner-results/table")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_scanner_results_export_csv() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/scanner-results/export/csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_scanner_results_export_excel() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/scanner-results/export/excel")
    assert response.status_code == 200
    assert (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        in response.headers["content-type"]
    )
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_scanner_results_export_pdf() -> None:
    async with analyst_client(browser=True) as client:
        response = await client.get("/scanner-results/export/pdf")
    assert response.status_code == 200
    assert "application/pdf" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]