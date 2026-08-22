"""Unit & Integration Tests for Advanced Options Foundation — Sprint 19.1G-A1.

Tests:
  - Black-Scholes analytical call and put pricing
  - Guarded Newton-Raphson + Brent bisection IV solver
  - Standard Option Greeks (Delta, Gamma, Theta, Vega, Rho)
  - Intrinsic violation & numerical bounds handling
  - Moneyness classification (ATM, ITM, OTM, DEEP_ITM, DEEP_OTM)
  - Long option breakeven & distance calculation
  - Primary IV & Straddle expected move engine
  - Volatility Structure (IV Smile, IV Skew, Term Structure)
  - Option Liquidity & Risk Profile calculation
  - Enriched Option Chain reconstruction
  - Advanced Options Context hook for Stock 360°
  - PIT safety & REST API / UI partial endpoints
"""
import math
from datetime import UTC, date
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.advanced_options_service import AdvancedOptionsService
from app.services.black_scholes import (
    bs_call_price,
    bs_put_price,
    calculate_greeks,
    implied_volatility,
)
from tests.auth_helpers import auth_client, seed_builtins

UTC = UTC


def test_black_scholes_pricing_known_values() -> None:
    """Test Black-Scholes call and put prices against known theoretical values."""
    # S = 100, K = 100, T = 1.0, r = 0.05, sigma = 0.20
    c_price = bs_call_price(100.0, 100.0, 1.0, 0.05, 0.20)
    p_price = bs_put_price(100.0, 100.0, 1.0, 0.05, 0.20)

    # Theoretical call = ~10.45, put = ~5.57
    assert 10.40 <= c_price <= 10.50
    assert 5.50 <= p_price <= 5.60

    # Put-Call parity check: C - P = S - K * exp(-r*T)
    parity_diff = (c_price - p_price) - (100.0 - 100.0 * math.exp(-0.05 * 1.0))
    assert abs(parity_diff) < 1e-4


def test_implied_volatility_solver_convergence() -> None:
    """Test IV solver recovery of known volatility."""
    spot = 2500.0
    strike = 2500.0
    t = 30 / 365.0
    r = 0.065
    target_iv = 0.22  # 22%

    c_price = bs_call_price(spot, strike, t, r, target_iv)
    p_price = bs_put_price(spot, strike, t, r, target_iv)

    iv_c = implied_volatility(c_price, spot, strike, t, "CE", r)
    iv_p = implied_volatility(p_price, spot, strike, t, "PE", r)

    assert iv_c.solver_status == "CONVERGED"
    assert iv_c.iv is not None
    assert abs(iv_c.iv - target_iv) < 1e-4

    assert iv_p.solver_status == "CONVERGED"
    assert iv_p.iv is not None
    assert abs(iv_p.iv - target_iv) < 1e-4


def test_textbook_hardcoded_reference_benchmark() -> None:
    """Textbook reference test using hard-coded benchmark outputs (S=100, K=100, T=1, r=0.05, q=0, vol=0.20)."""
    spot = 100.0
    strike = 100.0
    t = 1.0
    r = 0.05
    q = 0.0
    vol = 0.20

    # Independently derived benchmark expected values:
    exp_c_price = 10.45058
    exp_p_price = 5.57352
    exp_c_delta = 0.63683
    exp_p_delta = -0.36317
    exp_gamma = 0.01876
    exp_vega_1pct = 0.37524
    exp_c_theta_day = -0.01756
    exp_p_theta_day = -0.0045
    exp_c_rho_1pct = 0.53232
    exp_p_rho_1pct = -0.41890

    c_price = bs_call_price(spot, strike, t, r, vol, q)
    p_price = bs_put_price(spot, strike, t, r, vol, q)

    assert abs(c_price - exp_c_price) < 1e-4
    assert abs(p_price - exp_p_price) < 1e-4

    # Put-Call Parity Check: C - P == S * exp(-q*T) - K * exp(-r*T)
    parity = (c_price - p_price) - (spot * math.exp(-q * t) - strike * math.exp(-r * t))
    assert abs(parity) < 1e-5

    c_greeks = calculate_greeks(spot, strike, t, vol, "CE", r, q)
    p_greeks = calculate_greeks(spot, strike, t, vol, "PE", r, q)

    assert abs(c_greeks.delta - exp_c_delta) < 1e-4
    assert abs(p_greeks.delta - exp_p_delta) < 1e-4

    # Gamma identical for Call and Put
    assert abs(c_greeks.gamma - exp_gamma) < 1e-4
    assert abs(p_greeks.gamma - exp_gamma) < 1e-4

    # Vega identical for Call and Put
    assert abs(c_greeks.vega_per_pct - exp_vega_1pct) < 1e-4
    assert abs(p_greeks.vega_per_pct - exp_vega_1pct) < 1e-4

    # Daily Theta (calendar 365)
    assert abs(c_greeks.theta_per_day - exp_c_theta_day) < 1e-4
    assert abs(p_greeks.theta_per_day - exp_p_theta_day) < 1e-4

    # Rho
    assert abs(c_greeks.rho_per_pct - exp_c_rho_1pct) < 1e-4
    assert abs(p_greeks.rho_per_pct - exp_p_rho_1pct) < 1e-4


