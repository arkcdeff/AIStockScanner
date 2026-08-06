import pytest

from tests.auth_helpers import analyst_client


@pytest.mark.asyncio
async def test_indicator_calculate_endpoint() -> None:
    async with analyst_client() as client:
        response = await client.post("/api/v1/indicators/calculate")
        assert response.status_code == 202
        body = response.json()
        assert "calculated" in body
        assert "skipped" in body


@pytest.mark.asyncio
async def test_indicator_calculate_for_symbol() -> None:
    async with analyst_client() as client:
        response = await client.post("/api/v1/indicators/calculate?symbol=RELIANCE")
        assert response.status_code in (202, 404)


@pytest.mark.asyncio
async def test_indicator_status_endpoint() -> None:
    async with analyst_client() as client:
        response = await client.get("/api/v1/indicators/status")
        assert response.status_code == 200
        body = response.json()
        assert "total_indicator_rows" in body
        assert "stocks_with_indicators" in body


@pytest.mark.asyncio
async def test_indicator_status_for_symbol() -> None:
    async with analyst_client() as client:
        response = await client.get("/api/v1/indicators/status?symbol=RELIANCE")
        assert response.status_code in (200, 404)
