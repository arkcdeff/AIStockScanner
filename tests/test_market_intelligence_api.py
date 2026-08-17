"""Task 18.11: REST API tests for Market Intelligence endpoints.

Tests cover authentication, authorization, domain endpoints, serialization,
error handling, and refresh. No external network requests.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.auth_helpers import (
    analyst_client,
    viewer_client,
)

API = "/api/v1/market-intelligence"


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_returns_401():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"{API}/latest")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_viewer_can_read_latest():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/latest")
    # 404 is OK if DB is empty (no snapshots)
    assert resp.status_code in (200, 404)


@pytest.mark.asyncio
async def test_viewer_can_read_explanation():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/explanation")
    assert resp.status_code == 200
    data = resp.json()
    assert "headline" in data
    assert "is_ai_generated" in data


@pytest.mark.asyncio
async def test_viewer_can_read_breadth():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/breadth")
    assert resp.status_code == 200
    data = resp.json()
    assert "breadth_score" in data


@pytest.mark.asyncio
async def test_viewer_can_read_sectors():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/sectors")
    assert resp.status_code == 200
    data = resp.json()
    assert "sector_intelligence_score" in data


@pytest.mark.asyncio
async def test_viewer_can_read_macro():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/macro")
    assert resp.status_code == 200
    data = resp.json()
    assert "macro_score" in data


@pytest.mark.asyncio
async def test_viewer_can_read_global():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/global")
    assert resp.status_code == 200
    data = resp.json()
    assert "global_score" in data


@pytest.mark.asyncio
async def test_viewer_can_read_gift_nifty():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/gift-nifty")
    assert resp.status_code == 200
    data = resp.json()
    assert "gift_nifty_score" in data


@pytest.mark.asyncio
async def test_viewer_can_read_institutional():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/institutional")
    assert resp.status_code == 200
    data = resp.json()
    assert "institutional_score" in data


@pytest.mark.asyncio
async def test_viewer_can_read_external_factors():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/external-factors")
    assert resp.status_code == 200
    data = resp.json()
    assert "india_vix" in data
    assert "usd_inr" in data
    assert "brent_crude" in data


@pytest.mark.asyncio
async def test_viewer_can_read_factors():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/factors")
    assert resp.status_code in (200, 404)


@pytest.mark.asyncio
async def test_viewer_can_read_indian_indices():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/indian-indices")
    assert resp.status_code == 200
    data = resp.json()
    assert "indices" in data


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_requires_date_range():
    async with viewer_client() as c:
        resp = await c.get(
            f"{API}/history",
            params={"start_date": "2025-01-10", "end_date": "2025-01-05"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_history_valid_range():
    async with viewer_client() as c:
        resp = await c.get(
            f"{API}/history",
            params={"start_date": "2025-01-01", "end_date": "2025-01-10"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_history_rejects_range_over_max():
    async with viewer_client() as c:
        resp = await c.get(
            f"{API}/history",
            params={"start_date": "2020-01-01", "end_date": "2025-01-01"},
        )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Refresh permission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_requires_refresh_permission():
    """Viewer has market_intelligence.read but NOT market_intelligence.refresh."""
    async with viewer_client() as c:
        resp = await c.post(f"{API}/refresh")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_analyst_can_refresh():
    """Analyst has market_intelligence.refresh."""
    async with analyst_client() as c:
        resp = await c.post(f"{API}/refresh")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "composite_score" in data
    assert "regime" in data


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explanation_response_serialization():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/explanation")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["bullish_factors"], list)
    assert isinstance(data["bearish_factors"], list)
    assert isinstance(data["risk_factors"], list)
    assert isinstance(data["missing_data"], list)
    assert isinstance(data["stale_data"], list)
    assert isinstance(data["watch_items"], list)
    assert isinstance(data["is_ai_generated"], bool)
    assert isinstance(data["fallback_used"], bool)


@pytest.mark.asyncio
async def test_breadth_response_serialization():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/breadth")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("advancing_count"), (int, type(None)))
    assert isinstance(data.get("declining_count"), (int, type(None)))
    assert isinstance(data.get("net_advances"), (int, type(None)))


@pytest.mark.asyncio
async def test_external_factors_serialization():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/external-factors")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("india_vix", "usd_inr", "brent_crude"):
        item = data.get(key)
        if item is not None:
            assert "index_code" in item
            assert "score" in item
            assert "freshness" in item


# ---------------------------------------------------------------------------
# Empty DB behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_returns_404_when_no_snapshots():
    async with viewer_client() as c:
        resp = await c.get(f"{API}/latest")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data


# ---------------------------------------------------------------------------
# Full endpoint coverage (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_endpoints_return_valid_json():
    """Smoke test: every endpoint returns valid JSON with no crashes."""
    endpoints = [
        "/explanation",
        "/breadth",
        "/sectors",
        "/macro",
        "/global",
        "/gift-nifty",
        "/institutional",
        "/external-factors",
        "/indian-indices",
    ]
    async with viewer_client() as c:
        for ep in endpoints:
            resp = await c.get(f"{API}{ep}")
            assert resp.status_code == 200, f"{ep} returned {resp.status_code}"
            data = resp.json()
            assert isinstance(data, dict), f"{ep} did not return a dict"
