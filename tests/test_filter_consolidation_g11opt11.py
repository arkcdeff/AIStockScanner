"""G.11-OPT-11 Phase 2 — Authoritative Filter Consolidation & State Consistency tests.

Validates that:
1. OI Statistics has a single authoritative filter form with no visible duplicate controls,
   canonical strikes vocabulary, backward-compatible range handling, and precedence.
2. OI Change has a single authoritative filter form, removes duplicate selects, and preserves
   the workbench-specific Analysis Window quick-toggle bar while preserving active strike scope.
3. Multistrike OI wires strikes and interval query parameters from URL into the service and
   re-echoes them to the shared filter panel, eliminating dead controls, and renders the
   Spot / ATM reference strip.
4. Options OI vs Time wires strikes and interval query parameters from URL into the service and
   re-echoes them to the shared filter panel, eliminating dead controls, and renders the
   Spot / PCR reference strip.
5. Shared filter panel preserves manual strike inputs (call_strikes, put_strikes).
6. Regression test: Option Chain, Futures OI vs Time, OI Replay, and EOD Workbench
   remain fully operational.
"""

from __future__ import annotations

import re
import pytest


@pytest.fixture
async def browser_client():
    from tests.auth_helpers import auth_client

    async with auth_client(is_superuser=True, browser=True) as client:
        yield client


def _select_blocks(html: str, name: str) -> list[str]:
    """Return all <select name="..."> blocks in HTML."""
    return re.findall(rf'<select[^>]*name="{name}".*?</select>', html, re.S)


def _forms_with_action(html: str, action_substring: str) -> list[str]:
    """Return all forms whose action contains action_substring."""
    return [
        m.group(0)
        for m in re.finditer(r'<form\b[^>]*action="([^"]*)"[^>]*>.*?</form>', html, re.S)
        if action_substring in m.group(1)
    ]


# =========================================================================== #
# 1. OI Statistics Consolidation Tests
# =========================================================================== #

async def test_oi_statistics_has_single_authoritative_filter_form(browser_client):
    """OI Statistics must render only one form for oi-statistics with no duplicate visible controls."""
    response = await browser_client.get("/market-data/derivatives/oi-statistics")
    assert response.status_code == 200
    html = response.text

    # Exactly one form targeting oi-statistics
    forms = _forms_with_action(html, "/market-data/derivatives/oi-statistics")
    assert len(forms) == 1, f"Expected 1 form for oi-statistics, found {len(forms)}"

    # Shared filter panel header present
    assert "Derivatives Filter &amp; Selection" in html or "Derivatives Filter" in html

    # Exactly one visible select control for symbol and expiry
    symbol_selects = _select_blocks(html, "symbol")
    assert len(symbol_selects) == 1, f"Expected 1 visible symbol select, found {len(symbol_selects)}"

    expiry_selects = _select_blocks(html, "expiry")
    assert len(expiry_selects) == 1, f"Expected 1 visible expiry select, found {len(expiry_selects)}"

    # Canonical strike depth control is 'strikes'
    strike_selects = _select_blocks(html, "strikes")
    assert len(strike_selects) == 1, f"Expected 1 visible strikes select, found {len(strike_selects)}"

    # Legacy 'range' control is not visible as a competing select
    range_selects = _select_blocks(html, "range")
    assert len(range_selects) == 0, f"Expected 0 visible range selects, found {len(range_selects)}"

    # But hidden input is preserved for backwards compatibility with legacy assertions
    assert 'name="range"' in html


async def test_oi_statistics_strikes_parameter_reflection_and_precedence(browser_client):
    """URL strikes parameter is reflected in the shared filter panel, and range retains precedence if sent."""
    # When strikes=20 is sent:
    res1 = await browser_client.get("/market-data/derivatives/oi-statistics?strikes=20")
    assert res1.status_code == 200
    strikes_block1 = _select_blocks(res1.text, "strikes")[0]
    assert 'value="20" selected' in strikes_block1

    # When both range=10 and strikes=20 are sent, range takes precedence
    res2 = await browser_client.get("/market-data/derivatives/oi-statistics?range=10&strikes=20")
    assert res2.status_code == 200
    strikes_block2 = _select_blocks(res2.text, "strikes")[0]
    assert 'value="10" selected' in strikes_block2


# =========================================================================== #
# 2. OI Change Consolidation & Quick-Toggle Tests
# =========================================================================== #

async def test_oi_change_has_single_authoritative_filter_form(browser_client):
    """OI Change must render only one form for oi-change with no duplicate visible controls."""
    response = await browser_client.get("/market-data/derivatives/oi-change")
    assert response.status_code == 200
    html = response.text

    # Exactly one form targeting oi-change
    forms = _forms_with_action(html, "/market-data/derivatives/oi-change")
    assert len(forms) == 1, f"Expected 1 form for oi-change, found {len(forms)}"

    # Exactly one select control for symbol, expiry, strikes, and interval
    assert len(_select_blocks(html, "symbol")) == 1
    assert len(_select_blocks(html, "expiry")) == 1
    assert len(_select_blocks(html, "strikes")) == 1
    assert len(_select_blocks(html, "interval")) == 1


