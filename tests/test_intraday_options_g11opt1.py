"""Unit & Integration Tests for Milestone G.11-OPT-1 Phase 1.

Covers:
  - Contract & dynamic expiry resolution (non-hardcoded, sorted, expired exclusion)
  - Data normalization (CE/PE strike ladder, spot price, ATM, ITM/OTM)
  - PCR calculation and zero/missing denominator safety
  - All 7 REST API endpoints (/underlyings, /expiries, /contracts, /chain, /dashboard, /pcr, /oi-comparison)
  - Authentication and permission enforcement
  - HTML views & HTMX partials (/market-data/options, /chain-table, /dashboard-cards, /pcr-view, /oi-chart)
  - Preservation of historical derivatives workbench (/market-data/derivatives)
"""
from datetime import date

import pytest

from app.services.intraday_options_service import IntradayOptionsService
from app.services.option_chain_adapter import MockOptionChainAdapter
from tests.auth_helpers import auth_client, seed_builtins

# ===========================================================================
# 1. Contract & Expiry Resolution Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_supported_underlyings():
    """Verify supported Indian index underlyings match specifications."""
    svc = IntradayOptionsService(force_mock=True)
    underlyings = await svc.get_supported_underlyings()
    symbols = {u["symbol"] for u in underlyings}
    assert "NIFTY" in symbols
    assert "BANKNIFTY" in symbols
    assert "FINNIFTY" in symbols
    assert "MIDCPNIFTY" in symbols

    # Check strike step configuration
    nifty_spec = next(u for u in underlyings if u["symbol"] == "NIFTY")
    assert nifty_spec["strike_step"] == 50
    banknifty_spec = next(u for u in underlyings if u["symbol"] == "BANKNIFTY")
    assert banknifty_spec["strike_step"] == 100


@pytest.mark.asyncio
async def test_dynamic_expiry_resolution():
    """Verify dynamic expiry resolution without hardcoded calendar dates."""
    mock_adapter = MockOptionChainAdapter()
    svc = IntradayOptionsService(adapter=mock_adapter)

    expiries = await svc.get_available_expiries("NIFTY")
    assert len(expiries) > 0

    # Ensure chronologically sorted
    dates = [date.fromisoformat(e["expiry_date"]) for e in expiries]
    assert dates == sorted(dates)

    # Ensure no expired dates (must be >= today)
    today = date.today()
    for d in dates:
        assert d >= today

    # Verify metadata fields
    first_exp = expiries[0]
    assert first_exp["underlying"] == "NIFTY"
    assert "days_to_expiry" in first_exp
    assert "expiry_type" in first_exp
    assert first_exp["is_nearest"] is True


@pytest.mark.asyncio
async def test_unsupported_underlying_rejected():
    """Verify invalid underlying symbol raises explicit ValueError."""
    svc = IntradayOptionsService(force_mock=True)
    with pytest.raises(ValueError, match="Unsupported underlying"):
        await svc.get_available_expiries("UNKNOWN_INDEX")


# ===========================================================================
# 2. Data Normalization & Ladder Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_option_chain_ladder_structure():
    """Verify strike ladder structure, ATM strike, ITM/OTM, and PCR calculation."""
    svc = IntradayOptionsService(force_mock=True)
    ladder_data = await svc.get_option_chain_ladder("NIFTY", strike_count=5)

    assert ladder_data["underlying"] == "NIFTY"
    assert ladder_data["spot_price"] > 0
    assert ladder_data["atm_strike"] > 0
    assert len(ladder_data["ladder"]) > 0

    # Verify ATM strike is centered and marked
    atm_rows = [r for r in ladder_data["ladder"] if r["is_atm"]]
    assert len(atm_rows) == 1
    assert atm_rows[0]["strike_price"] == ladder_data["atm_strike"]

    # Verify moneyness flags
    spot = ladder_data["spot_price"]
    for row in ladder_data["ladder"]:
        s = row["strike_price"]
        if s < spot:
            assert row["call"]["is_itm"] is True
            assert row["put"]["is_itm"] is False
        elif s > spot:
            assert row["call"]["is_itm"] is False
            assert row["put"]["is_itm"] is True


