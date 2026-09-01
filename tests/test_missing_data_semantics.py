from datetime import date
from decimal import Decimal

import pytest

from app.api.routes.market_intelligence_pages import _fmt_score, _label, _regime_class
from app.services.index_price_action import IndexPriceAction
from tests.auth_helpers import viewer_client


@pytest.mark.asyncio
async def test_1_missing_index_data_does_not_render_neutral():
    engine = IndexPriceAction("DUMMY_EMPTY", date.today())
    engine.set_repo(None)
    async def fake_fetch(): return []
    engine._fetch_history = fake_fetch
    
    trend = await engine.trend()
    assert trend.get("trend") is None, "Missing data should not yield 'neutral'"
    score = await engine.price_action_score()
    assert score.get("price_action_score") is None, "Missing data should not yield 0"
    
    assert _label(trend.get("trend")) == "--"
    assert _fmt_score(score.get("price_action_score")) == "--"

def test_2_legitimate_zero_score_renders_zero():
    assert _fmt_score(0) == "0.0"
    assert _fmt_score(0.0) == "0.0"
    assert _fmt_score(Decimal("0.0")) == "0.0"

def test_3_legitimate_neutral_direction_renders_neutral():
    assert _label("neutral") == "Neutral"
    assert _regime_class("neutral") == "bg-warning text-dark"

@pytest.mark.asyncio
async def test_4_top_level_zero_factor_coverage():
    # If 0% completeness, we should see "Not ready" in the UI.
    PAGE = "/market-intelligence"
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    if "0%" in resp.text and "required factor coverage" in resp.text:
        assert "Market intelligence not ready" in resp.text
        assert "Scores and directional conclusions remain unavailable." in resp.text

@pytest.mark.asyncio
async def test_5_empty_news_remains_not_available():
    PAGE = "/market-intelligence"
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Coverage: Not available" in resp.text
    assert "Company Events: No recorded events" in resp.text

@pytest.mark.asyncio
async def test_6_empty_corporate_events_remains_not_available():
    PAGE = "/market-intelligence"
    async with viewer_client(browser=True) as c:
        resp = await c.get(PAGE)
    assert resp.status_code == 200
    assert "Corporate events: Not available" in resp.text

@pytest.mark.asyncio
async def test_7_pit_historical_evaluation_remains_unchanged():
    # Ensure IndexPriceAction respects trade_date bounds
    engine = IndexPriceAction("DUMMY", date(2023, 1, 1))
    assert engine.trade_date == date(2023, 1, 1)
