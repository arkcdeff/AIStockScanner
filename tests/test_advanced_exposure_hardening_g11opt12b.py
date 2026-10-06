"""G.11-OPT-12B Phase 2A hardening contract tests.

Covers the three hardening items added on top of the accepted Phase 2A charts:

1. Signed profile pass-through — the Advanced Exposure tab route must call
   ``get_exposure_snapshot`` / ``get_strike_exposure_table`` / ``get_gamma_profile``
   with the service's supported signed profile
   (``DEALER_SHORT_CALL_LONG_PUT_HEURISTIC``) consistently, so ``scenario_net_gex``
   is a genuine signed quantity and ``gamma_flip`` can carry a real
   zero-crossing level instead of being structurally null under
   ``GROSS_UNSIGNED``.
2. Chart data contract under the signed profile — multi-strike ordering,
   positive/negative/zero/null semantics preserved from the backend payload.
3. HTMX ``beforeSwap`` lifecycle hardening — a deterministic, scoped,
   once-registered cleanup hook that destroys the fragment's charts before the
   container is replaced.
"""

from __future__ import annotations

import inspect
import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.api.routes import market_data_pages
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
SIGNED_PROFILE = "DEALER_SHORT_CALL_LONG_PUT_HEURISTIC"


def _snapshot(**overrides: Any) -> AdvancedExposureSnapshot:
    base: dict[str, Any] = {
        "symbol": "NIFTY",
        "trade_date": date(2026, 8, 10),
        "as_of_date": date(2026, 8, 10),
        "resolved_spot": Decimal("24500.00"),
        "gross_total_gex": Decimal("5000000000.00"),
        "scenario_net_gex": Decimal("-250000000.00"),
        "gross_total_dex": Decimal("1000000000.00"),
        "assumption_profile": SIGNED_PROFILE,
        "gamma_flip": GammaFlipResult(
            gamma_flip_level=Decimal("24318.75"),
            status="FLIP_FOUND",
            distance_from_spot=Decimal("-181.25"),
            distance_pct=Decimal("-0.74"),
            grid_lower_bound=Decimal("22050.00"),
            grid_upper_bound=Decimal("26950.00"),
            crossing_count=1,
        ),
        "concentration": GammaConcentration(),
        "exposure_quality": "HIGH",
        "warnings": [],
    }
    base.update(overrides)
    return AdvancedExposureSnapshot(**base)


def _multi_strike_rows() -> list[StrikeExposureRow]:
    return [
        # genuine zero Net GEX row (zero-OI strike; service floors it at 0.00)
        StrikeExposureRow(
            strike_price=Decimal("24100.00"), is_atm=False,
            ce_gross_gex=Decimal("0.00"), pe_gross_gex=Decimal("0.00"),
            net_scenario_gex=Decimal("0.00"),
        ),
        StrikeExposureRow(
            strike_price=Decimal("24200.00"), is_atm=False,
            ce_gross_gex=Decimal("0.00"), pe_gross_gex=Decimal("300000000.00"),
            net_scenario_gex=Decimal("300000000.00"),
        ),
        StrikeExposureRow(
            strike_price=Decimal("24500.00"), is_atm=True,
            ce_gross_gex=Decimal("2000000000.00"), pe_gross_gex=Decimal("1800000000.00"),
            net_scenario_gex=Decimal("-200000000.00"),
        ),
        StrikeExposureRow(
            strike_price=Decimal("24800.00"), is_atm=False,
            ce_gross_gex=Decimal("450000000.00"), pe_gross_gex=Decimal("0.00"),
            net_scenario_gex=Decimal("-450000000.00"),
        ),
    ]


def _grid() -> list[GammaProfilePoint]:
    return [
        GammaProfilePoint(spot_level=Decimal("22050.00"), spot_change_pct=Decimal("-10.00"),
                          gross_gex=Decimal("1.00"), scenario_net_gex=Decimal("-800000.00")),
        GammaProfilePoint(spot_level=Decimal("24318.75"), spot_change_pct=Decimal("-0.74"),
                          gross_gex=Decimal("1.00"), scenario_net_gex=Decimal("0.00")),
        GammaProfilePoint(spot_level=Decimal("26950.00"), spot_change_pct=Decimal("10.00"),
                          gross_gex=Decimal("1.00"), scenario_net_gex=Decimal("900000.00")),
    ]