@pytest.mark.asyncio
async def test_pcr_zero_denominator_safe():
    """Verify PCR calculation handles zero denominator without crashing or NaN."""
    svc = IntradayOptionsService(force_mock=True)
    pcr_data = await svc.get_live_pcr("NIFTY")

    assert "pcr_oi" in pcr_data
    if pcr_data["total_call_oi"] == 0:
        assert pcr_data["pcr_oi"] is None
    else:
        expected = round(pcr_data["total_put_oi"] / pcr_data["total_call_oi"], 4)
        assert pcr_data["pcr_oi"] == expected


@pytest.mark.asyncio
async def test_oi_comparison_data():
    """Verify strike-by-strike Call vs Put OI comparison bar formatting."""
    svc = IntradayOptionsService(force_mock=True)
    oi_comp = await svc.get_call_vs_put_oi("NIFTY", strike_count=5)

    assert "comparison_bars" in oi_comp
    assert len(oi_comp["comparison_bars"]) > 0
    for bar in oi_comp["comparison_bars"]:
        assert 0 <= bar["call_bar_pct"] <= 100
        assert 0 <= bar["put_bar_pct"] <= 100
        assert "call_oi" in bar
        assert "put_oi" in bar


# ===========================================================================
# 3. REST API Endpoint Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_options_rest_api_endpoints():
    """Verify all 7 options REST API endpoints with authentication."""
    await seed_builtins()
    async with auth_client(is_superuser=True) as client:
        # 1. Underlyings
        r1 = await client.get("/api/v1/market-data/options/underlyings")
        assert r1.status_code == 200
        data1 = r1.json()
        assert len(data1) >= 4

        # 2. Expiries
        r2 = await client.get("/api/v1/market-data/options/expiries?underlying=NIFTY")
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["underlying"] == "NIFTY"
        assert "expiries" in data2

        # 3. Contracts
        r3 = await client.get("/api/v1/market-data/options/contracts?underlying=NIFTY")
        assert r3.status_code == 200
        data3 = r3.json()
        assert data3["underlying"] == "NIFTY"
        assert "strikes" in data3

        # 4. Chain
        r4 = await client.get("/api/v1/market-data/options/chain?underlying=NIFTY&strike_count=5")
        assert r4.status_code == 200
        data4 = r4.json()
        assert data4["underlying"] == "NIFTY"
        assert "ladder" in data4

        # 5. Dashboard
        r5 = await client.get("/api/v1/market-data/options/dashboard?underlying=NIFTY")
        assert r5.status_code == 200
        data5 = r5.json()
        assert "spot_price" in data5
        assert "total_call_oi" in data5
        assert "pcr" in data5

        # 6. PCR
        r6 = await client.get("/api/v1/market-data/options/pcr?underlying=NIFTY")
        assert r6.status_code == 200
        data6 = r6.json()
        assert "pcr_oi" in data6
        assert "sentiment_classification" in data6

        # 7. OI Comparison
        r7 = await client.get("/api/v1/market-data/options/oi-comparison?underlying=NIFTY&strike_count=5")
        assert r7.status_code == 200
        data7 = r7.json()
        assert "comparison_bars" in data7

        # 8. OI Change
        r8 = await client.get("/api/v1/market-data/options/oi-change?underlying=NIFTY&strike_count=5")
        assert r8.status_code == 200
        data8 = r8.json()
        assert "strikes" in data8
        assert "active_filter" in data8
        assert "summary" in data8


@pytest.mark.asyncio
async def test_options_api_unauthenticated_rejected():
    """Verify API endpoints require authentication (401 without credentials)."""
    import httpx

    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/v1/market-data/options/underlyings")
        assert r.status_code == 401


