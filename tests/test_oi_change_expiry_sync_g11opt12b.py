"""Focused regression tests — Options workspace expiry synchronization.

Defect repaired here: after an underlying change the OI Change tab could keep
operating on the previous underlying's expiry. Three linked causes:

  1. the workspace script allowed a tab refresh while the new underlying's
     expiry options were still being fetched, so the request was built from the
     outgoing expiry value;
  2. the workspace URL was not re-synchronized after the expiry-options swap,
     so a refresh/bookmark reloaded an expiry the screen never showed (an
     expiry that is not even listed for the newly selected underlying);
  3. the page route trusted the requested expiry blindly, pre-rendering every
     tab — including OI Change — for a scope the underlying does not have.

Authoritative rule under test: the workspace controls are the single source of
truth, and only an expiry listed for the selected underlying may drive a view.
An unlisted expiry falls back to the nearest listed expiry (exactly the value
the expiry dropdown selects when no expiry is supplied); it is never invented
and never silently reused.

No production behavior beyond this rule is exercised: the stubs below record
only which expiry each pre-render call receives.
"""
from __future__ import annotations

import re
from datetime import date

import pytest

from tests.auth_helpers import auth_client

WORKSPACE_TEMPLATE = "app/templates/options_workspace.html"
PAGE_ROUTE_MODULE = "app/api/routes/market_data_pages.py"

LISTED = ["2026-10-27", "2026-11-23", "2026-12-29"]
UNLISTED = "2026-10-06"  # a NIFTY weekly that BANKNIFTY does not list


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _expiry_rows(tokens: list[str]) -> list[dict]:
    return [
        {
            "expiry_date": token,
            "display_label": token,
            "days_to_expiry": 7 * (idx + 1),
            "expiry_type": "WEEKLY" if idx == 0 else "MONTHLY",
            "is_nearest": idx == 0,
            "is_next": idx == 1,
            "is_current_monthly": idx == 1,
            "contract_count": 10,
            "provider": "TEST",
            "provenance": "TEST",
            "is_provider_listed": True,
            "status": "ACTIVE",
        }
        for idx, token in enumerate(tokens)
    ]


def _install_stub_service(monkeypatch: pytest.MonkeyPatch, expiries: list[str]) -> dict:
    """Patch the page route's service with recording stubs.

    Returns a recorder capturing the ``expiry_date`` every pre-render call
    received (``None`` when the call was made without an expiry).
    """
    from app.services import intraday_options_service as svc_module

    recorder: dict = {"calls": {}, "expiries": expiries}

    async def _supported(self):  # noqa: ANN001
        return [{"symbol": "BANKNIFTY", "name": "NIFTY BANK", "strike_step": 100}]

    async def _available(self, underlying: str):  # noqa: ANN001
        return _expiry_rows(expiries)

    async def _chain(self, underlying, expiry_date=None, strike_count=None, **kwargs):  # noqa: ANN001
        recorder["calls"]["chain"] = expiry_date
        return {
            "ladder": [],
            "selected_expiry": expiry_date.isoformat() if expiry_date else None,
            "underlying": "BANKNIFTY",
            "underlying_name": "NIFTY BANK",
            "spot_price": 0.0,
            "atm_strike": None,
            "strike_step": 100.0,
            "summary": {"strike_coverage_label": "ATM ± 0 Strikes (0 strikes)"},
            "provider": "TEST",
            "freshness": "TEST",
            "as_of_ist": "—",
            "market_session": "CLOSED",
            "is_market_open": False,
            "available_expiries": list(expiries),
        }

    async def _dashboard(self, underlying, expiry_date=None, **kwargs):  # noqa: ANN001
        recorder["calls"]["dashboard"] = expiry_date
        return None

    async def _pcr(self, underlying, expiry_date=None, **kwargs):  # noqa: ANN001
        recorder["calls"]["pcr"] = expiry_date
        return None

    async def _oi(self, underlying, expiry_date=None, **kwargs):  # noqa: ANN001
        recorder["calls"]["oi_comparison"] = expiry_date
        return None

    async def _oi_change(self, underlying, expiry_date=None, **kwargs):  # noqa: ANN001
        recorder["calls"]["oi_change"] = expiry_date
        return None

    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_supported_underlyings", _supported)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_available_expiries", _available)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_option_chain_ladder", _chain)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_option_dashboard", _dashboard)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_live_pcr", _pcr)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_call_vs_put_oi", _oi)
    monkeypatch.setattr(svc_module.IntradayOptionsService, "get_oi_change_analysis", _oi_change)
    return recorder


