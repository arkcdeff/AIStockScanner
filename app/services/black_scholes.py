"""Black-Scholes & Option Greeks Quantitative Engine — Sprint 19.1G-A1.

Provides pure mathematical functions for:
  - Analytical Black-Scholes Call and Put pricing
  - Guarded Newton-Raphson + Brent bisection Implied Volatility (IV) solver
  - Standard Option Greeks (Delta, Gamma, Theta, Vega, Rho)
  - Extreme IV, zero DTE, and intrinsic violation numerical guards
"""
from __future__ import annotations

import math

from app.schemas.advanced_options import OptionGreeksRead, OptionIVResult


# Standard Normal Cumulative Distribution Function N(x) using math.erf
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

# Standard Normal Probability Density Function n(x)
def _norm_pdf(x: float) -> float:
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)

# Standard risk-free rate default (6.5% p.a. repo / T-Bill benchmark)
DEFAULT_RISK_FREE_RATE = 0.065
MAX_IV_SOLVER_ITERATIONS = 100
IV_SOLVER_TOLERANCE = 1e-5
MIN_IV_BOUND = 1e-4      # 0.01%
MAX_IV_BOUND = 5.0       # 500%


def bs_call_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    volatility: float = 0.20,
    dividend_yield: float = 0.0,
) -> float:
    """Calculate Black-Scholes price for European Call Option."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if time_to_expiry <= 0:
        return max(0.0, spot - strike)
    if volatility <= 0:
        discounted_fwd = spot * math.exp(-dividend_yield * time_to_expiry) - strike * math.exp(-risk_free_rate * time_to_expiry)
        return max(0.0, discounted_fwd)

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility**2) * time_to_expiry) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    call_price = spot * math.exp(-dividend_yield * time_to_expiry) * _norm_cdf(d1) - strike * math.exp(-risk_free_rate * time_to_expiry) * _norm_cdf(d2)
    return max(0.0, float(call_price))


def bs_put_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    volatility: float = 0.20,
    dividend_yield: float = 0.0,
) -> float:
    """Calculate Black-Scholes price for European Put Option."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if time_to_expiry <= 0:
        return max(0.0, strike - spot)
    if volatility <= 0:
        discounted_fwd = strike * math.exp(-risk_free_rate * time_to_expiry) - spot * math.exp(-dividend_yield * time_to_expiry)
        return max(0.0, discounted_fwd)

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility**2) * time_to_expiry) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    put_price = strike * math.exp(-risk_free_rate * time_to_expiry) * _norm_cdf(-d2) - spot * math.exp(-dividend_yield * time_to_expiry) * _norm_cdf(-d1)
    return max(0.0, float(put_price))