# ===========================================================================
# 4. HTML Views & HTMX Partial Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_options_html_views_and_partials():
    """Verify full-page options workspace and HTMX partial endpoints."""
    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # Full workspace
        r_page = await client.get("/market-data/options")
        assert r_page.status_code == 200
        assert "Options / Intraday Workbench" in r_page.text
        assert "tab-chain" in r_page.text
        assert "tab-dashboard" in r_page.text
        assert "tab-pcr" in r_page.text
        assert "tab-oi" in r_page.text
        assert "tab-oichange" in r_page.text
        assert "toggle-buildup" in r_page.text
        assert "toggle-iv" in r_page.text
        assert "toggle-all-greeks" in r_page.text

        # HTMX Partials - Default Option Chain table
        r_table = await client.get("/market-data/options/chain-table?symbol=NIFTY&strike_count=5")
        assert r_table.status_code == 200
        assert "CALLS (CE)" in r_table.text
        assert "PUTS (PE)" in r_table.text
        assert "Option Chain Summary Panel" in r_table.text
        assert "Total CE OI" in r_table.text
        assert "Call OI Change" in r_table.text
        assert "CE Volume" in r_table.text
        assert "PE Volume" in r_table.text
        assert "PE OI Change" in r_table.text
        assert "Total PE OI" in r_table.text

        # HTMX Partials - Option Chain table with Greeks & Buildup toggled ON
        r_table_greeks = await client.get(
            "/market-data/options/chain-table?symbol=NIFTY&strike_count=5&show_buildup=true&show_iv=true&show_delta=true&show_gamma=true&show_vega=true"
        )
        assert r_table_greeks.status_code == 200
        assert "Delta" in r_table_greeks.text
        assert "Gamma" in r_table_greeks.text
        assert "Vega" in r_table_greeks.text
        assert "Buildup" in r_table_greeks.text
        assert "Quantitative Engine: Greeks and IV" in r_table_greeks.text

        r_dash = await client.get("/market-data/options/dashboard-cards?symbol=NIFTY")
        assert r_dash.status_code == 200
        assert "Spot Price" in r_dash.text
        assert "Live PCR" in r_dash.text

        r_pcr = await client.get("/market-data/options/pcr-view?symbol=NIFTY")
        assert r_pcr.status_code == 200
        assert "Live Put-Call Ratio" in r_pcr.text

        r_oi = await client.get("/market-data/options/oi-chart?symbol=NIFTY&strike_count=5")
        assert r_oi.status_code == 200
        assert "Strike-wise Call vs Put Open Interest" in r_oi.text

        # Tab 5: OI Change View
        r_oichange = await client.get("/market-data/options/oi-change-view?symbol=NIFTY&strike_count=5")
        assert r_oichange.status_code == 200
        assert "Strike-Wise Open Interest Change &amp; Buildup Classification" in r_oichange.text or "Strike-Wise Open Interest Change" in r_oichange.text
        assert "Filter Buildup:" in r_oichange.text
        assert "Buildup Legend:" in r_oichange.text

        r_select = await client.get("/market-data/options/expiries-select?symbol=NIFTY")
        assert r_select.status_code == 200
        assert "<select" in r_select.text

        # Historical Derivatives Workbench must remain intact
        r_hist = await client.get("/market-data/derivatives")
        assert r_hist.status_code == 200
        assert "Derivatives Workbench" in r_hist.text


# ===========================================================================
# 5. Buildup Classification, Greeks & Summary Panel Tests
# ===========================================================================

def test_buildup_classification_quadrants():
    """Verify all 4 buildup quadrants and neutral handling."""
    from app.services.intraday_options_service import classify_buildup

    # Long Buildup: Price ↑, OI ↑
    lb = classify_buildup(12.5, 50000)
    assert lb["classification"] == "LONG_BUILDUP"
    assert lb["code"] == "LB"
    assert lb["color_class"] == "success"

    # Short Buildup: Price ↓, OI ↑
    sb = classify_buildup(-15.0, 75000)
    assert sb["classification"] == "SHORT_BUILDUP"
    assert sb["code"] == "SB"
    assert sb["color_class"] == "danger"

    # Short Covering: Price ↑, OI ↓
    sc = classify_buildup(25.0, -30000)
    assert sc["classification"] == "SHORT_COVERING"
    assert sc["code"] == "SC"
    assert sc["color_class"] == "info"

    # Long Unwinding: Price ↓, OI ↓
    lu = classify_buildup(-8.0, -45000)
    assert lu["classification"] == "LONG_UNWINDING"
    assert lu["code"] == "LU"
    assert lu["color_class"] == "warning"

    # Neutral / Inconclusive
    neutral_zero_price = classify_buildup(0.0, 10000)
    assert neutral_zero_price["classification"] == "NEUTRAL"

    neutral_zero_oi = classify_buildup(10.0, 0)
    assert neutral_zero_oi["classification"] == "NEUTRAL"

    neutral_none = classify_buildup(None, None)
    assert neutral_none["classification"] == "NEUTRAL"