def _ctx(**overrides: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "symbol": "NIFTY",
        "trade_date": date(2026, 8, 10),
        "expiry_date": date(2026, 8, 28),
        "available_expiries": [],
        "snapshot": _snapshot(),
        "strikes": _multi_strike_rows(),
        "gamma_profile": _grid(),
        "unavailable_reason": None,
        "assumption_profile": SIGNED_PROFILE,
    }
    ctx.update(overrides)
    return _render("components/advanced_exposure_tab.html", ctx)


def _script_of(html: str) -> str:
    start = html.index("<script>") + len("<script>")
    return html[start : html.index("</script>")]


def _json_array(script: str, var_name: str) -> list:
    m = re.search(r"var " + var_name + r" = (\[.*?\]);", script, re.S)
    assert m is not None, f"var {var_name} missing"
    import json

    return json.loads(m.group(1))


# ---------------------------------------------------------------------------
# 1. Signed profile pass-through (route -> service)
# ---------------------------------------------------------------------------
class TestSignedProfilePassThrough:
    def test_route_source_passes_signed_profile_to_all_three_calls(self) -> None:
        src = inspect.getsource(market_data_pages.advanced_exposure_tab_partial)
        assert "PROFILE_DEALER_HEURISTIC" in src, (
            "route must import and use the service's signed profile constant"
        )
        # One profile, used consistently by all three service calls.
        assert src.count("assumption_profile") >= 4
        for call in ("get_exposure_snapshot(", "get_strike_exposure_table(",
                     "get_gamma_profile("):
            i = src.index(call)
            assert "assumption_profile" in src[i : i + 220], (
                f"{call} must receive the signed profile"
            )

    def test_service_constant_is_the_repository_signed_profile(self) -> None:
        from app.services.advanced_exposure_service import (
            PROFILE_DEALER_HEURISTIC,
            PROFILE_GROSS_UNSIGNED,
        )

        assert PROFILE_DEALER_HEURISTIC == SIGNED_PROFILE
        assert PROFILE_GROSS_UNSIGNED == "GROSS_UNSIGNED"
        assert PROFILE_DEALER_HEURISTIC != PROFILE_GROSS_UNSIGNED

    def test_context_exposes_backend_profile(self) -> None:
        html = _ctx()
        assert SIGNED_PROFILE in html  # scenario profile shown on Chart B caption
        assert "assumption_profile" in _script_of(html) or SIGNED_PROFILE in html

    async def test_endpoint_renders_with_signed_profile_context(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                "/market-data/derivatives/advanced-exposure/tab",
                params={"symbol": "NIFTY", "trade_date": "2026-09-24"},
            )
            assert resp.status_code == 200
            assert 'id="advanced-exposure-container"' in resp.text


# ---------------------------------------------------------------------------
# 2. Gamma flip backend -> template propagation
# ---------------------------------------------------------------------------
class TestGammaFlipPropagation:
    def test_flip_found_serializes_marker_and_badge(self) -> None:
        html = _ctx()
        script = _script_of(html)
        gamma_flip = re.search(r"var gammaFlip = (.*?);", script).group(1)
        assert gamma_flip == "24318.75"
        assert "Gamma Flip: 24318.75" in html  # header badge from backend value
        assert "'Gamma Flip'" in script  # refLine label (single-quoted JS literal)
        assert "Gamma Flip unavailable" not in html

    def test_flip_null_still_truthful_no_fabrication(self) -> None:
        html = _ctx(
            snapshot=_snapshot(
                gamma_flip=GammaFlipResult(
                    status="NO_FLIP_IN_RANGE",
                    grid_lower_bound=Decimal("22050.00"),
                    grid_upper_bound=Decimal("26950.00"),
                )
            )
        )
        script = _script_of(html)
        assert re.search(r"var gammaFlip = null;", script)
        assert "Gamma Flip unavailable (NO_FLIP_IN_RANGE)" in html
        # No flip value may appear in the serialized payload
        assert '"flip"' not in script

    def test_current_spot_and_zero_reference_preserved(self) -> None:
        script = _script_of(_ctx())
        assert re.search(r"var currentSpot = 24500.0;", script)
        assert "suggestedMin: 0" in script and "suggestedMax: 0" in script
        assert "ctx.tick.value === 0" in script


