"""Unit & Integration Tests for Milestone G.11-OPT-2: Derivatives & OI Analytics Enhancement.

Covers:
  1. Dynamic multi-contract expiry discovery (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX).
  2. Weekly vs Monthly expiry categorization without hardcoded dates.
  3. Index instrument key resolution.
  4. Buildup classification across all four quadrants (LB, SB, SC, LU, Neutral).
  5. Max Pain mathematical calculation and loss curve.
  6. Comprehensive OI Statistics Dashboard service & KPI cards.
  7. OI Statistics page HTML rendering.
  8. OI Change interval analytics (5m, 15m, 30m, 60m, session) and baseline disclosures.
  9. OI Change page HTML rendering.
  10. Multistrike OI synchronized time-series.
  11. Options OI vs Time synchronized time-series.
  12. Futures OI vs Time with transparent provider entitlement disclosure.
  13. Historical replay timeline and point-in-time safety.
  14. Navigation preservation (/market-data/options and /market-data/derivatives).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import market_data_pages
from app.main import create_app
from app.models.auth import User
from app.providers.upstox_live_provider import (
    resolve_index_instrument_key,
)
from app.services.auth_service import AuthenticatedUser
from app.services.intraday_options_service import (
    IntradayOptionsService,
    calculate_max_pain,
    classify_buildup,
)


def _override_market_data_auth(app):
    dummy_user = AuthenticatedUser(
        user=MagicMock(spec=User, id=1, is_superuser=True),
        principal=MagicMock(),
        roles=("admin",),
        permissions=("stocks.read", "market_data.write"),
    )
    dep = market_data_pages.router.dependencies[0].dependency
    app.dependency_overrides[dep] = lambda: dummy_user
    return dep


# ===========================================================================
# 1. Dynamic Expiry & Contract Resolution Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_01_dynamic_expiry_multi_contract() -> None:
    """1. Verify dynamic multi-expiry discovery returns multiple contracts without hardcoding."""
    svc = IntradayOptionsService(force_mock=True)

    for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
        expiries = await svc.get_available_expiries(sym)
        # G.11-OPT-3: this used to demand >= 4 expiries, which only held because a
        # generic Thursday calendar was returned as if it were a listing. The
        # authorized contract master is now authoritative and genuinely lists fewer
        # contracts for some indices, so the count is bounded rather than fixed.
        assert expiries, f"{sym} should return the provider-listed expiries"
        assert len(expiries) <= 24, f"{sym} returned an implausibly long expiry list"

        dates = [date.fromisoformat(e["expiry_date"]) for e in expiries]
        assert dates == sorted(dates), f"{sym} expiries must be chronologically sorted"
        assert len(set(dates)) == len(dates), f"{sym} expiries must not repeat"

        today = date.today()
        for d in dates:
            assert d >= today, f"Expiry {d} must be in the future or today"

        for e in expiries:
            # Every entry states whether it is a real listing or a labelled
            # calendar estimate; nothing may be presented as listed without proof.
            assert e["is_provider_listed"] in (True, False)
            if not e["is_provider_listed"]:
                assert e["provenance"] == "CALENDAR_DERIVED_FALLBACK"


@pytest.mark.asyncio
async def test_02_dynamic_expiry_categorization() -> None:
    """2. Verify weekly and monthly expiry classification and quick selection flags."""
    svc = IntradayOptionsService(force_mock=True)
    expiries = await svc.get_available_expiries("NIFTY")

    types = {e["expiry_type"] for e in expiries}
    assert "WEEKLY" in types, "Expiries should contain weekly contracts"
    assert "MONTHLY" in types, "Expiries should contain monthly contracts"

    assert expiries[0]["is_nearest"] is True
    assert expiries[1]["is_next"] is True
    assert any(e["is_current_monthly"] is True for e in expiries)

    for e in expiries:
        assert "display_label" in e
        assert ("Weekly" in e["display_label"] or "Monthly" in e["display_label"])


def test_03_index_instrument_key_resolution() -> None:
    """3. Verify correct Upstox instrument key mapping for Indian indices."""
    assert resolve_index_instrument_key("NIFTY") == "NSE_INDEX|Nifty 50"
    assert resolve_index_instrument_key("NIFTY 50") == "NSE_INDEX|Nifty 50"
    assert resolve_index_instrument_key("BANKNIFTY") == "NSE_INDEX|Nifty Bank"
    assert resolve_index_instrument_key("FINNIFTY") == "NSE_INDEX|Nifty Fin Service"
    assert resolve_index_instrument_key("MIDCPNIFTY") == "NSE_INDEX|NIFTY MID SELECT"
    assert resolve_index_instrument_key("SENSEX") == "BSE_INDEX|SENSEX"
    assert resolve_index_instrument_key("RELIANCE") == "NSE_EQ|RELIANCE"


# ===========================================================================
# 2. Buildup Classification Tests
# ===========================================================================

def test_04_buildup_classification_all_quadrants() -> None:
    """4. Verify option contract buildup logic across all four quadrants and edge cases."""
    # Long Buildup: Premium ↑, OI ↑
    lb = classify_buildup(price_change=12.50, oi_change=50000)
    assert lb["classification"] == "LONG_BUILDUP"
    assert lb["code"] == "LB"
    assert lb["color_class"] == "success"

    # Short Buildup: Premium ↓, OI ↑
    sb = classify_buildup(price_change=-8.25, oi_change=45000)
    assert sb["classification"] == "SHORT_BUILDUP"
    assert sb["code"] == "SB"
    assert sb["color_class"] == "danger"

    # Short Covering: Premium ↑, OI ↓
    sc = classify_buildup(price_change=15.00, oi_change=-30000)
    assert sc["classification"] == "SHORT_COVERING"
    assert sc["code"] == "SC"

    # Long Unwinding: Premium ↓, OI ↓
    lu = classify_buildup(price_change=-11.20, oi_change=-25000)
    assert lu["classification"] == "LONG_UNWINDING"
    assert lu["code"] == "LU"

    # Neutral / Edge cases
    assert classify_buildup(price_change=0, oi_change=1000)["classification"] == "NEUTRAL"
    assert classify_buildup(price_change=5.0, oi_change=0)["classification"] == "NEUTRAL"
    assert classify_buildup(price_change=None, oi_change=1000)["classification"] == "NEUTRAL"


# ===========================================================================
# 3. Max Pain Calculation Tests
# ===========================================================================

def test_05_max_pain_calculation_and_loss_curve() -> None:
    """5. Verify mathematical Max Pain strike identification and payout loss curve."""
    mock_ladder = [
        {"strike_price": 24000.0, "call": {"oi": 10000}, "put": {"oi": 50000}},
        {"strike_price": 24500.0, "call": {"oi": 60000}, "put": {"oi": 70000}},
        {"strike_price": 25000.0, "call": {"oi": 90000}, "put": {"oi": 15000}},
    ]

    max_pain, loss_curve = calculate_max_pain(mock_ladder)
    assert max_pain is not None
    assert max_pain in [24000.0, 24500.0, 25000.0]
    assert len(loss_curve) == 3
    for pt in loss_curve:
        assert "strike_price" in pt
        assert "total_payout" in pt
        assert pt["total_payout"] >= 0


# ===========================================================================
# 4. OI Statistics Service & UI Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_06_oi_statistics_service() -> None:
    """6. Verify comprehensive OI statistics computation."""
    svc = IntradayOptionsService(force_mock=True)
    stats = await svc.get_oi_statistics("NIFTY")

    assert stats["underlying"] == "NIFTY"
    assert stats["spot_price"] > 0
    assert stats["atm_strike"] > 0
    assert stats["total_call_oi"] > 0
    assert stats["total_put_oi"] > 0
    assert stats["total_oi"] == stats["total_call_oi"] + stats["total_put_oi"]
    assert stats["net_oi"] == stats["total_put_oi"] - stats["total_call_oi"]
    assert stats["pcr_oi"] is not None
    assert len(stats["table_rows"]) > 0
    assert len(stats["top_call_strikes"]) > 0
    assert len(stats["top_put_strikes"]) > 0


@pytest.mark.asyncio
async def test_07_oi_statistics_page_rendering() -> None:
    """7. Verify GET /market-data/derivatives/oi-statistics renders HTTP 200 with HTML."""
    app = create_app()
    dep = _override_market_data_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/market-data/derivatives/oi-statistics?symbol=NIFTY")
            assert resp.status_code == 200
            assert "OI Statistics Dashboard" in resp.text
            assert "Max Pain" in resp.text
            assert "NIFTY" in resp.text
    finally:
        app.dependency_overrides.pop(dep, None)


# ===========================================================================
# 5. OI Change Interval Analytics Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_08_interval_oi_change_scaling_and_baselines() -> None:
    """8. Verify interval analytics across 5m, 15m, 60m and day sessions."""
    svc = IntradayOptionsService(force_mock=True)

    # 15m interval
    oi_15m = await svc.get_interval_oi_change("NIFTY", interval="15m")
    assert oi_15m["interval"] == "15m"
    assert oi_15m["baseline_type"] == "INTRADAY_INTERVAL"
    # G.11-OPT-3: the label used to promise a "Last 15M Window" that was produced by
    # multiplying the day change by mins/375. The interval is now measured between
    # real comparable observations, and the response says which one it is.
    assert "comparable observation pairs" in oi_15m["baseline_label"]
    assert oi_15m["pro_rata_scaling_applied"] is False
    assert len(oi_15m["strikes"]) > 0
    if not oi_15m["measurement_available"]:
        # No stored pair exists: the response must disclose that rather than guess,
        # and the provider day-change figure is kept separately.
        assert oi_15m["disclosure"] is not None
        assert "no proportional scaling" in oi_15m["disclosure"].lower()
        for row in oi_15m["strikes"]:
            assert row["call"]["change_in_oi"] is None
            assert row["call"]["provider_day_change_in_oi"] is not None

    # Day / Session interval
    oi_day = await svc.get_interval_oi_change("NIFTY", interval="session")
    assert oi_day["baseline_type"] == "PREVIOUS_CLOSE"
    assert "Provider day change" in oi_day["baseline_label"]
    assert oi_day["disclosure"] is not None
    assert "not interval measurements" in oi_day["disclosure"]


@pytest.mark.asyncio
async def test_09_oi_change_page_rendering() -> None:
    """9. Verify GET /market-data/derivatives/oi-change renders HTTP 200 with HTML."""
    app = create_app()
    dep = _override_market_data_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/market-data/derivatives/oi-change?symbol=NIFTY&interval=15m")
            assert resp.status_code == 200
            assert "OI Change &amp; Interval Analytics" in resp.text or "OI Change & Interval Analytics" in resp.text
            assert "Buildup Legend" in resp.text
    finally:
        app.dependency_overrides.pop(dep, None)


# ===========================================================================
# 6. Multistrike & Time-Series Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_10_multistrike_oi_time_series() -> None:
    """10. Verify multi-strike open interest time-series generation."""
    svc = IntradayOptionsService(force_mock=True)
    m_oi = await svc.get_multistrike_oi("NIFTY")

    # G.11-OPT-3: this used to assert >= 5 time labels, which the removed
    # ``0.70 + 0.30 * (i / 9)`` ramp always produced. Mock mode persists no
    # observations, so the honest result is an explicit uncovered state.
    if m_oi["series_available"]:
        assert len(m_oi["time_labels"]) >= 2
        assert len(m_oi["strike_series"]) >= 1
    else:
        assert m_oi["time_labels"] == []
        assert m_oi["coverage_message"]
        assert m_oi["provenance"] == "UNAVAILABLE"
    for s in m_oi["strike_series"]:
        # Call and put are separate per-contract series, each with its own
        # availability. Series shorter than the timeline would misalign contracts.
        for side in ("call", "put"):
            payload = s[side]
            assert payload["available"] in (True, False)
            if payload["available"]:
                assert len(payload["oi"]) == len(payload["timestamps"])
                assert len(payload["oi_change_series"]) == len(payload["timestamps"])
            else:
                assert payload["reason"] == "INSUFFICIENT_OBSERVATIONS"
                assert payload["coverage_message"]
        assert s["has_real_series"] == (
            s["call"]["available"] or s["put"]["available"]
        )


@pytest.mark.asyncio
async def test_11_options_oi_vs_time_series() -> None:
    """11. Verify cumulative Options OI vs Time series generation."""
    svc = IntradayOptionsService(force_mock=True)
    time_data = await svc.get_options_oi_vs_time("NIFTY")

    # G.11-OPT-3: the previous assertion depended on a synthetic 0.65 -> 1.00 OI
    # ramp and a fabricated +/-0.6% spot drift. Neither exists any more.
    assert len(time_data["call_oi_series"]) == len(time_data["time_labels"])
    assert len(time_data["put_oi_series"]) == len(time_data["time_labels"])
    assert len(time_data["spot_series"]) == len(time_data["time_labels"])
    if not time_data["time_labels"]:
        assert time_data["provenance"] == "UNAVAILABLE"
        assert time_data["coverage_message"]
        assert time_data["aggregate_available"] is False
    assert "never blended" in time_data["aggregate_scope_note"]


@pytest.mark.asyncio
async def test_12_futures_oi_vs_time_disclosure() -> None:
    """12. Verify futures OI progression and transparent provider entitlement disclosure."""
    svc = IntradayOptionsService(force_mock=True)
    fut = await svc.get_futures_oi_vs_time("NIFTY")

    # G.11-OPT-3: ``NIFTY-FUT`` is not a traded instrument. Real futures price,
    # volume and OI ARE entitled, so the response either carries the provider's own
    # trading symbol or states that no real futures observation exists. A synthetic
    # cost-of-carry price is never substituted for a traded one.
    assert fut["synthetic_substitution"] is False
    assert "provider_entitlement" in fut
    if fut["contract_symbol"] is not None:
        # The provider's own traded symbol, never an invented ``NIFTY-FUT``.
        assert fut["contract_symbol"] != "NIFTY-FUT"
        assert "FUT" in fut["contract_symbol"]
        assert fut["instrument_key"]
        assert fut["provider_entitlement"] == "REAL_PROVIDER_FUTURES_FEED"
        assert "No synthetic cost-of-carry price" in fut["entitlement_statement"]
    else:
        # No real contract could be resolved: say so, and substitute nothing.
        assert fut["reason"]
        assert fut["limitations"]
        assert fut["ledger"] is None
        assert "synthetic carry price is substituted" in fut["limitations"][0]


# ===========================================================================
# 7. Historical Replay & Route Preservation Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_13_replay_timeline_and_point_in_time_safety() -> None:
    """13. Verify replay timeline steps and PIT isolation."""
    svc = IntradayOptionsService(force_mock=True)
    timeline = await svc.get_session_replay_timeline("NIFTY")

    # G.11-OPT-3: 25 invented 15-minute steps used to be returned with
    # ``has_snapshot: True`` on every one, which made replay pure lookahead. Only
    # real persisted observation instants are reported now; mock mode holds none.
    assert timeline["replay_available"] is False
    assert timeline["timeline_steps"] == []
    assert timeline["real_step_count"] == 0
    assert timeline["timeline_source"] == "UNAVAILABLE"
    assert "cannot be reconstructed" in timeline["coverage_message"]
    assert "available_at" in timeline["no_lookahead_statement"]


@pytest.mark.asyncio
async def test_14_derivatives_navigation_and_route_preservation() -> None:
    """14. Verify all new routes and preserved baseline routes return HTTP 200."""
    app = create_app()
    dep = _override_market_data_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Preserved live options workspace
            resp = await client.get("/market-data/options?symbol=NIFTY")
            assert resp.status_code == 200
            assert "Options / Intraday Workbench" in resp.text

            # Preserved historical derivatives workbench
            resp = await client.get("/market-data/derivatives")
            assert resp.status_code == 200
            assert "Derivatives" in resp.text

            # New derivatives suite views
            routes = [
                "/market-data/derivatives/option-chain?symbol=NIFTY",
                "/market-data/derivatives/oi-statistics?symbol=NIFTY",
                "/market-data/derivatives/oi-change?symbol=NIFTY",
                "/market-data/derivatives/multistrike-oi?symbol=NIFTY",
                "/market-data/derivatives/options-oi-time?symbol=NIFTY",
                "/market-data/derivatives/futures-oi-time?symbol=NIFTY",
                "/market-data/derivatives/oi-replay?symbol=NIFTY",
            ]
            for route in routes:
                r = await client.get(route)
                assert r.status_code == 200, f"Route {route} failed with status {r.status_code}"
    finally:
        app.dependency_overrides.pop(dep, None)
