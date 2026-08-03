import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_dashboard_page() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "AI Stock Scanner" in response.text
        assert "Dashboard" in response.text


@pytest.mark.asyncio
async def test_dashboard_root_redirect() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Dashboard" in response.text


@pytest.mark.asyncio
async def test_dashboard_search_empty() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard/search?q=")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_dashboard_search_with_query() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard/search?q=RELIANCE")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
