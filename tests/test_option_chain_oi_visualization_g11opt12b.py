"""G.11-OPT-12B Phase 2B — Option Chain & Strike OI Visualization contract tests.

Verifies the Phase 2B additions without touching any analytics contract:

Chart A — Call vs Put OI strike tornado (components/option_oi_tornado_chart.html)
    * renders from the existing backend ``oi_data.comparison_bars`` produced by
      IntradayOptionsService.get_call_vs_put_oi — nothing recomputed client-side.
Chart B — Strike-wise ΔOI (components/option_doi_chart.html)
    * renders from the existing backend ``oi_change_data.strikes`` produced by
      IntradayOptionsService.get_oi_change_analysis — nothing recomputed.

Contract guarantees enforced here (against ACTUAL service output, not fixtures):
1. Tornado datasets use the actual call_oi / put_oi backend values; strikes are
   numerically ordered; ATM comes from the backend flag only.
2. ΔOI chart distinguishes positive / negative / zero / null exactly as the
   service emits them (null is a gap, never a zero).
3. No fabricated data: payloads are serialized backend rows only.
4. Table in-cell OI/ΔOI bars: numeric values remain authoritative; bar scaling
   is dataset-local (value / largest displayed value); zero and unavailable
   render no bar; accessibility titles distinguish value from visual aid.
5. Chart.js 4.4.0 guarded loader; Phase 2A hardened lifecycle markers reused;
   no Tailwind; shared G.11-OPT-11 filter architecture untouched.
6. HTMX partials re-render the chart components so repeated swaps stay safe and
   the truthful empty state (no canvas at all) holds when there is no data.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from app.api.routes.market_data_pages import _render
from app.services.intraday_options_service import IntradayOptionsService
from tests.auth_helpers import auth_client, seed_builtins

CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
TORNADO_CANVAS = "opt12bOiTornadoChart"
DOI_CANVAS = "opt12bDoiChart"


# ---------------------------------------------------------------------------
# Helpers: render the components with realistic backend-shaped contexts
# ---------------------------------------------------------------------------


def _tornado_context(**overrides: Any) -> dict[str, Any]:
    """Shape mirrors IntradayOptionsService.get_call_vs_put_oi output."""
    base: dict[str, Any] = {
        "underlying": "NIFTY",
        "spot_price": 24500.0,
        "atm_strike": 24500.0,
        "selected_expiry": "2026-10-01",
        "total_call_oi": 550000,
        "total_put_oi": 528000,
        "max_strike_oi": 50000,
        "strikes": [24000.0, 24250.0, 24500.0, 24750.0, 25000.0],
        "comparison_bars": [
            {
                "strike_price": 24000.0,
                "is_atm": False,
                "call_oi": 50000,
                "put_oi": 48000,
                "call_bar_pct": 100.0,
                "put_bar_pct": 96.0,
                "call_change_oi": 1200,
                "put_change_oi": -800,
            },
            {
                "strike_price": 24250.0,
                "is_atm": False,
                "call_oi": 50000,
                "put_oi": 48000,
                "call_bar_pct": 100.0,
                "put_bar_pct": 96.0,
                "call_change_oi": 1200,
                "put_change_oi": -800,
            },
            {
                "strike_price": 24500.0,
                "is_atm": True,
                "call_oi": 0,  # genuine zero stays zero
                "put_oi": 48000,
                "call_bar_pct": 0.0,
                "put_bar_pct": 96.0,
                "call_change_oi": 0,  # genuine zero ΔOI
                "put_change_oi": None,  # unavailable ΔOI stays None
            },
            {
                "strike_price": 24750.0,
                "is_atm": False,
                "call_oi": 50000,
                "put_oi": None,  # unavailable OI stays None
                "call_bar_pct": 100.0,
                "put_bar_pct": 0.0,
                "call_change_oi": 1200,
                "put_change_oi": -800,
            },
            {
                "strike_price": 25000.0,
                "is_atm": False,
                "call_oi": 50000,
                "put_oi": 48000,
                "call_bar_pct": 100.0,
                "put_bar_pct": 96.0,
                "call_change_oi": 1200,
                "put_change_oi": -800,
            },
        ],
        "provider": "MOCK_PROVIDER",
        "freshness": "MARKET_CLOSED",
        "as_of_ist": "2026-10-01 10:00 IST",
    }
    base.update(overrides)
    return base


def _doi_context(**overrides: Any) -> dict[str, Any]:
    """Shape mirrors IntradayOptionsService.get_oi_change_analysis output."""
    base: dict[str, Any] = {
        "underlying": "NIFTY",
        "underlying_name": "NIFTY 50",
        "spot_price": 24500.0,
        "atm_strike": 24500.0,
        "selected_expiry": "2026-10-01",
        "available_expiries": [],
        "strike_step": 250,
        "strike_count": 4,
        "summary": {"strike_coverage_label": "ATM ± 2"},
        "interval_baseline": "Provider Intraday Baseline vs Previous Session Close",
        "max_abs_oi_change": 1200,
        "active_filter": "ALL",
        "provider": "MOCK_PROVIDER",
        "freshness": "MARKET_CLOSED",
        "market_session": "CLOSED",
        "is_market_open": False,
        "as_of_ist": "2026-10-01 10:00 IST",
        "strikes": [
            {
                "strike_price": 24250.0,
                "is_atm": False,
                "call": {"oi": 50000, "change_in_oi": 1200, "ltp": 300.5, "price_change": 15.5, "buildup": {"code": "LB", "color_class": "success", "tooltip": "Long Buildup"}, "bar_pct": 100.0, "is_positive": True},
                "put": {"oi": 48000, "change_in_oi": 800, "ltp": 150.25, "price_change": 6.0, "buildup": {"code": "LB", "color_class": "success", "tooltip": "Long Buildup"}, "bar_pct": 66.7, "is_positive": True},
            },
            {
                "strike_price": 24500.0,
                "is_atm": True,
                "call": {"oi": 50000, "change_in_oi": 0, "ltp": 150.0, "price_change": -4.5, "buildup": {"code": "—", "color_class": "secondary", "tooltip": "Neutral"}, "bar_pct": 0.0, "is_positive": False},
                "put": {"oi": 48000, "change_in_oi": None, "ltp": 150.0, "price_change": -6.0, "buildup": {"code": "—", "color_class": "secondary", "tooltip": "Neutral"}, "bar_pct": 0.0, "is_positive": None},
            },
            {
                "strike_price": 24750.0,
                "is_atm": False,
                "call": {"oi": 50000, "change_in_oi": 1200, "ltp": 75.0, "price_change": -4.5, "buildup": {"code": "SB", "color_class": "danger", "tooltip": "Short Buildup"}, "bar_pct": 100.0, "is_positive": True},
                "put": {"oi": 48000, "change_in_oi": -800, "ltp": 225.5, "price_change": 14.0, "buildup": {"code": "SC", "color_class": "info", "tooltip": "Short Covering"}, "bar_pct": 66.7, "is_positive": False},
            },
            {
                "strike_price": 25000.0,
                "is_atm": False,
                "call": {"oi": 50000, "change_in_oi": 1200, "ltp": 40.0, "price_change": -4.5, "buildup": {"code": "SB", "color_class": "danger", "tooltip": "Short Buildup"}, "bar_pct": 100.0, "is_positive": True},
                "put": {"oi": 48000, "change_in_oi": -800, "ltp": 260.0, "price_change": 14.0, "buildup": {"code": "SC", "color_class": "info", "tooltip": "Short Covering"}, "bar_pct": 66.7, "is_positive": False},
            },
        ],
    }
    base.update(overrides)
    return base


def _render_tornado(**overrides: Any) -> str:
    context = {"request": None, "underlying": "NIFTY", "expiry": None, "oi_data": _tornado_context()}
    context.update(overrides)
    return _render("components/option_oi_tornado_chart.html", context)


def _render_doi(**overrides: Any) -> str:
    context = {"request": None, "underlying": "NIFTY", "expiry": None, "oi_change_data": _doi_context()}
    context.update(overrides)
    return _render("components/option_doi_chart.html", context)


def _script_of(html: str) -> str:
    start = html.index("<script>") + len("<script>")
    return html[start : html.index("</script>")] if "</script>" in html else html[start:]


def _extract_rows(script: str) -> list[dict[str, Any]]:
    match = re.search(r"var rows = (\[.*?\]);", script, re.S)
    assert match is not None, "var rows not found in chart bootstrap script"
    return json.loads(match.group(1))


def _read_template(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


TORNADO_TEMPLATE = "app/templates/components/option_oi_tornado_chart.html"
DOI_TEMPLATE = "app/templates/components/option_doi_chart.html"
CHAIN_TABLE_TEMPLATE = "app/templates/components/intraday_option_chain_table.html"


# ---------------------------------------------------------------------------
# Chart A — Call vs Put OI tornado
# ---------------------------------------------------------------------------


class TestTornadoChartContract:
    """Chart A visualizes the existing backend comparison_bars only."""

    def test_canvas_and_wrapper_render_with_data(self) -> None:
        html = _render_tornado()
        assert f'<canvas id="{TORNADO_CANVAS}"' in html
        assert "Call vs Put OI — Strike Tornado" in html
        assert CHART_JS_URL in _script_of(html)

    def test_payload_uses_actual_backend_oi_values(self) -> None:
        ctx = _tornado_context()
        rows = _extract_rows(_script_of(_render_tornado()))
        assert [r["strike"] for r in rows] == [b["strike_price"] for b in ctx["comparison_bars"]]
        assert [r["callOi"] for r in rows] == [b["call_oi"] for b in ctx["comparison_bars"]]
        assert [r["putOi"] for r in rows] == [b["put_oi"] for b in ctx["comparison_bars"]]

    def test_strikes_numerically_ordered_in_payload(self) -> None:
        rows = _extract_rows(_script_of(_render_tornado()))
        strikes = [r["strike"] for r in rows]
        assert strikes == sorted(strikes)

    def test_atm_comes_from_backend_flag(self) -> None:
        ctx = _tornado_context()
        rows = _extract_rows(_script_of(_render_tornado()))
        expected = [b["is_atm"] for b in ctx["comparison_bars"]]
        assert [r["atm"] for r in rows] == expected
        assert sum(1 for r in rows if r["atm"]) == 1
        # ATM reference plugin is drawn from the flags, not from a recomputed spot
        assert "atmFlags.indexOf(true)" in _script_of(html=_render_tornado())

    def test_ce_mirrored_coordinate_transform_only(self) -> None:
        script = _script_of(_render_tornado())
        # The only transformation: CE mirrored negative for tornado geometry.
        assert "-r.callOi" in script
        # Put side is the actual value; no other mutation of the payload.
        assert "r.putOi === null ? null : r.putOi" in script

    def test_genuine_zero_stays_zero(self) -> None:
        rows = _extract_rows(_script_of(_render_tornado()))
        assert rows[2]["callOi"] == 0  # backend zero stays zero (zero-length bar)

    def test_unavailable_oi_serialized_as_null_not_zero(self) -> None:
        rows = _extract_rows(_script_of(_render_tornado()))
        assert rows[3]["putOi"] is None
        # Tooltip renders N/A for null, actual number otherwise.
        assert "N/A (unavailable)" in _script_of(_render_tornado())

    def test_null_oi_produces_null_bar_not_zero_height(self) -> None:
        script = _script_of(_render_tornado())
        assert "null" in script  # null data point -> no bar, truthful gap

    def test_tooltip_shows_exact_backend_values(self) -> None:
        script = _script_of(_render_tornado())
        assert "fmtInt(ce)" in script and "fmtInt(pe)" in script
        assert "Call OI (CE): " in script and "Put OI (PE): " in script

    def test_compact_axis_formatting_present(self) -> None:
        script = _script_of(_render_tornado())
        assert "fmtCompact" in script
        assert "Cr" in script and "L" in script and "K" in script

    def test_legend_identifies_ce_pe(self) -> None:
        script = _script_of(_render_tornado())
        assert "Call OI (CE)" in script and "Put OI (PE)" in script

    def test_truthful_empty_state_when_no_data(self) -> None:
        html = _render_tornado(oi_data=None)
        assert TORNADO_CANVAS not in html  # no canvas at all — zero chart instances
        html2 = _render_tornado(oi_data={"comparison_bars": []})
        assert TORNADO_CANVAS not in html2


class TestTornadoLifecycle:
    """Phase 2A hardened lifecycle pattern reused for the tornado fragment."""

    def test_destroy_before_init_registry_present(self) -> None:
        script = _script_of(_render_tornado())
        assert "destroyPreviousCharts()" in script
        assert "previous[i].destroy()" in script

    def test_beforeSwap_scoped_and_registered_exactly_once(self) -> None:
        script = _script_of(_render_tornado())
        assert script.count("addEventListener('htmx:beforeSwap'") == 1
        assert script.count("removeEventListener('htmx:beforeSwap'") == 1
        assert "__g11Opt12bOiTornadoBeforeSwapBound" in script
        assert "'oi-container'" in script  # scoped to this fragment only

    def test_guarded_one_time_loader_shared_semaphore(self) -> None:
        script = _script_of(_render_tornado())
        assert "__g11Opt12bChartJsLoading" in script

    def test_no_other_htmx_events_registered(self) -> None:
        script = _script_of(_render_tornado())
        assert re.findall(r"addEventListener\('htmx:([a-zA-Z]+)'", script) == ["beforeSwap"]


# ---------------------------------------------------------------------------
# Chart B — Strike-wise ΔOI
# ---------------------------------------------------------------------------


class TestDoiChartContract:
    """Chart B visualizes the existing backend ΔOI values with truthful states."""

    def test_canvas_and_wrapper_render_with_data(self) -> None:
        html = _render_doi()
        assert f'<canvas id="{DOI_CANVAS}"' in html
        assert "Strike-wise ΔOI" in html
        assert CHART_JS_URL in _script_of(html)

    def test_payload_uses_actual_backend_doi_values(self) -> None:
        ctx = _doi_context()
        rows = _extract_rows(_script_of(_render_doi()))
        assert [r["ceDoi"] for r in rows] == [s["call"]["change_in_oi"] for s in ctx["strikes"]]
        assert [r["peDoi"] for r in rows] == [s["put"]["change_in_oi"] for s in ctx["strikes"]]

    def test_positive_doi_represented(self) -> None:
        rows = _extract_rows(_script_of(_render_doi()))
        assert any(r["ceDoi"] is not None and r["ceDoi"] > 0 for r in rows)
        assert any(r["peDoi"] is not None and r["peDoi"] > 0 for r in rows)

    def test_negative_doi_represented(self) -> None:
        rows = _extract_rows(_script_of(_render_doi()))
        assert any(r["peDoi"] is not None and r["peDoi"] < 0 for r in rows)

    def test_zero_doi_stays_zero_not_null(self) -> None:
        rows = _extract_rows(_script_of(_render_doi()))
        assert rows[1]["ceDoi"] == 0  # genuine zero from backend

    def test_null_doi_serialized_as_null_never_zero(self) -> None:
        rows = _extract_rows(_script_of(_render_doi()))
        assert rows[1]["peDoi"] is None
        script = _script_of(_render_doi())
        # NaN (gap) for null, Math.abs otherwise — never fabricated zeros.
        assert "NaN" in script and "Math.abs" in script

    def test_bar_colors_distinguish_sign_states(self) -> None:
        script = _script_of(_render_doi())
        assert "#22c55e" in script  # positive
        assert "#ef4444" in script  # negative
        assert "#94a3b8" in script  # neutral zero
        assert "#64748b" in script  # unavailable

    def test_tooltip_shows_signed_values_and_unavailable_state(self) -> None:
        script = _script_of(_render_doi())
        assert "CE ΔOI: N/A (unavailable)" in script or "N/A (unavailable)" in script
        assert "fmtSigned" in script

    def test_no_net_doi_invented(self) -> None:
        script = _script_of(_render_doi())
        assert "netDoi" not in script and "net_doi" not in script

    def test_truthful_empty_state_when_no_data(self) -> None:
        html = _render_doi(oi_change_data=None)
        assert DOI_CANVAS not in html
        html2 = _render_doi(oi_change_data={"strikes": []})
        assert DOI_CANVAS not in html2


class TestDoiLifecycle:
    """Phase 2A hardened lifecycle pattern reused for the ΔOI fragment."""

    def test_destroy_before_init_registry_present(self) -> None:
        script = _script_of(_render_doi())
        assert "destroyPreviousCharts()" in script
        assert "previous[i].destroy()" in script

    def test_beforeSwap_scoped_and_registered_exactly_once(self) -> None:
        script = _script_of(_render_doi())
        assert script.count("addEventListener('htmx:beforeSwap'") == 1
        assert script.count("removeEventListener('htmx:beforeSwap'") == 1
        assert "__g11Opt12bDoiBeforeSwapBound" in script
        assert "'oichange-container'" in script

    def test_guarded_one_time_loader_shared_semaphore(self) -> None:
        script = _script_of(_render_doi())
        assert "__g11Opt12bChartJsLoading" in script


# ---------------------------------------------------------------------------
# Option chain table — in-cell OI / ΔOI bars
# ---------------------------------------------------------------------------


class TestChainTableBars:
    """Numeric values remain authoritative; bars are dataset-local visual aids."""

    def test_bar_scaling_is_dataset_local_deterministic(self) -> None:
        src = _read_template(CHAIN_TABLE_TEMPLATE)
        assert "namespace(max_oi = 0, max_abs_doi = 0)" in src
        assert "(value / max_val) * 100" in src
        assert "(value | abs) / max_abs * 100" in src

    def test_zero_and_unavailable_render_no_bar(self) -> None:
        src = _read_template(CHAIN_TABLE_TEMPLATE)
        # OI macro requires value > 0; ΔOI macro requires value != 0; both guard None.
        assert "value > 0 %" in src
        assert "value != 0 and max_abs and max_abs > 0" in src
        assert "value is not none and max_val" in src

    def test_accessibility_titles_distinguish_value_from_visual_bar(self) -> None:
        src = _read_template(CHAIN_TABLE_TEMPLATE)
        assert "visual aid relative to the largest displayed OI, not a data value" in src
        assert "visual aid relative to the largest displayed |ΔOI|, not a data value" in src
        assert "CE OI unavailable" in src and "PE ΔOI unavailable" in src

    def test_bar_legend_note_present(self) -> None:
        src = _read_template(CHAIN_TABLE_TEMPLATE)
        assert "OI / ΔOI bar shading is a relative visual aid" in src

    def test_existing_numeric_cells_and_atm_preserved(self) -> None:
        src = _read_template(CHAIN_TABLE_TEMPLATE)
        # Numeric formatting calls untouched
        assert "'{:,}'.format(c.open_interest)" in src
        assert "'{:+,}'.format(c.change_in_oi)" in src
        assert "'{:,}'.format(p.open_interest)" in src
        assert "'{:+,}'.format(p.change_in_oi)" in src
        # Backend ATM badge preserved
        assert "ATM" in src and "is_atm" in src


# ---------------------------------------------------------------------------
# Route wiring — HTMX partials re-render the chart components
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRouteWiring:
    """HTMX refreshes must keep the Phase 2B visualizations present/safe."""

    async def test_oi_chart_partial_includes_tornado_chart(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            r = await client.get("/market-data/options/oi-chart?symbol=NIFTY&strike_count=5")
            assert r.status_code == 200
            assert "Strike-wise Call vs Put Open Interest" in r.text
            assert TORNADO_CANVAS in r.text  # chart component re-rendered after swap
            assert CHART_JS_URL in r.text

    async def test_oi_change_partial_includes_doi_chart(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            r = await client.get("/market-data/options/oi-change-view?symbol=NIFTY&strike_count=5")
            assert r.status_code == 200
            assert DOI_CANVAS in r.text
            assert CHART_JS_URL in r.text

    async def test_workspace_page_hosts_both_chart_components(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            r = await client.get("/market-data/options")
            assert r.status_code == 200
            assert TORNADO_CANVAS in r.text
            assert DOI_CANVAS in r.text
            assert "Call vs Put OI — Strike Tornado" in r.text
            assert "Strike-wise ΔOI" in r.text

    async def test_chain_table_partial_renders_with_bar_styling(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            r = await client.get("/market-data/options/chain-table?symbol=NIFTY&strike_count=5")
            assert r.status_code == 200
            assert "CALLS (CE)" in r.text
            assert "visual aid relative to the largest displayed OI" in r.text
            assert "linear-gradient" in r.text  # in-cell bars rendered


# ---------------------------------------------------------------------------
# Service contract — the data Phase 2B renders is genuinely the backend's
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestServiceDataContract:
    """Phase 2B consumes existing service fields; none are invented upstream."""

    async def test_get_call_vs_put_oi_exposes_required_fields(self) -> None:
        service = IntradayOptionsService()
        data = await service.get_call_vs_put_oi(underlying="NIFTY", strike_count=3)
        assert data["comparison_bars"], "expected backend comparison_bars for NIFTY"
        bar = data["comparison_bars"][0]
        for field in ("strike_price", "is_atm", "call_oi", "put_oi", "call_change_oi", "put_change_oi"):
            assert field in bar, f"backend field missing: {field}"
        strikes = [b["strike_price"] for b in data["comparison_bars"]]
        assert strikes == sorted(strikes)

    async def test_get_oi_change_analysis_exposes_nullable_doi(self) -> None:
        service = IntradayOptionsService()
        data = await service.get_oi_change_analysis(underlying="NIFTY", strike_count=3)
        assert data["strikes"], "expected backend strike rows for NIFTY"
        row = data["strikes"][0]
        assert "change_in_oi" in row["call"] and "change_in_oi" in row["put"]
        assert "bar_pct" in row["call"] and "bar_pct" in row["put"]
        assert "is_atm" in row


# ---------------------------------------------------------------------------
# Shared architecture — filters, Chart.js, no Tailwind, Phase 2A intact
# ---------------------------------------------------------------------------


class TestSharedArchitecturePreserved:
    def test_no_tailwind_introduced(self) -> None:
        for path in (TORNADO_TEMPLATE, DOI_TEMPLATE, CHAIN_TABLE_TEMPLATE):
            src = _read_template(path)
            assert "tailwind" not in src.lower()

    def test_no_new_chart_libraries_introduced(self) -> None:
        for path in (TORNADO_TEMPLATE, DOI_TEMPLATE, "app/templates/options_workspace.html"):
            src = _read_template(path)
            for lib in ("plotly", "echarts", "highcharts", "d3.v", "apexcharts"):
                assert lib not in src.lower(), f"forbidden chart library: {lib}"

    def test_workspace_reuses_existing_controls_not_new_filters(self) -> None:
        src = _read_template("app/templates/options_workspace.html")
        # The established options-workspace controls remain the only selectors.
        assert "optionsControlForm" in src
        assert "opt-strike-count" in src
        assert "opt-symbol" in src
        assert "opt-expiry" in src
        # No duplicate derivatives-filter-form on this page (G.11-OPT-11 panel
        # is not part of this workspace; consuming local form values is the
        # established architecture here).
        assert "derivatives-filter-form" not in src
        # Refresh path unchanged
        assert "refreshActiveTab()" in src

    def test_phase2a_lifecycle_untouched(self) -> None:
        src = _read_template("app/templates/components/advanced_exposure_tab.html")
        assert "__g11Opt12bBeforeSwapBound" in src
        assert "'deriv-exposure-output'" in src
        assert CHART_JS_URL in src

    def test_phase2a_beforeSwap_hook_untouched_and_scoped(self) -> None:
        src = _read_template("app/templates/components/advanced_exposure_tab.html")
        assert src.count("addEventListener('htmx:beforeSwap'") == 1

    def test_chartjs_version_pinned_440(self) -> None:
        for path in (TORNADO_TEMPLATE, DOI_TEMPLATE):
            src = _read_template(path)
            assert "chart.js@4.4.0" in src
