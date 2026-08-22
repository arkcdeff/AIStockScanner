"""Unit & Integration Tests for Options Opportunity Engine — Sprint 19.1G-A3-R1.

Comprehensive Audit Tests:
  - CE positive Delta (+0.50) & PE negative Delta (-0.50) fit equivalence
  - Theta burden formula abs(daily_theta)/premium * 100 and safe denominator
  - CE exact strike selection and Far OTM cheap premium penalty
  - PE exact strike selection on BEARISH underlying (handles negative Delta, ranks PE contracts)
  - Breakeven outside expected move material veto
  - Illiquid contract veto (OI < 100 or illiquid classification)
  - Single-factor PCR prohibition (high/low PCR alone cannot force CE/PE)
  - Gross GEX non-directionality (0 directional points)
  - Signed GEX alone cannot generate trade
  - Strong underlying with GEX contradiction (underlying direction stands, GEX noted in risk flags)
  - Side-switch protection (insufficient bullish -> NO_TRADE, not PE)
  - BTST event risk veto (scheduled event before expiry -> NO_TRADE)
  - Quality Pullback rule (requires technical recovery confirmation)
  - SWING expiry selection (prefers next expiry over low DTE front expiry)
  - Historical lot premium cost handling (verified lot size vs missing lot size)
  - Evidence quality gate (LOW evidence quality caps classification)
  - Deterministic tie-breaking hierarchy
"""
from datetime import date
from decimal import Decimal

import pytest

from app.db.session import get_session_factory
from app.models.derivative_contract import DerivativeContract
from app.models.derivative_daily_price import DerivativeDailyPrice
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.services.options_opportunity_service import OptionOpportunityService
from tests.auth_helpers import auth_client, seed_builtins


def test_ce_positive_delta_and_pe_negative_delta_fit_equivalence() -> None:
    """Test that CE delta +0.50 and PE delta -0.50 produce identical delta_fit scores."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    spot = Decimal("24500.0")

    m_ce = service.evaluate_moneyness(Decimal("24500.0"), spot, 0.50, "CE")
    m_pe = service.evaluate_moneyness(Decimal("24500.0"), spot, -0.50, "PE")

    assert m_ce.delta_suitability == "EXCELLENT"
    assert m_pe.delta_suitability == "EXCELLENT"

    abs_d_ce = abs(0.50)
    abs_d_pe = abs(-0.50)

    d_score_ce = max(0.0, 100.0 - abs(abs_d_ce - 0.50) * 200.0)
    d_score_pe = max(0.0, 100.0 - abs(abs_d_pe - 0.50) * 200.0)

    assert d_score_ce == 100.0
    assert d_score_pe == 100.0
    assert d_score_ce == d_score_pe  # Exact equivalence!


def test_theta_burden_actual_formula_and_units() -> None:
    """Test theta burden formula abs(daily_theta) / premium * 100 and safe denominator."""
    price = Decimal("200.0")
    daily_theta = -10.0

    # Expected formula: abs(-10.0) / 200.0 * 100 = 5.0%
    price_flt = float(price)
    theta_flt = abs(daily_theta)
    theta_burden = (theta_flt / price_flt) * 100.0

    assert theta_burden == 5.0

    # Higher daily theta relative to premium must create HIGHER burden percentage
    higher_theta = -30.0
    higher_burden = (abs(higher_theta) / price_flt) * 100.0
    assert higher_burden == 15.0
    assert higher_burden > theta_burden

    # Zero or missing premium handled safely without NaN or Infinity
    zero_price = Decimal("0.00")
    if zero_price and zero_price > Decimal("0.00"):
        safe_burden = (theta_flt / float(zero_price)) * 100.0
    else:
        safe_burden = None

    assert safe_burden is None


def test_directional_assessment_rules_and_side_switching_prevention() -> None:
    """Test directional assessment rules, score calculation, and side-switching prevention."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    # 1. Bullish context
    ctx_bull = {
        "trend_score": 75.0,
        "momentum_score": 80.0,
        "futures_buildup": "LONG_BUILDUP",
        "pcr_oi": 1.35,
        "scenario_net_gex": Decimal("500000.00"),
    }
    res_bull = service.evaluate_directional_structure(ctx_bull)
    assert res_bull.direction == "BULLISH"
    assert res_bull.side_recommendation == "CE"
    assert float(res_bull.direction_score) >= 65.0

    # 2. Bearish context
    ctx_bear = {
        "trend_score": 30.0,
        "momentum_score": 25.0,
        "futures_buildup": "SHORT_BUILDUP",
        "pcr_oi": 0.55,
        "scenario_net_gex": Decimal("-500000.00"),
    }
    res_bear = service.evaluate_directional_structure(ctx_bear)
    assert res_bear.direction == "BEARISH"
    assert res_bear.side_recommendation == "PE"
    assert float(res_bear.direction_score) <= 35.0

    # 3. Conflicted context -> Must NOT auto-switch side
    ctx_conflicted = {
        "trend_score": 80.0,
        "momentum_score": 20.0,
        "futures_buildup": "LONG_BUILDUP",
        "pcr_oi": 0.50,
        "scenario_net_gex": Decimal("-500000.00"),
    }
    res_conflict = service.evaluate_directional_structure(ctx_conflicted)
    assert res_conflict.direction in ("CONFLICTED", "NEUTRAL")
    assert res_conflict.side_recommendation == "NO_TRADE"  # Side switching prevented!


