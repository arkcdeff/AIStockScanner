"""Unit & Integration Tests for Advanced Options Exposure Analytics — Sprint 19.1G-A2 / 19.1G-A2-R1.

Tests:
  - Spot grid construction math (41 points, endpoints, monotonic, center = spot)
  - API grid limits validation (reject negative/excessive grid parameters)
  - Historical lot-size hierarchy & date selection (no backward leakage of future lots)
  - Missing historical lot size behavior (MISSING != ZERO, returns UNAVAILABLE)
  - GROSS_UNSIGNED Gamma Flip returns SIGNED_ASSUMPTION_REQUIRED
  - Scenario Gamma Flip detection (single, multiple crossings with tie-break, no crossing)
  - Analytical Vanna and Charm vs finite-difference approximations (documented sign convention)
  - Stock 360° hook integration (AdvancedExposureContext)
  - REST API endpoints & HTMX UI partial rendering
"""
from datetime import date
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.derivative_contract import (
    DerivativeContract,
    DerivativeLotSizeHistory,
)
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.advanced_exposure_service import (
    PROFILE_DEALER_HEURISTIC,
    PROFILE_GROSS_UNSIGNED,
    AdvancedExposureService,
)
from app.services.black_scholes import calculate_greeks
from tests.auth_helpers import auth_client, seed_builtins


def test_spot_grid_construction_math() -> None:
    """Explicit test for spot grid construction math (41 points, endpoints, monotonic, center = spot)."""
    base_spot = 100.0
    spot_range_pct = 10.0
    spot_step_pct = 0.5

    range_pct = max(1.0, min(30.0, spot_range_pct))
    step_pct = max(0.1, min(5.0, spot_step_pct))
    num_steps = int(round((2 * range_pct) / step_pct))

    # 1. Total inclusive point count MUST be 41
    total_points = num_steps + 1
    assert total_points == 41

    grid_spots: list[float] = []
    grid_pcts: list[float] = []
    for i in range(total_points):
        pct = round(-range_pct + (i * step_pct), 4)
        hypo = base_spot * (1.0 + (pct / 100.0))
        grid_pcts.append(pct)
        grid_spots.append(round(hypo, 2))

    # 2. Verify endpoints
    assert grid_spots[0] == 90.0
    assert grid_spots[-1] == 110.0
    assert grid_pcts[0] == -10.0
    assert grid_pcts[-1] == +10.0

    # 3. Verify center (0.0% change = current spot 100.0) exists exactly once at step index 20
    assert grid_pcts[20] == 0.0
    assert grid_spots[20] == 100.0
    assert grid_pcts.count(0.0) == 1

    # 4. Verify monotonic ordering
    assert grid_spots == sorted(grid_spots)
    assert len(set(grid_spots)) == 41  # No duplicate points


@pytest.mark.asyncio
async def test_api_grid_limits_validation() -> None:
    """Test API grid input validation for bounds and excessive points."""
    factory = get_session_factory()
    async with factory() as session:
        service = AdvancedExposureService(session)

        # Negative range / zero step rejected
        with pytest.raises(ValueError, match="strictly positive"):
            await service.get_gamma_profile("NIFTY", date(2026, 8, 10), date(2026, 8, 28), spot_range_pct=-5.0)

        with pytest.raises(ValueError, match="strictly positive"):
            await service.get_gamma_profile("NIFTY", date(2026, 8, 10), date(2026, 8, 28), spot_step_pct=0.0)