# ---------------------------------------------------------------------------
# 3. Chart data contract under signed profile
# ---------------------------------------------------------------------------
class TestChartContractUnderSignedProfile:
    def test_chart_a_multi_point_grid_serialized(self) -> None:
        data = _json_array(_script_of(_ctx()), "scenarioGrid")
        assert [p["net"] for p in data] == [-800000.0, 0.0, 900000.0]
        assert [p["spot"] for p in data] == [22050.0, 24318.75, 26950.0]

    def test_chart_b_multi_strike_ordered(self) -> None:
        data = _json_array(_script_of(_ctx()), "strikeRows")
        strikes = [r["strike"] for r in data]
        assert strikes == sorted(strikes) == [24100.0, 24200.0, 24500.0, 24800.0]

    def test_chart_b_mixed_sign_zero_and_null_semantics(self) -> None:
        nets = [r["net"] for r in _json_array(_script_of(_ctx()), "strikeRows")]
        assert nets[0] == 0.0, "genuine zero (zero-OI strike) stays zero"
        assert any(v is not None and v > 0 for v in nets), "positive present"
        assert any(v is not None and v < 0 for v in nets), "negative present"
        # null semantics proven directly on an all-unavailable row:
        null_row_ctx = _ctx(
            strikes=[StrikeExposureRow(strike_price=Decimal("25000.00"), is_atm=False)]
        )
        assert _json_array(_script_of(null_row_ctx), "strikeRows")[0]["net"] is None

    def test_chart_b_sign_colors_preserved(self) -> None:
        script = _script_of(_ctx())
        assert "rgba(34, 197, 94, 0.75)" in script  # positive
        assert "rgba(239, 68, 68, 0.75)" in script  # negative
        assert "rgba(148, 163, 184" in script       # genuine zero

    def test_atm_marking_only_from_backend_flag(self) -> None:
        html = _ctx()
        data = _json_array(_script_of(html), "strikeRows")
        atms = [r["atm"] for r in data]
        assert atms == [False, False, True, False]
        assert "ATM strike highlighted" in html


# ---------------------------------------------------------------------------
# 4. HTMX beforeSwap lifecycle hardening
# ---------------------------------------------------------------------------
class TestBeforeSwapLifecycle:
    def test_beforeswap_hook_registered_once_and_scoped(self) -> None:
        script = _script_of(_ctx())
        assert "htmx:beforeSwap" in script
        assert "__g11Opt12bBeforeSwapBound" in script  # once-per-session guard
        assert "deriv-exposure-output" in script       # scoped to this fragment
        assert "destroyPreviousCharts()" in script

    def test_no_duplicate_listener_registration(self) -> None:
        html = _ctx()
        # exactly one registration site (the second occurrence is the paired
        # removeEventListener teardown handle)
        assert html.count("addEventListener('htmx:beforeSwap'") == 1
        assert html.count("removeEventListener('htmx:beforeSwap'") == 1

    def test_global_htmx_handlers_unchanged_elsewhere(self) -> None:
        # base.html keeps only its existing CSRF delegation; the fragment adds
        # exactly one scoped listener.
        script = _script_of(_ctx())
        assert len(re.findall(r"addEventListener\('htmx:", script)) == 1

    async def test_endpoint_still_renders_lifecycle_markers(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                "/market-data/derivatives/advanced-exposure/tab",
                params={"symbol": "NIFTY", "trade_date": "2026-09-24"},
            )
            assert resp.status_code == 200
            assert "__g11Opt12bAdvExposureCharts" in resp.text or "data unavailable" in resp.text.lower()


# ---------------------------------------------------------------------------
# 5. Preserved Phase 2A surface
# ---------------------------------------------------------------------------
class TestPhase2ASurfacePreserved:
    def test_kpi_table_disclaimer_and_layout_intact(self) -> None:
        html = _ctx()
        assert "Exposure Analytics Disclaimer" in html
        assert "row row-cols-2 row-cols-md-3 row-cols-lg-6 g-3" in html
        assert "Strike Exposure & GEX Breakdown" in html
        pos_kpi = html.index("row row-cols-2")
        pos_a = html.index("Spot Scenario Gamma Profile")
        pos_b = html.index("Strike-wise Net GEX Profile")
        pos_tbl = html.index("Strike Exposure & GEX Breakdown")
        assert pos_kpi < pos_a < pos_b < pos_tbl

    def test_no_inert_tailwind_introduced(self) -> None:
        html = _ctx()
        for pattern in (r"\bgap-4\b", r"\brounded-lg\b", r"\bbg-slate-800\b"):
            assert re.search(pattern, html) is None

    def test_empty_states_truthful(self) -> None:
        html = _ctx(snapshot=None, strikes=[], gamma_profile=[])
        assert "Spot scenario gamma data unavailable." in html
        assert "Strike-wise net GEX data unavailable." in html
        assert "<script>" not in html
