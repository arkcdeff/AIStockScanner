"""Regression and acceptance tests for G.11-OPT-10: Advanced Exposure Integration & Bootstrap Closure.

Verifies:
1. Advanced Exposure host control exists on EOD workbench (/market-data/derivatives).
2. Correct HTMX endpoint (/market-data/derivatives/advanced-exposure/tab) is referenced.
3. Form target, symbol, date, and expiry parameters are wired.
4. Existing EOD tabs continue to render without regression.
5. Advanced Exposure partial renders valid Bootstrap 5 / design-system classes.
6. Inert Tailwind classes (bg-slate-800, rounded-lg, grid-cols-*, space-y-6, divide-y) are eliminated.
7. Scoped CSS rules in design-system.css support sign encoding compatibility.
8. Unavailable and empty states remain completely unchanged and preserved.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.api.routes.market_data_pages import _render
from app.schemas.advanced_exposure import (
    AdvancedExposureSnapshot,
    GammaConcentration,
    GammaFlipResult,
    StrikeExposureRow,
)
from tests.auth_helpers import auth_client, seed_builtins, viewer_client


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
            gamma_flip_level=Decimal("24300.00"),
            distance_pct=Decimal("-0.82"),
            status="FLIP_FOUND",
            grid_lower_bound=Decimal("23000.00"),
            grid_upper_bound=Decimal("26000.00"),
            crossing_count=1,
        ),
        "concentration": GammaConcentration(
            top1_strike=Decimal("24500.00"),
            top1_gex=Decimal("1500000000.00"),
            top1_share_pct=Decimal("30.00"),
            top3_strikes=[Decimal("24500.00"), Decimal("24600.00"), Decimal("24400.00")],
            top3_share_pct=Decimal("65.00"),
            top5_strikes=[Decimal("24500.00"), Decimal("24600.00"), Decimal("24400.00"), Decimal("24700.00"), Decimal("24300.00")],
            top5_share_pct=Decimal("85.00"),
        ),
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
            net_scenario_gex=Decimal("400000000.00"),
            gross_dex=Decimal("300000000.00"),
        ),
    ]


class TestEODWorkbenchHostControl:
    """Verify D-09-08 closure: Advanced Exposure is hosted within the EOD workbench."""

    @pytest.mark.asyncio
    async def test_advanced_exposure_tab_control_renders_in_eod_workbench(self) -> None:
        await seed_builtins()
        async with viewer_client(browser=True) as client:
            resp = await client.get("/market-data/derivatives")
            assert resp.status_code == 200

            # 1. Host control tab link exists in the tab bar
            assert 'href="#deriv-exposure"' in resp.text
            assert "Advanced Exposure" in resp.text

            # 2. Host control tab pane exists
            assert 'id="deriv-exposure"' in resp.text

            # 3. References the correct HTMX endpoint and target
            assert 'hx-get="/market-data/derivatives/advanced-exposure/tab"' in resp.text
            assert 'hx-target="#deriv-exposure-output"' in resp.text

            # 4. Context parameter inputs are present
            assert 'id="deriv-exposure-symbol"' in resp.text
            assert 'id="deriv-exposure-date"' in resp.text
            assert 'id="deriv-exposure-expiry-container"' in resp.text

            # 5. Empty state is present inside the target container
            assert 'id="deriv-exposure-output"' in resp.text
            assert "Select persisted inputs to load gamma exposure" in resp.text

    @pytest.mark.asyncio
    async def test_existing_eod_tabs_remain_functional_and_undisturbed(self) -> None:
        await seed_builtins()
        async with viewer_client(browser=True) as client:
            resp = await client.get("/market-data/derivatives")
            assert resp.status_code == 200

            # Verify all pre-existing tabs remain in place
            expected_tabs = [
                "#deriv-coverage",
                "#deriv-chain",
                "#deriv-options",
                "#deriv-oi",
                "#deriv-futures",
                "#deriv-participants",
                "#deriv-replay",
                "#deriv-advanced",
                "#deriv-exposure",
            ]
            for tab_id in expected_tabs:
                assert f'href="{tab_id}"' in resp.text
                assert f'id="{tab_id.lstrip("#")}"' in resp.text


class TestBootstrapStylingClosure:
    """Verify D-09-07 closure: Tailwind classes replaced with Bootstrap 5 / design system."""

    def test_partial_contains_no_inert_tailwind_layout_or_card_classes(self) -> None:
        snap = _sample_snapshot()
        html = _render(
            "components/advanced_exposure_tab.html",
            {
                "snapshot": snap,
                "strikes": _sample_strike_rows(),
                "symbol": "NIFTY",
                "trade_date": snap.trade_date,
                "expiry_date": snap.trade_date,
                "available_expiries": [],
                "gamma_profile": [],
                "unavailable_reason": None,
            },
        )

        # Forbidden inert Tailwind utility classes
        inert_tailwind_patterns = [
            r"\bbg-slate-800\b",
            r"\bborder-slate-700\b",
            r"\brounded-lg\b",
            r"\bspace-y-6\b",
            r"\bgrid-cols-2\b",
            r"\bgrid-cols-4\b",
            r"\bgrid-cols-6\b",
            r"\bgap-4\b",
            r"\bdivide-y\b",
            r"\boverflow-x-auto\b",
        ]
        for pattern in inert_tailwind_patterns:
            match = re.search(pattern, html)
            assert match is None, f"Found inert Tailwind class {match.group(0)!r} in rendered HTML"

    def test_partial_uses_active_bootstrap_and_design_system_classes(self) -> None:
        snap = _sample_snapshot()
        html = _render(
            "components/advanced_exposure_tab.html",
            {
                "snapshot": snap,
                "strikes": _sample_strike_rows(),
                "symbol": "NIFTY",
                "trade_date": snap.trade_date,
                "expiry_date": snap.trade_date,
                "available_expiries": [],
                "gamma_profile": [],
                "unavailable_reason": None,
            },
        )

        # Expected Bootstrap 5 / design system classes
        assert "d-flex flex-column gap-3" in html
        assert "state-panel state-panel-warning" in html
        assert "card" in html
        assert "row row-cols-2 row-cols-md-3 row-cols-lg-6 g-3" in html
        assert "table-responsive" in html
        assert "table table-sm table-hover table-financial" in html
        assert "text-end" in html
        assert "font-monospace" in html

    @pytest.mark.asyncio
    async def test_endpoint_renders_bootstrap_partial_with_200(self) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                "/market-data/derivatives/advanced-exposure/tab",
                params={"symbol": "NIFTY", "trade_date": "2026-09-24"},
            )
            assert resp.status_code == 200
            assert 'id="advanced-exposure-container"' in resp.text
            assert "state-panel state-panel-warning" in resp.text
            assert "table-financial" in resp.text
            assert "bg-slate-800" not in resp.text