def bs_vega(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    volatility: float = 0.20,
    dividend_yield: float = 0.0,
) -> float:
    """Calculate Black-Scholes raw Vega (rate of change of price per unit volatility)."""
    if spot <= 0 or strike <= 0 or time_to_expiry <= 0 or volatility <= 0:
        return 0.0

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility**2) * time_to_expiry) / (volatility * sqrt_t)
    vega = spot * math.exp(-dividend_yield * time_to_expiry) * _norm_pdf(d1) * sqrt_t
    return max(0.0, float(vega))


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    option_type: str,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: float = 0.0,
) -> OptionIVResult:
    """Solve Black-Scholes Implied Volatility using Newton-Raphson with Brent fallback."""
    opt_type = option_type.upper()
    if opt_type not in ("CE", "PE"):
        return OptionIVResult(solver_status="INVALID_OPTION_TYPE", input_quality="INVALID_TYPE")

    if spot <= 0 or strike <= 0:
        return OptionIVResult(solver_status="INVALID_PRICE", input_quality="NON_POSITIVE_UNDERLYING")

    if time_to_expiry <= 0:
        return OptionIVResult(solver_status="ZERO_DTE", input_quality="EXPIRED")

    disc_strike = strike * math.exp(-risk_free_rate * time_to_expiry)
    disc_spot = spot * math.exp(-dividend_yield * time_to_expiry)
    intrinsic = max(0.0, disc_spot - disc_strike) if opt_type == "CE" else max(0.0, disc_strike - disc_spot)

    # Upper theoretical bound check (Call price cannot exceed spot, Put price cannot exceed discounted strike)
    max_theoretical = disc_spot if opt_type == "CE" else disc_strike
    if market_price > max_theoretical + 1e-4:
        return OptionIVResult(
            solver_status="ABOVE_THEORETICAL_BOUND",
            input_quality="ABOVE_SPOT_MAXIMUM" if opt_type == "CE" else "ABOVE_DISCOUNTED_STRIKE_MAXIMUM",
        )

    if market_price < intrinsic - 1e-4:
        return OptionIVResult(
            solver_status="INTRINSIC_VIOLATION",
            input_quality="BELOW_INTRINSIC",
        )

    price_fn = bs_call_price if opt_type == "CE" else bs_put_price

    # 1. Newton-Raphson Algorithm
    vol = 0.25  # Initial guess 25%
    for i in range(1, MAX_IV_SOLVER_ITERATIONS + 1):
        price = price_fn(spot, strike, time_to_expiry, risk_free_rate, vol, dividend_yield)
        diff = price - market_price

        if abs(diff) < IV_SOLVER_TOLERANCE:
            return OptionIVResult(
                iv=round(vol, 6),
                solver_status="CONVERGED",
                iterations=i,
                input_quality="VALID",
            )

        v = bs_vega(spot, strike, time_to_expiry, risk_free_rate, vol, dividend_yield)
        if v < 1e-8:
            break  # Switch to Bisection fallback

        step = diff / v
        vol -= step

        if vol <= MIN_IV_BOUND or vol >= MAX_IV_BOUND:
            break  # Out of bounds -> Switch to Bisection fallback

    # 2. Bisection Guarded Fallback
    low_vol = MIN_IV_BOUND
    high_vol = MAX_IV_BOUND

    # Check bounds
    p_low = price_fn(spot, strike, time_to_expiry, risk_free_rate, low_vol, dividend_yield)
    p_high = price_fn(spot, strike, time_to_expiry, risk_free_rate, high_vol, dividend_yield)

    if market_price <= p_low:
        return OptionIVResult(iv=round(low_vol, 6), solver_status="CONVERGED", iterations=MAX_IV_SOLVER_ITERATIONS, input_quality="LOW_BOUND_CLAMP")
    if market_price >= p_high:
        return OptionIVResult(iv=round(high_vol, 6), solver_status="CONVERGED", iterations=MAX_IV_SOLVER_ITERATIONS, input_quality="HIGH_BOUND_CLAMP")

    mid_vol = vol
    for i in range(1, 40):
        mid_vol = 0.5 * (low_vol + high_vol)
        p_mid = price_fn(spot, strike, time_to_expiry, risk_free_rate, mid_vol, dividend_yield)
        diff = p_mid - market_price

        if abs(diff) < IV_SOLVER_TOLERANCE:
            return OptionIVResult(
                iv=round(mid_vol, 6),
                solver_status="CONVERGED",
                iterations=MAX_IV_SOLVER_ITERATIONS + i,
                input_quality="BISECTION_FALLBACK",
            )

        if diff > 0:
            high_vol = mid_vol
        else:
            low_vol = mid_vol

    return OptionIVResult(
        iv=round(mid_vol, 6),
        solver_status="CONVERGED",
        iterations=MAX_IV_SOLVER_ITERATIONS + 40,
        input_quality="APPROXIMATED",
    )


