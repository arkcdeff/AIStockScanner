"""G.11-OPT-12B Phase 2A — Advanced Exposure Visualization contract tests.

Verifies the two new Chart.js 4.4.0 charts added to the Advanced Exposure HTMX
partial (components/advanced_exposure_tab.html) without touching analytics:

Chart A — Spot Scenario Gamma Profile
    * renders from the existing backend ``gamma_profile`` context
      (GammaProfilePoint rows), never recomputed client-side.
Chart B — Strike-wise Net GEX Profile
    * renders from the existing backend ``strikes`` context
      (StrikeExposureRow rows), never recomputed client-side.

Contract guarantees enforced here:
1. Chart containers/canvases exist only when the corresponding backend data
   exists; missing data renders a truthful empty state instead of a fake chart.
2. A genuine zero stays zero and an unavailable value is serialized as null.
3. Gamma Flip null never breaks rendering (marker omitted, status shown).
4. Existing KPI cards, table, disclaimer and filter architecture remain intact.
5. HTMX lifecycle safety markers are present (destroy-before-init registry,
   guarded one-time Chart.js loader, no new global htmx handlers).
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.api.routes.market_data_pages import _render
from app.schemas.advanced_exposure import (
    AdvancedExposureSnapshot,
    GammaConcentration,
    GammaFlipResult,
    GammaProfilePoint,
    StrikeExposureRow,
)
from tests.auth_helpers import auth_client, seed_builtins

CHART_A_ID = "advExposureScenarioGammaChart"
CHART_B_ID = "advExposureStrikeGexChart"
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"


def _sample_snapshot(**overrides: Any) -> AdvancedExposureSnapshot:
    base: dict[str, Any] = {
        "symbol": "NIFTY",
        "trade_date": date(2026, 9, 24),
        "as_of_date": date(2026, 9, 24),
        "resolved_spot": Decimal("24500.00"),
        "gross_total_gex": Decimal("5000000000.00"),
        "scenario_net_gex": Decimal("2500000000.00"),
        "gross_total_dex": Decimal("1000000000.00"),
        "assumption_profile": "GROSS_UNSIGNED",
        "gamma_flip": GammaFlipResult(
            status="SIGNED_ASSUMPTION_REQUIRED",
            grid_lower_bound=Decimal("23000.00"),
            grid_upper_bound=Decimal("26000.00"),
        ),
        "concentration": GammaConcentration(),
        "exposure_quality": "HIGH",
        "warnings": [],
    }
    base.update(overrides)
    return AdvancedExposureSnapshot(**base)


def _sample_strike_rows() -> list[StrikeExposureRow]:
    return [
        StrikeExposureRow(
            strike_price=Decimal("24500.00"),
            is_atm=True,
            ce_open_interest=50000,
            pe_open_interest=45000,
            ce_gross_gex=Decimal("2000000000.00"),
            pe_gross_gex=Decimal("1800000000.00"),
            total_gross_gex=Decimal("3800000000.00"),
            net_scenario_gex=Decimal("200000000.00"),
            gross_dex=Decimal("500000000.00"),
        ),
        StrikeExposureRow(
            strike_price=Decimal("24600.00"),
            is_atm=False,
            ce_open_interest=30000,
            pe_open_interest=20000,
            ce_gross_gex=Decimal("1200000000.00"),
            pe_gross_gex=Decimal("800000000.00"),
            total_gross_gex=Decimal("2000000000.00"),
            net_scenario_gex=Decimal("-400000000.00"),
            gross_dex=Decimal("300000000.00"),
        ),
    ]


def _sample_gamma_profile() -> list[GammaProfilePoint]:
    return [
        GammaProfilePoint(
            spot_level=Decimal("22050.00"),
            spot_change_pct=Decimal("-10.00"),
            gross_gex=Decimal("100.00"),
            scenario_net_gex=Decimal("-99.99"),
        ),
        GammaProfilePoint(
            spot_level=Decimal("24500.00"),
            spot_change_pct=Decimal("0.00"),
            gross_gex=Decimal("150.00"),
            scenario_net_gex=Decimal("0.00"),
        ),
        GammaProfilePoint(
            spot_level=Decimal("26950.00"),
            spot_change_pct=Decimal("10.00"),
            gross_gex=Decimal("200.00"),
            scenario_net_gex=Decimal("10000000.00"),
        ),
    ]


def _render_tab(**overrides: Any) -> str:
    context: dict[str, Any] = {
        "symbol": "NIFTY",
        "trade_date": date(2026, 9, 24),
        "expiry_date": date(2026, 9, 24),
        "available_expiries": [],
        "snapshot": _sample_snapshot(),
        "strikes": _sample_strike_rows(),
        "gamma_profile": _sample_gamma_profile(),
        "unavailable_reason": None,
    }
    context.update(overrides)
    return _render("components/advanced_exposure_tab.html", context)


def _script_of(html: str) -> str:
    start = html.index("<script>") + len("<script>")
    return html[start : html.index("</script>")] if "</script>" in html else html[start:]


def _extract_json_array(script: str, var_name: str) -> list:
    match = re.search(r"var " + var_name + r" = (\[.*?\]);", script, re.S)
    assert match is not None, f"var {var_name} not found in chart bootstrap script"
    return json.loads(match.group(1))


class TestChartAContractSpotScenarioGammaProfile:
    """Chart A must visualize the existing backend spot scenario grid only."""

    def test_chart_a_canvas_and_container_render_with_data(self) -> None:
        html = _render_tab()
        assert f'<canvas id="{CHART_A_ID}"' in html
        assert "Spot Scenario Gamma Profile" in html
        # Fixed-height responsive wrapper (established project pattern)
        assert 'style="height: 320px; position: relative;"' in html
        # Chart.js 4.4.0 guarded loader, not a second library
        assert CHART_JS_URL in _script_of(html)

    def test_scenario_grid_serialized_as_valid_json_floats(self) -> None:
        html = _render_tab()
        data = _extract_json_array(_script_of(html), "scenarioGrid")
        assert [p["spot"] for p in data] == [22050.0, 24500.0, 26950.0]
        assert [p["chg"] for p in data] == [-10.0, 0.0, 10.0]
        assert [p["net"] for p in data] == [-99.99, 0.0, 10000000.0]

    def test_current_spot_serialized_when_available(self) -> None:
        script = _script_of(_render_tab())
        current_spot = re.search(r"var currentSpot = (.*?);", script).group(1)
        assert current_spot == "24500.0"
        # Header badge reflects the same existing backend value
        assert "Current Spot" in _render_tab()

    def test_current_spot_zero_is_treated_as_unavailable_not_genuine(self) -> None:
        # resolved_spot == 0.00 is the service's own "spot unresolved" sentinel;
        # the JS layer must not draw a Current Spot reference at 0.
        html = _render_tab(snapshot=_sample_snapshot(resolved_spot=Decimal("0.00")))
        assert "var currentSpot = 0.0;" in _script_of(html)
        assert "currentSpot > 0" in _script_of(html)  # guard present
        assert "Current Spot: ₹" not in html  # badge omitted

    def test_empty_scenario_grid_renders_truthful_empty_state(self) -> None:
        html = _render_tab(gamma_profile=[])
        assert "Spot scenario gamma data unavailable." in html
        assert f'<canvas id="{CHART_A_ID}"' not in html
        assert "NO_SCENARIO_GRID_POINTS" in html

    def test_none_scenario_grid_renders_truthful_reason_code(self) -> None:
        # The service returns None when lot-size provenance is unavailable.
        html = _render_tab(gamma_profile=None)
        assert "Spot scenario gamma data unavailable." in html
        assert "SCENARIO_GRID_NOT_COMPUTED_LOT_PROVENANCE_UNAVAILABLE" in html
        assert f'<canvas id="{CHART_A_ID}"' not in html


class TestChartAReferenceLines:
    """Current Spot / Gamma Flip / zero references per Phase 2A spec."""

    def test_gamma_flip_present_is_serialized_and_badged(self) -> None:
        html = _render_tab(
            snapshot=_sample_snapshot(
                gamma_flip=GammaFlipResult(
                    gamma_flip_level=Decimal("24300.50"),
                    status="FLIP_FOUND",
                    grid_lower_bound=Decimal("23000.00"),
                    grid_upper_bound=Decimal("26000.00"),
                )
            )
        )
        script = _script_of(html)
        gamma_flip = re.search(r"var gammaFlip = (.*?);", script).group(1)
        assert gamma_flip == "24300.5"
        assert "Gamma Flip: 24300.50" in html
        # Reference-line plugin draws the marker from the serialized value only
        assert "Gamma Flip" in script  # label used by the inline plugin

    def test_gamma_flip_null_omits_marker_and_reports_status(self) -> None:
        html = _render_tab()  # default sample has gamma_flip_level=None
        script = _script_of(html)
        gamma_flip = re.search(r"var gammaFlip = (.*?);", script).group(1)
        assert gamma_flip == "null"
        assert "Gamma Flip unavailable (SIGNED_ASSUMPTION_REQUIRED)" in html
        # No fabricated flip value anywhere in the payload
        assert '"flip"' not in script

    def test_zero_gamma_reference_is_enforced_on_axis(self) -> None:
        # suggestedMin/Max 0 keeps the zero gridline visible even for
        # strictly-positive or strictly-negative curves.
        script = _script_of(_render_tab())
        assert "suggestedMin: 0" in script
        assert "suggestedMax: 0" in script
        # The zero gridline is visually emphasized
        assert "ctx.tick.value === 0" in script


class TestChartBContractStrikeWiseNetGEX:
    """Chart B must visualize the existing backend strike exposure rows only."""

    def test_chart_b_canvas_and_container_render_with_data(self) -> None:
        html = _render_tab()
        assert f'<canvas id="{CHART_B_ID}"' in html
        assert "Strike-wise Net GEX Profile" in html

    def test_strike_rows_serialized_sorted_and_flagged(self) -> None:
        html = _render_tab()
        data = _extract_json_array(_script_of(html), "strikeRows")
        strikes = [r["strike"] for r in data]
        assert strikes == sorted(strikes), "strikes must be sorted numerically"
        assert [r["strike"] for r in data] == [24500.0, 24600.0]
        assert data[0]["atm"] is True
        assert data[1]["atm"] is False

    def test_positive_negative_and_zero_gex_distinguishable(self) -> None:
        script = _script_of(_render_tab())
        # Design-system semantics: green positive / red negative / neutral zero
        assert "rgba(34, 197, 94, 0.75)" in script
        assert "rgba(239, 68, 68, 0.75)" in script
        assert "rgba(148, 163, 184" in script
        assert "raw > 0" in script and "raw < 0" in script

    def test_unavailable_net_gex_serialized_as_null_not_zero(self) -> None:
        rows = [
            StrikeExposureRow(strike_price=Decimal("24400.00"), is_atm=False),
            StrikeExposureRow(
                strike_price=Decimal("24500.00"),
                is_atm=True,
                net_scenario_gex=Decimal("0.00"),
            ),
        ]
        script = _script_of(_render_tab(strikes=rows))
        data = _extract_json_array(script, "strikeRows")
        assert data[0]["net"] is None, "unavailable must serialize as null"
        assert data[1]["net"] == 0.0, "a genuine zero must remain zero"
        assert "N/A (unavailable)" in script  # tooltip stays truthful

    def test_empty_strike_exposure_renders_truthful_empty_state(self) -> None:
        html = _render_tab(strikes=[])
        assert "Strike-wise net GEX data unavailable." in html
        assert f'<canvas id="{CHART_B_ID}"' not in html
        assert "NO_STRIKE_EXPOSURE_ROWS" in html

    def test_atm_highlighted_only_from_backend_flag(self) -> None:
        html = _render_tab()
        assert "ATM strike highlighted" in html
        script = _script_of(html)
        assert "atmFlags" in script  # index-based, driven by backend is_atm


class TestHTMXLifecycleSafety:
    """No duplicate Chart.js instances / canvases / global handlers on reload."""

    def test_destroy_before_init_registry_present(self) -> None:
        script = _script_of(_render_tab())
        assert "__g11Opt12bAdvExposureCharts" in script  # registry key
        assert "destroyPreviousCharts" in script
        assert ".destroy()" in script

    def test_chart_js_loaded_once_via_guarded_loader(self) -> None:
        script = _script_of(_render_tab())
        assert "if (window.Chart)" in script
        assert "createElement('script')" in script
        # No bare external script tag that htmx would re-execute on each swap
        assert '<script src="https://cdn.jsdelivr.net/npm/chart.js' not in _render_tab()

    def test_no_global_htmx_event_handlers_registered(self) -> None:
        html = _render_tab()
        # G.11-OPT-12B Phase 2A hardening refines this contract: exactly ONE
        # scoped htmx listener is permitted — the once-registered beforeSwap
        # cleanup targeting this fragment's registry. Unscoped or accumulating
        # handlers (afterSwap/afterSettle or any second listener) remain banned.
        assert "htmx:afterSwap" not in html
        assert "htmx:afterSettle" not in html
        listeners = re.findall(r"addEventListener\('htmx:([a-zA-Z]+)'", html)
        assert listeners == ["beforeSwap"]


class TestExistingUIPreserved:
    """KPI cards, table, disclaimer, states and styling contract remain intact."""

    def test_kpi_cards_table_and_disclaimer_still_render(self) -> None:
        html = _render_tab()
        assert "Gross GEX" in html
        assert "Scenario Net GEX" in html
        assert "Gross DEX" in html
        assert "Top Strike Share" in html
        assert "Strike Exposure & GEX Breakdown" in html
        assert "Exposure Analytics Disclaimer" in html
        assert "table table-sm table-hover table-financial" in html
        assert "row row-cols-2 row-cols-md-3 row-cols-lg-6 g-3" in html

    def test_layout_order_kpi_then_charts_then_table(self) -> None:
        html = _render_tab()
        pos_kpi = html.index("row row-cols-2 row-cols-md-3 row-cols-lg-6 g-3")
        pos_chart_a = html.index("Spot Scenario Gamma Profile")
        pos_chart_b = html.index("Strike-wise Net GEX Profile")
        pos_table = html.index("Strike Exposure & GEX Breakdown")
        assert pos_kpi < pos_chart_a < pos_chart_b < pos_table

    def test_no_inert_tailwind_classes_introduced(self) -> None:
        html = _render_tab()
        for pattern in [r"\bgap-4\b", r"\brounded-lg\b", r"\bbg-slate-800\b", r"\bgrid-cols-\d\b"]:
            assert re.search(pattern, html) is None, f"inert class {pattern} reintroduced"

    def test_no_chart_markup_when_both_datasets_missing(self) -> None:
        html = _render_tab(snapshot=None, strikes=[], gamma_profile=[])
        assert "<script>" not in html
        assert f'canvas id="{CHART_A_ID}"' not in html
        assert f'canvas id="{CHART_B_ID}"' not in html
        assert "Spot scenario gamma data unavailable." in html
        assert "Strike-wise net GEX data unavailable." in html

    def test_provenance_labelled_as_eod_not_live(self) -> None:
        html = _render_tab()
        assert "persisted EOD data" in html
        assert "not live" in html


class TestAdvancedExposureChartsEndpoint:
    """Endpoint-level regression: partial still renders 200 with chart contract."""

    async def _fetch(self, **params: Any) -> tuple[int, str]:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                "/market-data/derivatives/advanced-exposure/tab",
                params={"symbol": "NIFTY", **params},
            )
            return resp.status_code, resp.text

    async def test_endpoint_200_and_truthful_chart_states(self) -> None:
        status, text = await self._fetch(trade_date="2026-09-24")
        assert status == 200
        assert 'id="advanced-exposure-container"' in text
        # Whichever state applies, it must be truthful: either real canvases
        # backed by the chart bootstrap, or explicit empty states — never a
        # canvas without the lifecycle registry.
        if "<canvas" in text:
            assert "__g11Opt12bAdvExposureCharts" in text
            assert CHART_JS_URL in text
        else:
            assert "data unavailable" in text or "unavailable" in text.lower()

    async def test_endpoint_invalid_expiry_still_422(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                "/market-data/derivatives/advanced-exposure/tab",
                params={"symbol": "NIFTY", "expiry_date": "not-a-date"},
            )
            assert resp.status_code == 422