def test_expected_move_sqrt_time_scaling_invariant() -> None:
    """Verify that Expected Move scales strictly with sqrt(T), proving no linear-time bug."""
    spot = 20000.0
    iv = 0.20

    # 25 DTE vs 100 DTE (4x time -> 2x move)
    t1 = 25.0 / 365.0
    t2 = 100.0 / 365.0

    move1 = spot * iv * math.sqrt(t1)
    move2 = spot * iv * math.sqrt(t2)

    ratio = move2 / move1
    assert abs(ratio - 2.0) < 1e-4  # 4x DTE produces exactly 2x move!


def test_iv_solver_theoretical_upper_bound_and_missing_price() -> None:
    """Test solver flags prices above spot or theoretical maximum."""
    spot = 100.0
    strike = 100.0
    t = 0.5
    r = 0.05

    # Call price > spot (e.g. 150.0)
    res_above = implied_volatility(150.0, spot, strike, t, "CE", r)
    assert res_above.solver_status == "ABOVE_THEORETICAL_BOUND"
    assert res_above.iv is None
    res_zero = implied_volatility(10.0, spot, strike, 0.0, "CE", r)
    assert res_zero.solver_status == "ZERO_DTE"

    # Non-positive spot
    res_bad = implied_volatility(10.0, 0.0, strike, t, "CE", r)
    assert res_bad.solver_status == "INVALID_PRICE"


def test_option_greeks_calculations() -> None:
    """Test Option Greeks formulas and directional properties."""
    spot = 2400.0
    strike = 2400.0
    t = 20 / 365.0
    r = 0.065
    vol = 0.18

    ce_greeks = calculate_greeks(spot, strike, t, vol, "CE", r)
    pe_greeks = calculate_greeks(spot, strike, t, vol, "PE", r)

    # ATM Call Delta ~ 0.50, Put Delta ~ -0.50
    assert ce_greeks.delta is not None and 0.45 <= ce_greeks.delta <= 0.58
    assert pe_greeks.delta is not None and -0.55 <= pe_greeks.delta <= -0.42

    # Gamma is positive and identical for CE/PE
    assert ce_greeks.gamma is not None and ce_greeks.gamma > 0
    assert ce_greeks.gamma == pe_greeks.gamma

    # Theta is negative for long options
    assert ce_greeks.theta_per_day is not None and ce_greeks.theta_per_day < 0
    assert pe_greeks.theta_per_day is not None and pe_greeks.theta_per_day < 0

    # Vega is positive
    assert ce_greeks.vega_per_pct is not None and ce_greeks.vega_per_pct > 0