def calculate_greeks(
    spot: float,
    strike: float,
    time_to_expiry: float,
    volatility: float | None,
    option_type: str,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    dividend_yield: float = 0.0,
) -> OptionGreeksRead:
    """Calculate Black-Scholes Option Greeks."""
    opt_type = option_type.upper()
    if (
        spot <= 0
        or strike <= 0
        or time_to_expiry <= 0
        or volatility is None
        or volatility <= 0
        or opt_type not in ("CE", "PE")
    ):
        # Default zero-time or invalid inputs
        if time_to_expiry <= 0:
            if opt_type == "CE":
                d = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
            else:
                d = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
            return OptionGreeksRead(delta=d, gamma=0.0, theta_per_day=0.0, vega_per_pct=0.0, rho_per_pct=0.0)
        return OptionGreeksRead()

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility**2) * time_to_expiry) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    pdf_d1 = _norm_pdf(d1)
    cdf_d1 = _norm_cdf(d1)
    cdf_d2 = _norm_cdf(d2)
    cdf_neg_d1 = _norm_cdf(-d1)
    cdf_neg_d2 = _norm_cdf(-d2)

    # 1. Delta
    if opt_type == "CE":
        delta = math.exp(-dividend_yield * time_to_expiry) * cdf_d1
    else:
        delta = -math.exp(-dividend_yield * time_to_expiry) * cdf_neg_d1

    # 2. Gamma (same for CE and PE)
    gamma = (math.exp(-dividend_yield * time_to_expiry) * pdf_d1) / (spot * volatility * sqrt_t)

    # 3. Vega (raw vega per 1 unit vol -> converted to per 1 percentage point = vega / 100)
    raw_vega = spot * math.exp(-dividend_yield * time_to_expiry) * pdf_d1 * sqrt_t
    vega_per_pct = raw_vega / 100.0

    # 4. Theta (annualized theta -> converted to per calendar day = theta / 365)
    term1 = -(spot * math.exp(-dividend_yield * time_to_expiry) * pdf_d1 * volatility) / (2.0 * sqrt_t)
    if opt_type == "CE":
        term2 = -risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry) * cdf_d2
        term3 = dividend_yield * spot * math.exp(-dividend_yield * time_to_expiry) * cdf_d1
        annual_theta = term1 + term2 + term3
    else:
        term2 = risk_free_rate * strike * math.exp(-risk_free_rate * time_to_expiry) * cdf_neg_d2
        term3 = -dividend_yield * spot * math.exp(-dividend_yield * time_to_expiry) * cdf_neg_d1
        annual_theta = term1 + term2 + term3
    theta_per_day = annual_theta / 365.0

    # 5. Rho (per 1 percentage point = rho / 100)
    if opt_type == "CE":
        raw_rho = strike * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * cdf_d2
    else:
        raw_rho = -strike * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * cdf_neg_d2
    rho_per_pct = raw_rho / 100.0

    # 6. Vanna (dDelta/dVol -> per 1 percentage point change in IV = vanna / 100)
    raw_vanna = -math.exp(-dividend_yield * time_to_expiry) * pdf_d1 * (d2 / volatility)
    vanna_per_pct = raw_vanna / 100.0

    # 7. Charm (dDelta/dt -> per calendar day = annual_charm / 365)
    common_charm = -math.exp(-dividend_yield * time_to_expiry) * pdf_d1 * (
        (2.0 * (risk_free_rate - dividend_yield) * time_to_expiry - d2 * volatility * sqrt_t)
        / (2.0 * time_to_expiry * volatility * sqrt_t)
    )
    if opt_type == "CE":
        annual_charm = dividend_yield * math.exp(-dividend_yield * time_to_expiry) * cdf_d1 + common_charm
    else:
        annual_charm = -dividend_yield * math.exp(-dividend_yield * time_to_expiry) * cdf_neg_d1 + common_charm
    charm_per_day = annual_charm / 365.0

    return OptionGreeksRead(
        delta=round(delta, 5),
        gamma=round(gamma, 6),
        theta_per_day=round(theta_per_day, 4),
        vega_per_pct=round(vega_per_pct, 4),
        rho_per_pct=round(rho_per_pct, 5),
        vanna_per_pct=round(vanna_per_pct, 6),
        charm_per_day=round(charm_per_day, 5),
    )