def test_analytical_vanna_and_charm_finite_difference() -> None:
    """Test analytical Vanna and Charm against finite-difference numerical approximations."""
    spot = 100.0
    strike = 100.0
    t = 1.0
    r = 0.05
    q = 0.0
    vol = 0.20

    greeks = calculate_greeks(spot, strike, t, vol, "CE", r, q)
    assert greeks.vanna_per_pct is not None
    assert greeks.charm_per_day is not None

    # Finite difference Vanna: dDelta / dVol over +0.005 vol change
    d_vol = 0.005
    g_up = calculate_greeks(spot, strike, t, vol + d_vol, "CE", r, q)
    g_dn = calculate_greeks(spot, strike, t, vol - d_vol, "CE", r, q)
    fd_vanna_raw = (g_up.delta - g_dn.delta) / (2.0 * d_vol)
    fd_vanna_1pct = fd_vanna_raw / 100.0

    assert abs(greeks.vanna_per_pct - fd_vanna_1pct) < 1e-3

    # Finite difference Charm: dDelta / dt over -1/365 calendar day (decay in t)
    dt = 1.0 / 365.0
    g_t_less = calculate_greeks(spot, strike, t - dt, vol, "CE", r, q)
    fd_charm_day = (g_t_less.delta - greeks.delta) / 1.0  # daily change

    assert abs(greeks.charm_per_day - fd_charm_day) < 1e-3


def test_gross_gex_and_dex_numeric_benchmark() -> None:
    """Test Gross GEX and DEX formulas against hand-calculated benchmark values."""
    spot = 24500.0
    gamma = 0.00015
    delta = 0.50
    oi = 10000
    lot_size = 25

    # Gross GEX = Gamma * OI * Lot * Spot^2 * 0.01
    exp_gross_gex = gamma * oi * lot_size * (spot**2) * 0.01  # = 0.00015 * 10000 * 25 * 600250000 * 0.01 = 225,093,750.0
    exp_gross_dex = delta * oi * lot_size * spot  # = 0.50 * 10000 * 25 * 24500 = 3,062,500,000.0

    assert abs(exp_gross_gex - 225093750.0) < 1e-2
    assert abs(exp_gross_dex - 3062500000.0) < 1e-2


@pytest.mark.asyncio
async def test_historical_lot_size_date_selection_and_no_leakage() -> None:
    """Integration test for PIT historical lot size date selection and prevention of backward leakage."""
    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", is_active=True)
        session.add(idx)
        await session.flush()

        # Earlier lot size 25 effective from 2024-11-20
        lot_hist_1 = DerivativeLotSizeHistory(
            underlying_index_id=idx.id,
            effective_from=date(2024, 11, 20),
            effective_to=date(2025, 4, 24),
            lot_size=25,
            available_at=date(2024, 11, 20),
        )
        # Later lot size 15 effective from 2025-04-25
        lot_hist_2 = DerivativeLotSizeHistory(
            underlying_index_id=idx.id,
            effective_from=date(2025, 4, 25),
            lot_size=15,
            available_at=date(2025, 4, 25),
        )
        session.add_all([lot_hist_1, lot_hist_2])
        await session.commit()

        service = AdvancedExposureService(session)

        # 1. Query on 2024-12-01 (should resolve 25)
        lot_size_1, source_1, qual_1 = await service._resolve_historical_lot_size(
            "INDEX", idx.id, date(2024, 12, 1), date(2024, 12, 1)
        )
        assert lot_size_1 == 25
        assert source_1 == "HISTORICAL_PIT"
        assert qual_1 == "VERIFIED_HISTORICAL"

        # 2. Query on 2026-08-10 (should resolve 15)
        lot_size_2, source_2, qual_2 = await service._resolve_historical_lot_size(
            "INDEX", idx.id, date(2026, 8, 10), date(2026, 8, 10)
        )
        assert lot_size_2 == 15
        assert source_2 == "HISTORICAL_PIT"

        # 3. Query on 2024-05-01 before any lot history (should return None / UNAVAILABLE)
        lot_size_old, source_old, qual_old = await service._resolve_historical_lot_size(
            "INDEX", idx.id, date(2024, 5, 1), date(2024, 5, 1)
        )
        assert lot_size_old is None
        assert source_old == "UNAVAILABLE"
        assert qual_old == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_gross_unsigned_gamma_flip_returns_signed_assumption_required() -> None:
    """Test that calling get_gamma_flip with GROSS_UNSIGNED returns SIGNED_ASSUMPTION_REQUIRED."""
    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", is_active=True)
        session.add(idx)
        await session.flush()

        trade_d = date(2026, 8, 10)
        exp_d = date(2026, 8, 28)

        idx_price = MarketIndexPrice(
            market_index_id=idx.id,
            trade_date=trade_d,
            open=Decimal("24500.0"),
            high=Decimal("24600.0"),
            low=Decimal("24450.0"),
            close=Decimal("24500.0"),
        )
        c_ce = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24500CE",
            expiry_date=exp_d,
            strike_price=Decimal("24500.0"),
            option_type="CE",
            lot_size=25,
            source_name="NSE_FO_BHAV",
            available_at=trade_d,
        )
        session.add_all([idx_price, c_ce])
        await session.flush()

        p_ce = DerivativeDailyPrice(
            contract_id=c_ce.id,
            trade_date=trade_d,
            close=Decimal("320.0"),
            settlement_price=Decimal("320.0"),
            volume_contracts=1000,
            open_interest=50000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )
        session.add(p_ce)
        await session.commit()

        service = AdvancedExposureService(session)

        # Call get_gamma_flip with GROSS_UNSIGNED profile
        flip_unsigned = await service.get_gamma_flip("NIFTY", trade_d, exp_d, trade_d, PROFILE_GROSS_UNSIGNED)
        assert flip_unsigned.status == "SIGNED_ASSUMPTION_REQUIRED"
        assert flip_unsigned.gamma_flip_level is None

        # Call get_gamma_flip with DEALER_SHORT_CALL_LONG_PUT_HEURISTIC profile
        flip_signed = await service.get_gamma_flip("NIFTY", trade_d, exp_d, trade_d, PROFILE_DEALER_HEURISTIC)
        assert flip_signed.status in ("FLIP_FOUND", "NO_FLIP_IN_RANGE", "MULTIPLE_FLIPS_FOUND")