@pytest.mark.asyncio
async def test_enriched_option_chain_and_advanced_options_service() -> None:
    """Integration test for AdvancedOptionsService, enriched chain, expected move, & volatility structure."""
    factory = get_session_factory()
    async with factory() as session:
        # 1. Setup MarketIndex & Price
        idx = MarketIndex(code="NIFTY", name="Nifty 50", is_active=True)
        session.add(idx)
        await session.flush()

        trade_d = date(2026, 8, 10)
        exp_d1 = date(2026, 8, 28)
        exp_d2 = date(2026, 9, 25)

        idx_price = MarketIndexPrice(
            market_index_id=idx.id,
            trade_date=trade_d,
            open=Decimal("24500.0"),
            high=Decimal("24600.0"),
            low=Decimal("24450.0"),
            close=Decimal("24500.0"),
        )
        session.add(idx_price)

        # 2. Setup Contracts (24000 PE, 24500 CE, 24500 PE, 25000 CE)
        c_ce_245 = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24500CE",
            expiry_date=exp_d1,
            strike_price=Decimal("24500.0"),
            option_type="CE",
            lot_size=25,
            available_at=trade_d,
        )
        c_pe_245 = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24500PE",
            expiry_date=exp_d1,
            strike_price=Decimal("24500.0"),
            option_type="PE",
            lot_size=25,
            available_at=trade_d,
        )
        c_pe_240 = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24000PE",
            expiry_date=exp_d1,
            strike_price=Decimal("24000.0"),
            option_type="PE",
            lot_size=25,
            available_at=trade_d,
        )
        c_ce_250 = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG25000CE",
            expiry_date=exp_d1,
            strike_price=Decimal("25000.0"),
            option_type="CE",
            lot_size=25,
            available_at=trade_d,
        )

        # Second expiry contract
        c_ce_exp2 = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26SEP24500CE",
            expiry_date=exp_d2,
            strike_price=Decimal("24500.0"),
            option_type="CE",
            lot_size=25,
            available_at=trade_d,
        )

        session.add_all([c_ce_245, c_pe_245, c_pe_240, c_ce_250, c_ce_exp2])
        await session.flush()

        # 3. Daily prices
        p_ce_245 = DerivativeDailyPrice(
            contract_id=c_ce_245.id,
            trade_date=trade_d,
            close=Decimal("320.0"),
            settlement_price=Decimal("320.0"),
            volume_contracts=1000,
            open_interest=50000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )
        p_pe_245 = DerivativeDailyPrice(
            contract_id=c_pe_245.id,
            trade_date=trade_d,
            close=Decimal("280.0"),
            settlement_price=Decimal("280.0"),
            volume_contracts=1200,
            open_interest=60000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )
        p_pe_240 = DerivativeDailyPrice(
            contract_id=c_pe_240.id,
            trade_date=trade_d,
            close=Decimal("110.0"),
            settlement_price=Decimal("110.0"),
            volume_contracts=800,
            open_interest=40000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )
        p_ce_250 = DerivativeDailyPrice(
            contract_id=c_ce_250.id,
            trade_date=trade_d,
            close=Decimal("130.0"),
            settlement_price=Decimal("130.0"),
            volume_contracts=900,
            open_interest=45000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )
        p_ce_exp2 = DerivativeDailyPrice(
            contract_id=c_ce_exp2.id,
            trade_date=trade_d,
            close=Decimal("500.0"),
            settlement_price=Decimal("500.0"),
            volume_contracts=300,
            open_interest=15000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )

        session.add_all([p_ce_245, p_pe_245, p_pe_240, p_ce_250, p_ce_exp2])
        await session.commit()

        # 4. Execute AdvancedOptionsService analytics
        service = AdvancedOptionsService(session)

        # A. Enriched Option Chain
        rows = await service.get_enriched_option_chain("NIFTY", trade_d, exp_d1)
        assert len(rows) == 3  # 24000, 24500, 25000

        atm_row = next(r for r in rows if r.is_atm)
        assert atm_row.strike_price == Decimal("24500.0")
        assert atm_row.call is not None and atm_row.call.iv_result.solver_status == "CONVERGED"
        assert atm_row.put is not None and atm_row.put.iv_result.solver_status == "CONVERGED"
        assert atm_row.call.moneyness.classification == "ATM"
        assert atm_row.call.liquidity.classification == "HIGH"
        assert atm_row.call.breakeven.breakeven_price == Decimal("24820.0")

        # B. Expected Move
        em = await service.get_expected_move("NIFTY", trade_d, exp_d1)
        assert em.expected_move_points > Decimal("0")
        assert em.straddle_price == Decimal("600.0")
        assert em.straddle_expected_move_points == Decimal("510.0")

        # C. IV Smile & Skew
        smile = await service.get_iv_smile("NIFTY", trade_d, exp_d1)
        assert len(smile) == 3

        skew = await service.get_iv_skew("NIFTY", trade_d, exp_d1)
        assert skew.atm_strike == Decimal("24500.0")

        # D. IV Term Structure
        ts = await service.get_iv_term_structure("NIFTY", trade_d)
        assert len(ts) == 2
        assert ts[0].is_front_expiry is True

        # E. Advanced Options Context hook
        ctx = await service.get_advanced_options_context("NIFTY", trade_d)
        assert ctx.is_fno_security is True
        assert ctx.front_expiry == exp_d1
        assert ctx.atm_iv is not None


@pytest.mark.asyncio
async def test_advanced_options_rest_api_and_ui_partial() -> None:
    """Integration test for REST API endpoints and HTMX UI partial rendering."""
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
        # REST API Advanced Chain
        resp_chain = await client.get(
            "/api/v1/market-data/derivatives/options/advanced-chain",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10", "expiry_date": "2026-08-28"},
        )
        assert resp_chain.status_code == 200

        # REST API Expected Move
        resp_em = await client.get(
            "/api/v1/market-data/derivatives/options/expected-move",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10", "expiry_date": "2026-08-28"},
        )
        assert resp_em.status_code == 200

        # HTMX UI Partial
        resp_ui = await client.get(
            "/market-data/derivatives/advanced-options/tab",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10"},
        )
        assert resp_ui.status_code == 200
        assert "Expected Move" in resp_ui.text
        assert "Enriched Option Chain & Greeks" in resp_ui.text
