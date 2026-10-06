"""Focused regression tests for G.11-OPT-8 - Advanced Exposure HTTP 500.

Defect fixed
------------
``GET /market-data/derivatives/advanced-exposure/tab`` returned **HTTP 500 for
every parameter combination**. The exposure service returns a fully-constructed
but all-``None`` :class:`AdvancedExposureSnapshot` whenever the underlying
resolves yet no stored EOD option history exists. Such a snapshot is *truthy*
(Pydantic v2 models define no ``__bool__``), so the template's
``{% if snapshot %}`` guard passed and an unguarded comparison raised::

    TypeError: '>' not supported between instances of 'NoneType' and 'int'

These tests pin the whole contract that the fix must uphold:

* the crash cannot come back - for ``None``, positive, negative and zero net GEX;
* every nullable analytics field is guarded and prints ``N/A``, never a Python
  ``None`` repr, ``None%``, ``NaN`` or ``Infinity``;
* a missing metric stays missing - it is never replaced by ``0``;
* an unavailable selection renders an explicit reason code;
* the route no longer swallows unexpected exceptions, while the expected
  data-availability failure still renders rather than erroring;
* authentication and invalid-parameter handling are unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes.market_data_pages import _render
from app.db.session import get_session_factory
from app.main import app
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.schemas.advanced_exposure import (
    AdvancedExposureSnapshot,
    GammaConcentration,
    GammaFlipResult,
    StrikeExposureRow,
)
from app.services.advanced_exposure_service import _add_exposure, _sum_exposure
from tests.auth_helpers import auth_client, seed_builtins

ROUTE = "/market-data/derivatives/advanced-exposure/tab"
REST_EXPOSURE = "/api/v1/market-data/derivatives/options/exposure"

TRADE_DATE = date(2026, 9, 24)
UNAVAILABLE_HEADLINE = "Exposure analytics unavailable"

# Printed only when a nullable value reaches the template unguarded.
_LEAK_PATTERNS = (
    r"\bNone\b",
    r"\bNaN\b",
    r"\bInfinity\b",
    r"\b(?:nan|inf|-inf)\b",
    r"None%",
    r"Strike: None",
    r"'>' not supported",
    r"not supported between instances",
)


def _assert_no_none_leak(html: str) -> None:
    for pattern in _LEAK_PATTERNS:
        match = re.search(pattern, html)
        assert match is None, f"leaked {match.group(0)!r} (pattern {pattern!r}) into HTML"


def _empty_snapshot(**overrides: Any) -> AdvancedExposureSnapshot:
    """The exact payload the service returns for an unresolved/empty EOD chain."""
    base: dict[str, Any] = {
        "symbol": "NIFTY",
        "trade_date": TRADE_DATE,
        "as_of_date": TRADE_DATE,
        "resolved_spot": Decimal("0.00"),
        "gamma_flip": GammaFlipResult(
            gamma_flip_level=None,
            status="INSUFFICIENT_DATA",
            grid_lower_bound=Decimal("0.00"),
            grid_upper_bound=Decimal("0.00"),
        ),
        "concentration": GammaConcentration(),
        "exposure_quality": "INSUFFICIENT",
        "warnings": ["OPTION_CHAIN_EMPTY"],
    }
    base.update(overrides)
    return AdvancedExposureSnapshot(**base)


def _populated_snapshot(**overrides: Any) -> AdvancedExposureSnapshot:
    """A fully-populated snapshot (MEDIUM quality -> neutral Data Quality colour)."""
    base: dict[str, Any] = {
        "symbol": "NIFTY",
        "trade_date": TRADE_DATE,
        "as_of_date": TRADE_DATE,
        "resolved_spot": Decimal("24550.25"),
        "gross_call_gex": Decimal("4239265625.00"),
        "gross_put_gex": Decimal("1331900000.00"),
        "gross_total_gex": Decimal("5571165625.00"),
        "scenario_call_gex": Decimal("4239265625.00"),
        "scenario_put_gex": Decimal("1331900000.00"),
        "scenario_net_gex": Decimal("2907365625.00"),
        "gross_total_dex": Decimal("16852631250.00"),
        "scenario_net_dex": Decimal("16852631250.00"),
        "gross_total_vanna": Decimal("92058750.00"),
        "gross_total_charm": Decimal("42568750.00"),
        "gamma_flip": GammaFlipResult(
            gamma_flip_level=Decimal("24310.50"),
            status="FLIP_FOUND",
            distance_from_spot=Decimal("-239.75"),
            distance_pct=Decimal("-0.98"),
            grid_lower_bound=Decimal("22000.00"),
            grid_upper_bound=Decimal("27000.00"),
            crossing_count=1,
        ),
        "concentration": GammaConcentration(
            top1_strike=Decimal("24500.0000"),
            top1_gex=Decimal("4239265625.00"),
            top1_share_pct=Decimal("76.09"),
            top3_strikes=[Decimal("24500.0000")],
            top3_share_pct=Decimal("100.00"),
            top5_strikes=[Decimal("24500.0000")],
            top5_share_pct=Decimal("100.00"),
        ),
        "exposure_quality": "MEDIUM",
        "warnings": [],
    }
    base.update(overrides)
    return AdvancedExposureSnapshot(**base)


def _render_partial(snapshot: Any, **extra: Any) -> str:
    ctx: dict[str, Any] = {
        "snapshot": snapshot,
        "strikes": [],
        "symbol": "NIFTY",
        "trade_date": TRADE_DATE,
        "expiry_date": TRADE_DATE,
        "available_expiries": [],
        "gamma_profile": [],
    }
    ctx.update(extra)
    return _render("components/advanced_exposure_tab.html", ctx)


async def _seed_index_only(code: str = "NIFTY") -> None:
    """Underlying resolvable, zero contracts - the exact shape that produced the 500."""
    factory = get_session_factory()
    async with factory() as session:
        session.add(MarketIndex(code=code, name=f"{code} Index", is_active=True))
        await session.commit()


async def _seed_single_call_contract(
    *,
    trade_d: date = date(2026, 8, 10),
    expiry_d: date = date(2026, 8, 28),
    available_at: date | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", is_active=True)
        session.add(idx)
        await session.flush()
        session.add(
            MarketIndexPrice(
                market_index_id=idx.id,
                trade_date=trade_d,
                open=Decimal("24500.0"),
                high=Decimal("24600.0"),
                low=Decimal("24450.0"),
                close=Decimal("24500.0"),
            )
        )
        contract = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24500CE",
            expiry_date=expiry_d,
            strike_price=Decimal("24500.0"),
            option_type="CE",
            lot_size=25,
            source_name="NSE_FO_BHAV",
            available_at=available_at or trade_d,
        )
        session.add(contract)
        await session.flush()
        session.add(
            DerivativeDailyPrice(
                contract_id=contract.id,
                trade_date=trade_d,
                close=Decimal("320.0"),
                settlement_price=Decimal("320.0"),
                volume_contracts=1000,
                open_interest=50000,
                underlying_value=Decimal("24500.0"),
                available_at=available_at or trade_d,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# 1. The crash itself - template level
# ---------------------------------------------------------------------------


class TestTemplateNoneSafety:
    """The unguarded ``None > 0`` comparison must never come back."""

    def test_all_none_snapshot_renders_unavailable_state(self) -> None:
        html = _render_partial(_empty_snapshot())
        assert UNAVAILABLE_HEADLINE in html
        assert "Reason:" in html
        assert "OPTION_CHAIN_EMPTY" in html
        _assert_no_none_leak(html)

    def test_snapshot_none_renders_unavailable_state(self) -> None:
        html = _render_partial(None)
        assert UNAVAILABLE_HEADLINE in html
        assert "NO_EXPOSURE_SNAPSHOT" in html
        _assert_no_none_leak(html)

    def test_explicit_unavailable_reason_is_preferred(self) -> None:
        html = _render_partial(
            None, unavailable_reason="UNDERLYING_NOT_PRESENT_IN_MARKET_INDICES: boom"
        )
        assert "UNDERLYING_NOT_PRESENT_IN_MARKET_INDICES" in html
        _assert_no_none_leak(html)

    def test_positive_net_gex_uses_emerald_and_exact_value(self) -> None:
        html = _render_partial(_populated_snapshot(scenario_net_gex=Decimal("2907365625.00")))
        assert "text-emerald-400" in html
        assert "text-rose-400" not in html
        assert "2907365625.00" in html
        _assert_no_none_leak(html)

    def test_negative_net_gex_uses_rose_and_exact_value(self) -> None:
        html = _render_partial(_populated_snapshot(scenario_net_gex=Decimal("-2907365625.00")))
        assert "text-rose-400" in html
        assert "text-emerald-400" not in html
        assert "-2907365625.00" in html
        _assert_no_none_leak(html)

    def test_zero_net_gex_is_neutral_and_kept_exact(self) -> None:
        html = _render_partial(_populated_snapshot(scenario_net_gex=Decimal("0.00")))
        assert "text-rose-400" not in html
        assert "text-emerald-400" not in html
        assert "0.00" in html
        _assert_no_none_leak(html)

    @pytest.mark.parametrize(
        "field",
        [
            "gross_call_gex",
            "gross_put_gex",
            "gross_total_gex",
            "scenario_call_gex",
            "scenario_put_gex",
            "scenario_net_gex",
            "gross_total_dex",
            "scenario_net_dex",
            "gross_total_vanna",
            "gross_total_charm",
        ],
    )
    def test_each_missing_scalar_metric_is_guarded(self, field: str) -> None:
        html = _render_partial(_populated_snapshot(**{field: None}))
        assert UNAVAILABLE_HEADLINE not in html, (
            f"{field}=None should still leave the other metrics available"
        )
        _assert_no_none_leak(html)

    @pytest.mark.parametrize(
        ("overrides", "marker"),
        [
            ({"gross_total_gex": None}, "N/A"),
            ({"scenario_net_gex": None}, "N/A"),
            ({"gross_total_dex": None}, "N/A"),
            (
                {"concentration": GammaConcentration(top1_share_pct=None, top1_strike=Decimal("24500"))},
                "N/A",
            ),
            (
                {"concentration": GammaConcentration(top1_share_pct=Decimal("76.09"), top1_strike=None)},
                "Strike:",
            ),
        ],
        ids=[
            "gross_total_gex",
            "scenario_net_gex",
            "gross_total_dex",
            "top1_share_pct",
            "top1_strike",
        ],
    )
    def test_displayed_metric_shows_placeholder_when_missing(
        self, overrides: dict[str, Any], marker: str
    ) -> None:
        """A KPI the page displays must read a placeholder, never a Python None repr."""
        html = _render_partial(_populated_snapshot(**overrides))
        assert marker in html
        _assert_no_none_leak(html)

    def test_missing_concentration_fields_are_guarded(self) -> None:
        html = _render_partial(
            _populated_snapshot(concentration=GammaConcentration())
        )
        assert "N/A" in html
        _assert_no_none_leak(html)

    def test_missing_gamma_flip_fields_render_status_not_none(self) -> None:
        html = _render_partial(
            _populated_snapshot(
                gamma_flip=GammaFlipResult(
                    gamma_flip_level=None,
                    status="NO_FLIP_IN_RANGE",
                    grid_lower_bound=Decimal("0.00"),
                    grid_upper_bound=Decimal("0.00"),
                )
            )
        )
        assert "NO_FLIP_IN_RANGE" in html
        _assert_no_none_leak(html)

    def test_row_level_missing_metrics_render_dash(self) -> None:
        row = StrikeExposureRow(
            strike_price=Decimal("24500.0000"),
            is_atm=True,
            ce_open_interest=50000,
            pe_open_interest=0,
            ce_gross_gex=Decimal("4239265625.00"),
            pe_gross_gex=None,
            total_gross_gex=None,
            net_scenario_gex=None,
            gross_dex=None,
        )
        html = _render_partial(_populated_snapshot(), strikes=[row])
        assert "24500.0000" in html
        assert "—" in html
        _assert_no_none_leak(html)

    def test_quality_warning_is_surfaced(self) -> None:
        html = _render_partial(
            _populated_snapshot(warnings=["OPTION_CHAIN_MISSING_PUT_SIDE"])
        )
        assert "OPTION_CHAIN_MISSING_PUT_SIDE" in html
        # The old page asserted an unqualified "PIT Verified" claim regardless of quality.
        assert "PIT Verified" not in html

    def test_no_hardcoded_pit_verified_claim(self) -> None:
        assert "PIT Verified" not in _render_partial(_empty_snapshot())
        assert "PIT Verified" not in _render_partial(_populated_snapshot())


# ---------------------------------------------------------------------------
# 2. No fabrication - a missing value must stay missing
# ---------------------------------------------------------------------------


class TestNoFabrication:
    """Missing inputs must not become ``0``/``NaN``/``Infinity``."""

    def test_sum_and_add_preserve_missing_as_missing(self) -> None:
        assert _sum_exposure([]) is None
        assert _sum_exposure([None, None]) is None
        assert _add_exposure(None, None) is None
        # Only real values are accumulated; a present value is not zeroed either.
        assert _add_exposure(None, Decimal("5.00")) == Decimal("5.00")
        assert _add_exposure(Decimal("5.00"), None) == Decimal("5.00")

    def test_empty_snapshot_metrics_are_none_not_zero(self) -> None:
        snap = _empty_snapshot()
        for field in (
            "gross_call_gex",
            "gross_put_gex",
            "gross_total_gex",
            "scenario_call_gex",
            "scenario_put_gex",
            "scenario_net_gex",
            "gross_total_dex",
            "scenario_net_dex",
            "gross_total_vanna",
            "gross_total_charm",
        ):
            assert getattr(snap, field) is None, f"{field} must stay None, not 0"
        assert snap.concentration.top1_share_pct is None
        assert snap.concentration.top1_gex is None
        assert snap.concentration.top3_strikes == []
        assert snap.gamma_flip.gamma_flip_level is None

    async def test_rest_payload_reports_null_and_json_safe(self) -> None:
        """The JSON twin must say ``null`` (never 0) and contain no NaN/Infinity."""
        await _seed_index_only()
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                REST_EXPOSURE,
                params={
                    "symbol": "NIFTY",
                    "trade_date": "2026-09-24",
                    "expiry_date": "2026-09-24",
                },
            )
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["scenario_net_gex"] is None
            assert payload["gross_total_gex"] is None
            assert payload["exposure_quality"] == "INSUFFICIENT"
            # allow_nan=False raises if any NaN/Infinity sneaked into the payload.
            json.dumps(payload, allow_nan=False)


# ---------------------------------------------------------------------------
# 3. The route - status codes, reasons, auth, invalid parameters
# ---------------------------------------------------------------------------


class TestRoute:
    async def test_index_present_without_contracts_returns_200(self) -> None:
        """The exact D-04 trigger: resolvable underlying, zero persisted contracts."""
        await _seed_index_only()
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            for params in (
                {},
                {"symbol": "NIFTY"},
                {"symbol": "NIFTY", "trade_date": "2026-09-24"},
                {"symbol": "NIFTY", "trade_date": "2026-09-24", "expiry_date": "2026-09-24"},
                {"symbol": "NIFTY", "trade_date": "2026-09-24", "as_of_date": "2026-09-24"},
            ):
                resp = await client.get(ROUTE, params=params)
                assert resp.status_code == 200, f"{params} -> {resp.status_code}"
                assert UNAVAILABLE_HEADLINE in resp.text
                assert "OPTION_CHAIN_EMPTY" in resp.text
                assert "No options exposure data available" in resp.text
                _assert_no_none_leak(resp.text)

    async def test_unknown_underlying_returns_200_with_reason_code(self) -> None:
        """An unresolvable underlying was previously swallowed into a silent 200."""
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(ROUTE, params={"symbol": "NIFTY"})
            assert resp.status_code == 200
            assert UNAVAILABLE_HEADLINE in resp.text
            assert "UNDERLYING_NOT_PRESENT_IN_MARKET_INDICES" in resp.text
            _assert_no_none_leak(resp.text)

    async def test_populated_dataset_renders_real_values(self) -> None:
        await _seed_single_call_contract()
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                ROUTE, params={"symbol": "NIFTY", "trade_date": "2026-08-10"}
            )
            assert resp.status_code == 200
            assert UNAVAILABLE_HEADLINE not in resp.text
            # Real computed value, byte-for-byte as the service produced it.
            assert "4239265625.00" in resp.text
            assert "HIGH" in resp.text
            # The only-side-seeded dataset reports its real warning.
            assert "OPTION_CHAIN_MISSING_PUT_SIDE" in resp.text
            _assert_no_none_leak(resp.text)

    async def test_point_in_time_excludes_future_available_at(self) -> None:
        """A record not yet available at ``as_of`` must not be visible (stale/absent)."""
        await _seed_single_call_contract(available_at=date(2026, 8, 11))
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(
                ROUTE,
                params={
                    "symbol": "NIFTY",
                    "trade_date": "2026-08-10",
                    "as_of_date": "2026-08-10",
                },
            )
            assert resp.status_code == 200
            assert UNAVAILABLE_HEADLINE in resp.text
            assert "OPTION_CHAIN_EMPTY" in resp.text
            _assert_no_none_leak(resp.text)

    async def test_unauthenticated_request_is_redirected_to_login(self) -> None:
        await seed_builtins()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        try:
            resp = await client.get(ROUTE)
        finally:
            await client.aclose()
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("location", "")

    @pytest.mark.parametrize(
        "params",
        [
            {"trade_date": "not-a-date"},
            {"trade_date": "2026-13-45"},
            {"as_of_date": "2026-99-99"},
            {"expiry_date": "yesterday"},
        ],
    )
    async def test_invalid_query_parameters_are_rejected_not_500(
        self, params: dict[str, str]
    ) -> None:
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(ROUTE, params=params)
            assert resp.status_code == 422, f"{params} -> {resp.status_code}"

    async def test_response_is_the_expected_html_partial(self) -> None:
        await _seed_index_only()
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(ROUTE, params={"symbol": "NIFTY"})
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/html")
            assert 'id="advanced-exposure-container"' in resp.text
            assert "Exposure Analytics Disclaimer" in resp.text
            assert "Strike Exposure & GEX Breakdown" in resp.text

    async def test_unexpected_error_is_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real programming error must stay visible, not be masked as 'no data'."""
        from app.services.advanced_exposure_service import AdvancedExposureService

        async def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("injected-programming-error")

        monkeypatch.setattr(
            AdvancedExposureService, "get_exposure_snapshot", _boom, raising=True
        )
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            with pytest.raises(RuntimeError, match="injected-programming-error"):
                await client.get(ROUTE, params={"symbol": "NIFTY"})

    async def test_expected_value_error_still_renders_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The narrow handler must keep the expected failure rendering, not raising."""
        from app.services.advanced_exposure_service import AdvancedExposureService

        async def _raise_value_error(*_args: Any, **_kwargs: Any) -> Any:
            raise ValueError("Unknown underlying symbol 'NIFTY'")

        monkeypatch.setattr(
            AdvancedExposureService,
            "get_exposure_snapshot",
            _raise_value_error,
            raising=True,
        )
        await seed_builtins()
        async with auth_client(is_superuser=True, browser=True) as client:
            resp = await client.get(ROUTE, params={"symbol": "NIFTY"})
        assert resp.status_code == 200
        assert UNAVAILABLE_HEADLINE in resp.text
        assert "UNDERLYING_NOT_PRESENT_IN_MARKET_INDICES" in resp.text
        _assert_no_none_leak(resp.text)