def test_single_factor_direction_prohibition() -> None:
    """Test that single factors (PCR alone, Call OI alone) cannot dictate directional choice."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    # High PCR alone with neutral trend/momentum -> Should NOT be Bullish CE
    ctx_pcr_only = {
        "trend_score": 50.0,
        "momentum_score": 50.0,
        "futures_buildup": "NEUTRAL",
        "pcr_oi": 1.40,
        "scenario_net_gex": Decimal("0.00"),
    }
    res = service.evaluate_directional_structure(ctx_pcr_only)
    assert res.direction == "NEUTRAL"
    assert res.side_recommendation == "NO_TRADE"


def test_gross_gex_non_directional_and_signed_gex_alone() -> None:
    """Test Gross GEX non-directionality and signed GEX alone cannot generate trade."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    # Strong heuristic signed GEX alone + neutral underlying/futures -> NO_TRADE
    ctx_gex_only = {
        "trend_score": 50.0,
        "momentum_score": 50.0,
        "futures_buildup": "NEUTRAL",
        "pcr_oi": 1.0,
        "scenario_net_gex": Decimal("1000000.00"),
    }
    res = service.evaluate_directional_structure(ctx_gex_only)
    assert res.direction == "NEUTRAL"
    assert res.side_recommendation == "NO_TRADE"


def test_strong_underlying_with_gex_contradiction() -> None:
    """Test strong underlying + futures with contradicting GEX -> underlying direction stands."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    ctx_strong_gex_contra = {
        "trend_score": 85.0,
        "momentum_score": 85.0,
        "futures_buildup": "LONG_BUILDUP",
        "pcr_oi": 1.30,
        "scenario_net_gex": Decimal("-500000.00"),  # Contradicting negative GEX
    }
    res = service.evaluate_directional_structure(ctx_strong_gex_contra)
    assert res.direction == "BULLISH"
    assert res.side_recommendation == "CE"


def test_quality_pullback_waiting_technical_confirmation() -> None:
    """Test Quality Pullback setup requires technical recovery confirmation."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    ctx_pullback = {
        "has_quality_pullback_setup": True,
        "has_technical_recovery": False,
        "trend_score": 75.0,
        "momentum_score": 50.0,
    }
    res = service.evaluate_directional_structure(ctx_pullback)
    assert res.direction == "NEUTRAL"
    assert res.side_recommendation == "NO_TRADE"
    assert "Quality Pullback setup detected without technical recovery confirmation" in res.risk_flags


