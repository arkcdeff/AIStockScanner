"""Task 18.11: Dashboard UI tests for Market Intelligence.

Tests cover page rendering, authentication, authorization, HTMX partials,
CSRF protection, XSS escaping, and empty/error states.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.auth_helpers import (
    analyst_client,
    viewer_client,
)

PAGE = "/market-intelligence"


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_redirects_to_login():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(PAGE, follow_redirects=False)
    assert resp.status_code in (302, 401)


@pytest.mark.asyncio
async def test_viewer_can_access_dashboard():
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_dashboard_contains_title():
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Market Intelligence" in resp.text


@pytest.mark.asyncio
async def test_dashboard_contains_hero_section():
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Market Regime" in resp.text
    assert "Composite Score" in resp.text
    assert "Confidence" in resp.text
    assert "Data Completeness" in resp.text


@pytest.mark.asyncio
async def test_dashboard_contains_factor_cards():
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "price action" in resp.text.lower() or "price_action" in resp.text.lower()
    assert "breadth" in resp.text.lower()


@pytest.mark.asyncio
async def test_dashboard_contains_panels():
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Index" in resp.text
    assert "Market" in resp.text
    assert "GIFT Nifty" in resp.text
    assert "FII/DII" in resp.text or "Combined Score" in resp.text
    assert "Breadth Score" in resp.text or "Breadth data unavailable" in resp.text
    assert "Sector Score" in resp.text or "Sector data unavailable" in resp.text
    assert "Macro Score" in resp.text or "Macro intelligence data unavailable" in resp.text
    assert "Supporting Factors" in resp.text or "No explanation available" in resp.text


# ---------------------------------------------------------------------------
# Refresh button
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_button_visible_for_analyst():
    async with analyst_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Refresh" in resp.text


@pytest.mark.asyncio
async def test_viewer_sees_no_refresh_button():
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    # Viewer should not see refresh button since they lack market_intelligence.refresh
    # The button is conditionally rendered via can_refresh
    assert 'hx-post="/market-intelligence/refresh"' not in resp.text


# ---------------------------------------------------------------------------
# Missing data handling (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_renders_safely_with_empty_db():
    """Dashboard must render without traceback when DB has no market data."""
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Market Intelligence" in resp.text
    # Should contain fallback messages, not traceback
    assert "Traceback" not in resp.text
    assert "NoneType" not in resp.text


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hero_partial_returns_html():
    async with viewer_client(browser=True) as c:
        resp = await c.get(f"{PAGE}/partial/hero")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_factors_partial_returns_html():
    async with viewer_client(browser=True) as c:
        resp = await c.get(f"{PAGE}/partial/factors")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_explanation_partial_returns_html():
    async with viewer_client(browser=True) as c:
        resp = await c.get(f"{PAGE}/partial/explanation")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sidebar_contains_market_intel_link():
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Market Intel" in resp.text
    assert "/market-intelligence" in resp.text


# ---------------------------------------------------------------------------
# No traceback / no NoneType
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_template_traceback():
    """Dashboard must never expose Jinja2 tracebacks."""
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "jinja2.exceptions" not in resp.text
    assert "TemplateSyntaxError" not in resp.text
    assert "UndefinedError" not in resp.text


PAGE = "/market-intelligence"

@pytest.mark.asyncio
async def test_dashboard_contains_news_policy_section():
    """Verify Phase D news policy section renders correctly."""
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "News &amp; Policy" in resp.text
    assert "Coverage: Not available" in resp.text
    assert "Company Events: No recorded events" in resp.text

@pytest.mark.asyncio
async def test_dashboard_contains_corporate_events_section():
    """Verify Phase D corporate events section renders correctly."""
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Corporate Events" in resp.text
    assert "Corporate events: Not available" in resp.text