def test_black_scholes_greeks_resolution():
    """Verify Black-Scholes Greeks calculation with analytical consistency."""
    from app.services.intraday_options_service import resolve_contract_greeks

    spot = 24500.0
    strike = 24500.0
    time_to_expiry_years = 14.0 / 365.0  # 14 days
    ltp = 250.0

    call_greeks = resolve_contract_greeks(
        spot=spot,
        strike=strike,
        time_to_expiry_years=time_to_expiry_years,
        option_type="CE",
        ltp=ltp,
    )
    assert call_greeks["status"] == "CONVERGED"
    assert call_greeks["provenance"] == "MODEL-CALCULATED"
    assert call_greeks["iv"] is not None and call_greeks["iv"] > 0
    assert 0.0 < call_greeks["delta"] < 1.0
    assert call_greeks["gamma"] > 0.0
    assert call_greeks["vega"] > 0.0

    put_greeks = resolve_contract_greeks(
        spot=spot,
        strike=strike,
        time_to_expiry_years=time_to_expiry_years,
        option_type="PE",
        ltp=ltp,
    )
    assert put_greeks["status"] == "CONVERGED"
    assert -1.0 < put_greeks["delta"] < 0.0

    # Provider supplied preservation
    prov_greeks = resolve_contract_greeks(
        spot=spot,
        strike=strike,
        time_to_expiry_years=time_to_expiry_years,
        option_type="CE",
        ltp=ltp,
        source_greeks={"iv": 0.15, "delta": 0.52, "gamma": 0.002, "vega": 15.0, "origin": "UPSTOX-LIVE"},
    )
    assert prov_greeks["provenance"] == "UPSTOX-LIVE"
    assert prov_greeks["delta"] == 0.52

    # Zero DTE handling
    zero_dte = resolve_contract_greeks(
        spot=spot,
        strike=strike,
        time_to_expiry_years=0.0,
        option_type="CE",
        ltp=ltp,
    )
    assert zero_dte["status"] == "ZERO_DTE"
    assert zero_dte["delta"] == 0.5


@pytest.mark.asyncio
async def test_six_metric_summary_panel_coverage():
    """Verify 6-metric summary panel aggregates properly and labels strike coverage."""
    svc = IntradayOptionsService(force_mock=True)

    # Windowed coverage (ATM ± 5)
    ladder_win = await svc.get_option_chain_ladder("NIFTY", strike_count=5)
    summary_win = ladder_win["summary"]
    assert "ATM ± 5 Strikes" in summary_win["strike_coverage_label"]
    assert summary_win["is_full_coverage"] is False
    assert summary_win["total_ce_oi"] > 0
    assert summary_win["total_pe_oi"] > 0
    assert summary_win["ce_volume"] > 0
    assert summary_win["pe_volume"] > 0
    assert "call_oi_change" in summary_win
    assert "pe_oi_change" in summary_win

    # Full coverage
    ladder_full = await svc.get_option_chain_ladder("NIFTY", strike_count=None)
    summary_full = ladder_full["summary"]
    assert "Full Expiry" in summary_full["strike_coverage_label"]
    assert summary_full["is_full_coverage"] is True


@pytest.mark.asyncio
async def test_oi_change_analysis_and_filtering():
    """Verify strike-wise OI change analysis, bar percentages, and buildup filtering."""
    svc = IntradayOptionsService(force_mock=True)

    # All buidlups
    all_data = await svc.get_oi_change_analysis("NIFTY", strike_count=10, buildup_filter="ALL")
    assert len(all_data["strikes"]) > 0
    assert all_data["max_abs_oi_change"] > 0
    for s in all_data["strikes"]:
        assert 0.0 <= s["call"]["bar_pct"] <= 100.0
        assert 0.0 <= s["put"]["bar_pct"] <= 100.0
        assert s["call"]["buildup"]["classification"] in (
            "LONG_BUILDUP", "SHORT_BUILDUP", "SHORT_COVERING", "LONG_UNWINDING", "NEUTRAL"
        )

    # Filtered by LONG_BUILDUP
    lb_data = await svc.get_oi_change_analysis("NIFTY", strike_count=10, buildup_filter="LONG_BUILDUP")
    assert lb_data["active_filter"] == "LONG_BUILDUP"
    for s in lb_data["strikes"]:
        # At least one side must be LONG_BUILDUP
        c_lb = s["call"]["buildup"]["classification"] == "LONG_BUILDUP"
        p_lb = s["put"]["buildup"]["classification"] == "LONG_BUILDUP"
        assert c_lb or p_lb