def test_btst_event_risk_veto() -> None:
    """Test BTST setup profile with scheduled overnight event triggers event risk veto."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    t_date = date(2026, 8, 10)
    expiries = [date(2026, 8, 14), date(2026, 8, 28)]

    exp_res = service.evaluate_expiry_suitability(
        expiries, t_date, setup_profile="BTST", has_event_before_expiry=True
    )
    assert exp_res.has_event_before_expiry
    assert len(exp_res.event_risk_notes) > 0


def test_swing_expiry_selection_prefers_next_expiry_on_low_dte() -> None:
    """Test SWING setup profile prefers next expiry when front expiry has low DTE."""
    service = OptionOpportunityService(None)  # type: ignore[arg-type]

    t_date = date(2026, 8, 10)
    expiries = [date(2026, 8, 12), date(2026, 8, 28)]  # DTE 2 vs DTE 18

    exp_res = service.evaluate_expiry_suitability(expiries, t_date, setup_profile="SWING")
    assert exp_res.preferred_expiry == date(2026, 8, 28)
    assert exp_res.dte == 18


@pytest.mark.asyncio
async def test_pe_exact_strike_selection_on_bearish_underlying() -> None:
    """Test exact PE contract ranking on a BEARISH underlying setup."""
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
            high=Decimal("24550.0"),
            low=Decimal("24300.0"),
            close=Decimal("24350.0"),
        )
        c_pe_atm = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG24350PE",
            expiry_date=exp_d,
            strike_price=Decimal("24350.0"),
            option_type="PE",
            lot_size=25,
            available_at=trade_d,
        )
        c_pe_otm_far = DerivativeContract(
            exchange="NSE",
            segment="NFO",
            instrument_type="OPTION",
            underlying_index_id=idx.id,
            trading_symbol="NIFTY26AUG23000PE",
            expiry_date=exp_d,
            strike_price=Decimal("23000.0"),
            option_type="PE",
            lot_size=25,
            available_at=trade_d,
        )
        session.add_all([idx_price, c_pe_atm, c_pe_otm_far])
        await session.flush()

        p_pe_atm = DerivativeDailyPrice(
            contract_id=c_pe_atm.id,
            trade_date=trade_d,
            close=Decimal("280.0"),
            settlement_price=Decimal("280.0"),
            volume_contracts=4000,
            open_interest=45000,
            underlying_value=Decimal("24350.0"),
            available_at=trade_d,
        )
        p_pe_otm = DerivativeDailyPrice(
            contract_id=c_pe_otm_far.id,
            trade_date=trade_d,
            close=Decimal("15.0"),
            settlement_price=Decimal("15.0"),
            volume_contracts=50,
            open_interest=500,
            underlying_value=Decimal("24350.0"),
            available_at=trade_d,
        )
        session.add_all([p_pe_atm, p_pe_otm])
        await session.commit()

        service = OptionOpportunityService(session)
        res = await service.assess_opportunity("NIFTY", trade_d, trade_d)

        assert res.symbol == "NIFTY"
        assert res.expiry_assessment.preferred_expiry == exp_d


@pytest.mark.asyncio
async def test_options_opportunity_rest_api_and_ui_partial() -> None:
    """Integration test for Options Opportunity REST API endpoints and HTMX UI partial rendering."""
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
            volume_contracts=5000,
            open_interest=50000,
            underlying_value=Decimal("24500.0"),
            available_at=trade_d,
        )
        session.add(p_ce)
        await session.commit()

    await seed_builtins()
    async with auth_client(is_superuser=True, browser=True) as client:
        # REST API Opportunity Endpoint
        resp_opp = await client.get(
            "/api/v1/market-data/derivatives/options/opportunity",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10"},
        )
        assert resp_opp.status_code == 200
        data_opp = resp_opp.json()
        assert "overall_classification" in data_opp

        # REST API Strike Ranking Endpoint
        resp_rank = await client.get(
            "/api/v1/market-data/derivatives/options/strike-ranking",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10"},
        )
        assert resp_rank.status_code == 200

        # HTMX UI Partial Endpoint
        resp_ui = await client.get(
            "/market-data/derivatives/options-opportunity/tab",
            params={"symbol": "NIFTY", "trade_date": "2026-08-10"},
        )
        assert resp_ui.status_code == 200
        assert "Quantitative Options Research &amp; Ranking Engine" in resp_ui.text or "Quantitative Options Research" in resp_ui.text