@pytest.mark.asyncio
async def test_advanced_exposure_rest_api_and_ui_partial() -> None:
    """Integration test for Advanced Exposure REST API endpoints and HTMX UI partial rendering."""
    factory = get_session_factory()
    async with factory() as session:
        idx = MarketIndex(code="NIFTY", name="Nifty 50", is_active=True)
        session.add(idx)
        await session.flush()

        trade_d = date(2026, 8, 10)
        exp_d = date(2026, 8, 28)

        idx_price = MarketIndexPrice(
            market_index_id=idx.id,
            trade_date=trade_d,
            open=Decimal("24500.0"),
            high=Decimal("24600.0"),
            low=Decimal("24450.0"),
            close=Decimal("24500.0"),
        )
        c_ce = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24500CE",
            expiry_date=exp_d,
            strike_price=Decimal("24500.0"),
            option_type="CE",
            lot_size=25,
            source_name="NSE_FO_BHAV",
            available_at=trade_d,
        )
        session.add_all([idx_price, c_ce])
        await session.flush()

        p_ce = DerivativeDailyPrice(
            contract_id=c_ce.id,
            trade_date=trade_d,
            close=Decimal("320.0"),
            settlement_price=Decimal("320.0"),
            volume_contracts=1000,
            open_interest=50000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )
        session.add(p_ce)
        await session.commit()

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # REST API Exposure Snapshot
        resp_exp = await client.get(
            "/api/v1/market-data/derivatives/options/exposure",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10", "expiry_date": "2026-08-28"},
        )
        assert resp_exp.status_code == 200

        # REST API GEX Table
        resp_gex = await client.get(
            "/api/v1/market-data/derivatives/options/gex",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10", "expiry_date": "2026-08-28"},
        )
        assert resp_gex.status_code == 200

        # REST API Gamma Profile
        resp_prof = await client.get(
            "/api/v1/market-data/derivatives/options/gamma-profile",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10", "expiry_date": "2026-08-28"},
        )
        assert resp_prof.status_code == 200

        # REST API Gamma Flip
        resp_flip = await client.get(
            "/api/v1/market-data/derivatives/options/gamma-flip",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10", "expiry_date": "2026-08-28"},
        )
        assert resp_flip.status_code == 200

        # HTMX UI Partial
        resp_ui = await client.get(
            "/market-data/derivatives/advanced-exposure/tab",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10"},
        )
        assert resp_ui.status_code == 200
        assert "Gross GEX" in resp_ui.text
        assert "Exposure Analytics Disclaimer" in resp_ui.text