def _selected_options(html: str) -> list[str]:
    """Options marked ``selected`` inside the expiry dropdown only."""
    select = re.search(r'<select[^>]*id="opt-expiry"[^>]*>([\s\S]*?)</select>', html)
    if select is None:
        return []
    return [
        value
        for value, attrs in re.findall(r'<option value="([^"]*)"([^>]*)>', select.group(1))
        if "selected" in attrs
    ]


# ---------------------------------------------------------------------------
# Page route — one valid expiry for every tab
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listed_expiry_drives_every_tab_unchanged(monkeypatch):
    """A listed expiry is used verbatim by chain, dashboard, PCR, OI and OI Change."""
    recorder = _install_stub_service(monkeypatch, LISTED)
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get(
            "/market-data/options", params={"symbol": "BANKNIFTY", "expiry": "2026-11-23"}
        )
    assert r.status_code == 200
    calls = recorder["calls"]
    assert calls["chain"] == date(2026, 11, 23)
    assert calls["dashboard"] == date(2026, 11, 23)
    assert calls["pcr"] == date(2026, 11, 23)
    assert calls["oi_comparison"] == date(2026, 11, 23)
    assert calls["oi_change"] == date(2026, 11, 23)
    assert _selected_options(r.text) == ["2026-11-23"]


@pytest.mark.asyncio
async def test_unlisted_expiry_falls_back_to_nearest_listed_expiry(monkeypatch):
    """The previous underlying's expiry must never drive the new underlying's tabs."""
    recorder = _install_stub_service(monkeypatch, LISTED)
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get(
            "/market-data/options", params={"symbol": "BANKNIFTY", "expiry": UNLISTED}
        )
    assert r.status_code == 200
    calls = recorder["calls"]
    for name, value in calls.items():
        assert value == date(2026, 10, 27), f"{name} received {value}"
    # The dropdown marks the effective (nearest listed) expiry, not the unlisted one.
    assert _selected_options(r.text) == ["2026-10-27"]
    # The unlisted expiry is not rendered as an option at all.
    assert f'value="{UNLISTED}"' not in r.text


@pytest.mark.asyncio
async def test_missing_expiry_uses_nearest_listed_expiry(monkeypatch):
    recorder = _install_stub_service(monkeypatch, LISTED)
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get("/market-data/options", params={"symbol": "BANKNIFTY"})
    assert r.status_code == 200
    assert recorder["calls"]["oi_change"] == date(2026, 10, 27)
    assert recorder["calls"]["chain"] == date(2026, 10, 27)
    assert _selected_options(r.text) == ["2026-10-27"]


@pytest.mark.asyncio
async def test_no_listed_expiries_renders_truthfully_without_expiry(monkeypatch):
    recorder = _install_stub_service(monkeypatch, [])
    async with auth_client(role="viewer", browser=True) as client:
        r = await client.get(
            "/market-data/options", params={"symbol": "BANKNIFTY", "expiry": UNLISTED}
        )
    assert r.status_code == 200
    assert all(value is None for value in recorder["calls"].values())
    assert "No Open Interest Change Data Available" in r.text


@pytest.mark.asyncio
async def test_unlisted_expiry_never_reaches_any_view(monkeypatch):
    """Negative control: the outgoing underlying's expiry is never passed on."""
    recorder = _install_stub_service(monkeypatch, LISTED)
    async with auth_client(role="viewer", browser=True) as client:
        await client.get("/market-data/options", params={"symbol": "BANKNIFTY", "expiry": UNLISTED})
    assert UNLISTED not in {value.isoformat() for value in recorder["calls"].values() if value}


# ---------------------------------------------------------------------------
# Workspace script — single authoritative control state
# ---------------------------------------------------------------------------