async def test_oi_change_analysis_window_quick_toggle_preserves_strikes(browser_client):
    """Analysis window quick-toggle buttons must preserve active strike scope in their URLs."""
    response = await browser_client.get("/market-data/derivatives/oi-change?symbol=NIFTY&strikes=20&interval=30m")
    assert response.status_code == 200
    html = response.text

    # The 30m button should be active
    assert re.search(r'href="[^"]*interval=30m[^"]*"\s+class="btn btn-outline-secondary\s+active\s+btn-info', html) is not None

    # The quick-toggle links must include strikes=20
    for label in ("5m", "15m", "30m", "60m"):
        match = re.search(rf'href="([^"]*interval={label}[^"]*)"', html)
        assert match is not None, f"Quick-toggle link for {label} not found"
        href = match.group(1)
        assert "strikes=20" in href, f"Quick-toggle href '{href}' for {label} lost strikes parameter"

    # Filter panel reflects both strikes and interval
    strikes_block = _select_blocks(html, "strikes")[0]
    assert 'value="20" selected' in strikes_block

    interval_block = _select_blocks(html, "interval")[0]
    assert 'value="30m" selected' in interval_block


# =========================================================================== #
# 3. Multistrike OI Wiring & Market Reference Tests
# =========================================================================== #

async def test_multistrike_oi_wires_strikes_and_interval(browser_client):
    """Multistrike OI must wire strikes and interval query parameters and eliminate dead controls."""
    response = await browser_client.get("/market-data/derivatives/multistrike-oi?symbol=NIFTY&strikes=10&interval=15m")
    assert response.status_code == 200
    html = response.text

    # Exactly one form targeting multistrike-oi
    forms = _forms_with_action(html, "/market-data/derivatives/multistrike-oi")
    assert len(forms) == 1, f"Expected 1 form, found {len(forms)}"

    # Exactly one select control for symbol and expiry
    assert len(_select_blocks(html, "symbol")) == 1
    assert len(_select_blocks(html, "expiry")) == 1

    # Shared filter panel reflects strikes=10 and interval=15m
    strikes_block = _select_blocks(html, "strikes")[0]
    assert 'value="10" selected' in strikes_block

    interval_block = _select_blocks(html, "interval")[0]
    assert 'value="15m" selected' in interval_block

    # Spot and ATM market reference strip is preserved
    assert "Spot: ₹" in html
    assert "ATM:" in html

    # Malformed strikes input is rejected with 400
    res_bad = await browser_client.get("/market-data/derivatives/multistrike-oi?strikes=invalid_count")
    assert res_bad.status_code == 400


# =========================================================================== #
# 4. Options OI vs Time Wiring & Market Reference Tests
# =========================================================================== #

async def test_options_oi_time_wires_strikes_and_interval(browser_client):
    """Options OI vs Time must wire strikes and interval query parameters and eliminate dead controls."""
    response = await browser_client.get("/market-data/derivatives/options-oi-time?symbol=NIFTY&strikes=10&interval=15m")
    assert response.status_code == 200
    html = response.text

    # Exactly one form targeting options-oi-time
    forms = _forms_with_action(html, "/market-data/derivatives/options-oi-time")
    assert len(forms) == 1, f"Expected 1 form, found {len(forms)}"

    # Exactly one select control for symbol and expiry
    assert len(_select_blocks(html, "symbol")) == 1
    assert len(_select_blocks(html, "expiry")) == 1

    # Shared filter panel reflects strikes=10 and interval=15m
    strikes_block = _select_blocks(html, "strikes")[0]
    assert 'value="10" selected' in strikes_block

    interval_block = _select_blocks(html, "interval")[0]
    assert 'value="15m" selected' in interval_block

    # Spot and PCR market reference strip is preserved
    assert "Spot: ₹" in html
    assert "PCR:" in html

    # Malformed strikes input is rejected with 400
    res_bad = await browser_client.get("/market-data/derivatives/options-oi-time?strikes=invalid_count")
    assert res_bad.status_code == 400


# =========================================================================== #
# 5. Shared Filter Panel Manual Strikes Preservation
# =========================================================================== #

@pytest.mark.parametrize(
    "url",
    [
        "/market-data/derivatives/oi-statistics",
        "/market-data/derivatives/oi-change",
        "/market-data/derivatives/multistrike-oi",
        "/market-data/derivatives/options-oi-time",
    ],
)
async def test_shared_filter_panel_preserves_manual_strike_inputs(browser_client, url):
    """Manual CE and PE strike inputs must remain available in the shared filter panel."""
    response = await browser_client.get(url)
    assert response.status_code == 200
    html = response.text
    assert 'name="call_strikes"' in html
    assert 'name="put_strikes"' in html


# =========================================================================== #
# 6. Untouched Pages Regression Check
# =========================================================================== #

@pytest.mark.parametrize(
    "url",
    [
        "/market-data/options?symbol=NIFTY",
        "/market-data/derivatives/futures-oi-time?symbol=NIFTY",
        "/market-data/derivatives/oi-replay?symbol=NIFTY",
        "/market-data/derivatives",
        "/market-data/derivatives/advanced-exposure/tab?symbol=NIFTY&trade_date=2026-09-29&expiry_date=",
    ],
)
async def test_untouched_derivatives_pages_remain_healthy(browser_client, url):
    """Non-target derivatives workbenches must remain fully reachable and healthy."""
    response = await browser_client.get(url)
    assert response.status_code == 200