def test_tab_refreshes_hold_until_new_expiries_are_in_the_dom():
    src = _read(WORKSPACE_TEMPLATE)
    script = src[src.index("Options Workspace Auto-Sync Script"):]

    # A flag set on underlying change ...
    assert "let expiryOptionsRefreshing = false;" in script
    assert re.search(
        r"if \(id === 'opt-symbol'\) \{[\s\S]*?expiryOptionsRefreshing = true;", script
    )
    # ... that suppresses tab refreshes while the expiry options are in flight.
    assert re.search(
        r"function refreshActiveTab\(\) \{\s*if \(expiryOptionsRefreshing\) \{[\s\S]*?return;",
        script,
    )


def test_expiry_swap_resyncs_url_then_refreshes_from_the_new_selection():
    script = _read(WORKSPACE_TEMPLATE)
    script = script[script.index("Options Workspace Auto-Sync Script"):]
    block = re.search(
        r"htmx:afterSwap', function\(evt\) \{[\s\S]*?\n    \}\);", script
    )
    assert block is not None, "expiry-container afterSwap handler missing"
    body = block.group(0)
    assert "target.id === 'opt-expiry-container'" in body
    clear_at = body.index("expiryOptionsRefreshing = false;")
    sync_at = body.index("syncWorkspaceUrl();")
    refresh_at = body.index("refreshActiveTab();")
    assert clear_at < sync_at < refresh_at, (
        "the swapped-in expiry must be released, mirrored into the URL, and only "
        "then drive the tab refresh"
    )


def test_failed_expiry_refresh_releases_the_hold():
    script = _read(WORKSPACE_TEMPLATE)
    script = script[script.index("Options Workspace Auto-Sync Script"):]
    for event_name in ("htmx:responseError", "htmx:sendError", "htmx:timeout"):
        assert event_name in script, f"{event_name} must release the hold"
    block = re.search(
        r"\[\s*'htmx:responseError'[\s\S]*?\n    \}\);", script
    )
    assert block is not None and "expiryOptionsRefreshing = false;" in block.group(0)


def test_oi_change_and_chain_read_the_same_control_state():
    script = _read(WORKSPACE_TEMPLATE)
    script = script[script.index("Options Workspace Auto-Sync Script"):]
    # One control read for every tab branch, including OI Change.
    assert script.count("getFilterParams()") >= 2
    assert re.search(
        r"const \{ symbol, expiry, strikeCount \} = getFilterParams\(\);", script
    )
    # The OI Change branch consumes exactly those destructured values.
    oi_branch = script[script.index("tab-oichange-btn"):]
    assert "/market-data/options/oi-change-view?symbol=${symbol}&expiry=${expiry}" in oi_branch
    chain_branch = script[script.index("tab-chain-btn"):script.index("tab-dashboard-btn")]
    assert "/market-data/options/chain-table?symbol=${symbol}&expiry=${expiry}" in chain_branch
    # No second expiry source or hardcoded date anywhere in the workspace script.
    assert "expiry_date =" not in script
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", script), "no hardcoded expiry in the script"


def test_expiry_dropdown_is_still_the_only_expiry_control():
    src = _read(WORKSPACE_TEMPLATE)
    assert src.count('id="opt-expiry"') == 1
    assert 'name="expiry_date"' in src
    assert 'hx-get="/market-data/options/expiries-select"' in src
    assert 'hx-target="#opt-expiry-container"' in src


def test_page_route_validates_expiry_against_listed_expiries():
    """The page route must resolve its expiry through the shared listed-expiry rule.

    G.11-OPT-13 Phase A moved the rule out of this route and into
    ``_canonical_listed_expiry`` so the page, the five tab partials and the
    standalone derivatives pages cannot validate it differently. The behavior the
    original assertion protected is unchanged; it is now asserted on the shared
    implementation, and the route is asserted to delegate to it.
    """
    src = _read(PAGE_ROUTE_MODULE)
    rule = src[src.index("async def _canonical_listed_expiry("):]
    rule = rule[: rule.index("async def _derivatives_filter_context(")]
    assert "listed_tokens" in rule
    assert "expiry.isoformat() in listed_tokens" in rule
    assert "expiries[0][\"expiry_date\"]" in rule
    # The fallback is logged, not silent.
    assert "not listed for" in rule
    route = src[src.index("async def options_workspace_page"):]
    route = route[: route.index("@router.get(\"/derivatives/oi-statistics\"")]
    assert "_canonical_listed_expiry(" in route, "the page route must use the shared rule"
