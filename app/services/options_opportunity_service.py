"""Options Opportunity, Expiry, Moneyness & Exact Strike Selection Service — Sprint 19.1G-A3.

Architectural Order:
  1. Underlying Context & Directional Structure -> OptionDirectionalAssessment
  2. Research Side Selection -> CE, PE, or NO_TRADE (side-switching protection)
  3. Expiry Suitability & Ranking -> OptionExpiryAssessment
  4. Moneyness & Delta Assessment -> OptionMoneynessAssessment
  5. Exact Contract Ranking & Quality Scoring -> OptionStrikeAssessment
  6. Risk / Liquidity / Event Vetoes -> NO_TRADE or contract rejection
  7. Assessment Aggregation -> OptionOpportunityAssessment
"""
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.options_opportunity import (
    OptionDirectionalAssessment,
    OptionExpiryAssessment,
    OptionMoneynessAssessment,
    OptionOpportunityAssessment,
    OptionStrikeAssessment,
)

METHODOLOGY_VERSIONS = {
    "direction": "option_direction_v1",
    "expiry": "option_expiry_selection_v1",
    "moneyness": "option_moneyness_v1",
    "ranking": "option_strike_ranking_v1",
}


class OptionOpportunityService:
    """Service layer for evaluating options opportunities and ranking exact strikes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get_underlying_direction_context(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date,
    ) -> dict[str, Any]:
        """Fetch composite technical, trend, futures, news, macro, and exposure signals for underlying."""
        ctx: dict[str, Any] = {
            "symbol": symbol.upper(),
            "trend_score": 50.0,
            "momentum_score": 50.0,
            "futures_buildup": "NEUTRAL",
            "pcr_oi": 1.0,
            "fii_net_index_contracts": 0,
            "gross_gex": Decimal("0.00"),
            "scenario_net_gex": Decimal("0.00"),
            "gamma_flip_distance_pct": None,
            "has_earnings_event": False,
            "has_rbi_event": False,
            "is_index": False,
        }

        if self.session is not None:
            # Check if underlying is an index
            from app.services.historical_option_chain import HistoricalOptionChainService
            chain_svc = HistoricalOptionChainService(self.session)
            u_info = await chain_svc._resolve_underlying(symbol.upper())
            if u_info:
                ctx["is_index"] = (u_info[0] == "INDEX")

            # Load derivatives exposure context if available
            try:
                from app.services.advanced_exposure_service import AdvancedExposureService
                exp_svc = AdvancedExposureService(self.session)
                exp_ctx = await exp_svc.get_advanced_exposure_context(symbol, trade_date, as_of_date)
                if exp_ctx and exp_ctx.is_fno_security:
                    ctx["gross_gex"] = exp_ctx.gross_total_gex or Decimal("0.00")
                    ctx["scenario_net_gex"] = exp_ctx.scenario_net_gex or Decimal("0.00")
                    ctx["gamma_flip_distance_pct"] = exp_ctx.distance_to_gamma_flip_pct
            except Exception:
                pass

            # Load futures analytics context if available
            try:
                from app.services.futures_analytics import FuturesAnalyticsService
                fut_svc = FuturesAnalyticsService(self.session)
                fut_summary = await fut_svc.get_futures_summary(symbol, trade_date, as_of_date)
                if fut_summary and fut_summary.buildup_classification:
                    ctx["futures_buildup"] = fut_summary.buildup_classification
            except Exception:
                pass

            # Load options PCR context if available
            try:
                from app.services.options_analytics import OptionsAnalyticsService
                opt_svc = OptionsAnalyticsService(self.session)
                pcr = await opt_svc.get_pcr(symbol, trade_date, as_of_date)
                if pcr and pcr.pcr_oi is not None:
                    ctx["pcr_oi"] = float(pcr.pcr_oi)
            except Exception:
                pass

        # Load Stock 360 / Opportunity context for non-indices
        if not ctx["is_index"] and self.session is not None:
            try:
                from app.services.stock360_service import Stock360Service
                s360_svc = Stock360Service(self.session)
                s360 = await s360_svc.get_stock360(symbol, trade_date, as_of_date)
                if s360:
                    ctx["trend_score"] = float(getattr(s360, "trend_score", 50.0) or 50.0)
                    ctx["momentum_score"] = float(getattr(s360, "momentum_score", 50.0) or 50.0)
            except Exception:
                pass

        return ctx

    def evaluate_directional_structure(
        self,
        ctx: dict[str, Any],
    ) -> OptionDirectionalAssessment:
        """Evaluate multi-factor directional structure. No single factor dictates direction."""
        bull_points = 0.0
        bear_points = 0.0
        total_weight = 0.0

        supporting: list[str] = []
        contradicting: list[str] = []
        risk_flags: list[str] = []

        # Check Quality Pullback rule
        if ctx.get("has_quality_pullback_setup", False) and not ctx.get("has_technical_recovery", False):
            risk_flags.append("Quality Pullback setup detected without technical recovery confirmation")
            return OptionDirectionalAssessment(
                direction="NEUTRAL",
                side_recommendation="NO_TRADE",
                direction_score=Decimal("50.00"),
                evidence_quality="MEDIUM",
                supporting_evidence=["Strong fundamental quality"],
                contradicting_evidence=["No technical recovery confirmation"],
                risk_flags=risk_flags,
                methodology_version=METHODOLOGY_VERSIONS["direction"],
            )

        # 1. Underlying Trend & Momentum (Weight = 40%)
        t_score = ctx.get("trend_score", 50.0)
        m_score = ctx.get("momentum_score", 50.0)
        total_weight += 40.0
        if t_score >= 60.0:
            bull_points += 20.0
            supporting.append(f"Strong trend score ({t_score:.1f})")
        elif t_score <= 40.0:
            bear_points += 20.0
            contradicting.append(f"Weak trend score ({t_score:.1f})")

        if m_score >= 60.0:
            bull_points += 20.0
            supporting.append(f"Positive momentum ({m_score:.1f})")
        elif m_score <= 40.0:
            bear_points += 20.0
            contradicting.append(f"Negative momentum ({m_score:.1f})")

        # 2. Futures Buildup (Weight = 30%)
        total_weight += 30.0
        buildup = ctx.get("futures_buildup", "NEUTRAL")
        if buildup == "LONG_BUILDUP":
            bull_points += 30.0
            supporting.append("Futures Long Buildup detected")
        elif buildup == "SHORT_BUILDUP":
            bear_points += 30.0
            supporting.append("Futures Short Buildup detected")
        elif buildup == "SHORT_COVERING":
            bull_points += 15.0
            supporting.append("Futures Short Covering")
        elif buildup == "LONG_UNWINDING":
            bear_points += 15.0
            supporting.append("Futures Long Unwinding")

        # 3. PCR Context (Weight = 15%) - Context only, single PCR cannot force direction
        total_weight += 15.0
        pcr = ctx.get("pcr_oi", 1.0)
        if pcr >= 1.25:
            bull_points += 15.0
            supporting.append(f"Healthy bullish PCR OI ({pcr:.2f})")
        elif pcr <= 0.70:
            bear_points += 15.0
            supporting.append(f"Bearish PCR OI ({pcr:.2f})")

        # 4. GEX & Scenario Context (Weight = 15% - Capped secondary context)
        # Note: Gross Unsigned GEX is NON-DIRECTIONAL (0 points). Scenario Net GEX is assumption-dependent.
        total_weight += 15.0
        scen_gex = ctx.get("scenario_net_gex", Decimal("0.00"))
        if scen_gex > Decimal("0.00"):
            bull_points += 15.0
            supporting.append("Positive scenario Net GEX (Dealer Long Gamma / Stabilizing)")
        elif scen_gex < Decimal("0.00"):
            bear_points += 15.0
            supporting.append("Negative scenario Net GEX (Dealer Short Gamma / Volatile)")

        # GEX Alone Protection: If underlying technicals + futures are neutral, GEX alone cannot force CE/PE
        primary_points = (bull_points if bull_points > bear_points else bear_points) - (15.0 if scen_gex != Decimal("0.00") else 0.0)
        if primary_points <= 15.0:
            # Insufficient primary underlying evidence
            return OptionDirectionalAssessment(
                direction="NEUTRAL",
                side_recommendation="NO_TRADE",
                direction_score=Decimal("50.00"),
                evidence_quality="LOW",
                supporting_evidence=supporting,
                contradicting_evidence=contradicting,
                risk_flags=["Insufficient underlying trend and futures evidence to establish directional trade"],
                methodology_version=METHODOLOGY_VERSIONS["direction"],
            )

        # Compute direction score (Bullishness Scale: 0 to 100)
        net_score = 50.0 + ((bull_points - bear_points) / max(1.0, total_weight)) * 50.0
        net_score = max(0.0, min(100.0, net_score))

        if net_score >= 65.0 and bull_points > bear_points * 1.5:
            direction = "BULLISH"
            side = "CE"
        elif net_score <= 35.0 and bear_points > bull_points * 1.5:
            direction = "BEARISH"
            side = "PE"
        elif abs(bull_points - bear_points) < 10.0 and (bull_points > 20.0 or bear_points > 20.0):
            direction = "CONFLICTED"
            side = "NO_TRADE"
            risk_flags.append("Conflicting bullish and bearish signals")
        else:
            direction = "NEUTRAL"
            side = "NO_TRADE"

        evidence_qual = "HIGH" if (len(supporting) + len(contradicting)) >= 3 else "MEDIUM"

        return OptionDirectionalAssessment(
            direction=direction,
            side_recommendation=side,
            direction_score=Decimal(str(round(net_score, 2))),
            evidence_quality=evidence_qual,
            supporting_evidence=supporting,
            contradicting_evidence=contradicting,
            risk_flags=risk_flags,
            methodology_version=METHODOLOGY_VERSIONS["direction"],
        )

    def evaluate_expiry_suitability(
        self,
        expiries: list[date],
        trade_date: date,
        setup_profile: str = "SWING",
        has_event_before_expiry: bool = False,
    ) -> OptionExpiryAssessment:
        """Evaluate actual available contract expiries against setup horizon."""
        if not expiries:
            return OptionExpiryAssessment(
                preferred_expiry=None,
                category="OTHER",
                expiry_score=Decimal("0.00"),
                event_risk_notes=["NO_AVAILABLE_EXPIRIES"],
            )

        evaluated: list[dict[str, Any]] = []
        event_notes: list[str] = []

        if has_event_before_expiry:
            event_notes.append("Major earnings/policy event scheduled before expiry")

        for i, exp in enumerate(expiries):
            dte = max(0, (exp - trade_date).days)
            cat = "NEAREST" if i == 0 else ("NEXT" if i == 1 else "MONTHLY")

            # Scoring based on DTE and setup profile
            if setup_profile == "BTST":
                # BTST prefers front expiry if DTE >= 1
                if has_event_before_expiry:
                    score = 20.0  # Vetoed due to overnight event risk
                else:
                    score = 90.0 if 1 <= dte <= 7 else (50.0 if dte > 7 else 20.0)
            elif setup_profile == "SWING":
                # Swing prefers 7-30 DTE to avoid rapid theta decay
                if dte < 5:
                    score = 30.0  # Low DTE penalty for SWING profile
                else:
                    score = 90.0 if 7 <= dte <= 30 else (60.0 if 30 < dte <= 45 else 40.0)
            else:  # BREAKOUT
                score = 85.0 if 5 <= dte <= 25 else 40.0

            evaluated.append({"expiry": str(exp), "dte": dte, "category": cat, "score": score})

        # Select highest scoring expiry
        best = max(evaluated, key=lambda e: (e["score"], -abs(e["dte"] - 14)))
        pref_exp = expiries[[e["expiry"] for e in evaluated].index(best["expiry"])]
        pref_dte = best["dte"]

        return OptionExpiryAssessment(
            preferred_expiry=pref_exp,
            dte=pref_dte,
            category=best["category"],
            expiry_score=Decimal(str(round(best["score"], 2))),
            evaluated_expiries=evaluated,
            has_event_before_expiry=has_event_before_expiry,
            event_risk_notes=event_notes,
            methodology_version=METHODOLOGY_VERSIONS["expiry"],
        )

    def evaluate_moneyness(
        self,
        strike: Decimal,
        spot: Decimal,
        delta: float | None,
        option_type: str,
        expected_move_pct: float = 3.0,
    ) -> OptionMoneynessAssessment:
        """Evaluate moneyness classification and Delta suitability."""
        if spot <= 0:
            return OptionMoneynessAssessment(
                classification="ATM",
                delta_suitability="UNACCEPTABLE",
                breakeven_within_expected_move=False,
            )

        dist_pct = float((strike - spot) / spot * Decimal("100.0"))
        if option_type == "CE":
            if dist_pct < -3.0:
                cls = "DEEP_ITM"
            elif dist_pct < -0.5:
                cls = "ITM"
            elif abs(dist_pct) <= 0.5:
                cls = "ATM"
            elif dist_pct <= 3.0:
                cls = "OTM"
            else:
                cls = "DEEP_OTM"
        else:  # PE
            if dist_pct > 3.0:
                cls = "DEEP_ITM"
            elif dist_pct > 0.5:
                cls = "ITM"
            elif abs(dist_pct) <= 0.5:
                cls = "ATM"
            elif dist_pct >= -3.0:
                cls = "OTM"
            else:
                cls = "DEEP_OTM"

        abs_delta = abs(delta) if delta is not None else 0.50
        penalties: list[str] = []

        if 0.40 <= abs_delta <= 0.65:
            delta_suit = "EXCELLENT"
        elif 0.30 <= abs_delta < 0.40 or 0.65 < abs_delta <= 0.80:
            delta_suit = "GOOD"
        elif 0.20 <= abs_delta < 0.30:
            delta_suit = "MARGINAL"
            penalties.append("LOW_DELTA_FAR_OTM")
        else:
            delta_suit = "UNACCEPTABLE"
            if abs_delta < 0.20:
                penalties.append("LOW_DELTA_FAR_OTM")
            else:
                penalties.append("DEEP_ITM_HIGH_CAPITAL")

        within_em = abs(dist_pct) <= expected_move_pct * 1.2
        if not within_em:
            penalties.append("BREAKEVEN_OUTSIDE_EXPECTED_MOVE")

        return OptionMoneynessAssessment(
            classification=cls,
            delta_suitability=delta_suit,
            breakeven_within_expected_move=within_em,
            penalty_reasons=penalties,
            methodology_version=METHODOLOGY_VERSIONS["moneyness"],
        )

    async def assess_opportunity(
        self,
        symbol: str,
        trade_date: date,
        as_of_date: date | None = None,
        setup_profile: str = "SWING",
        expiry_scope: str = "FRONT_EXPIRY",
        max_alternatives: int = 3,
        has_event_before_expiry: bool = False,
    ) -> OptionOpportunityAssessment:
        """Full Options Research & Opportunity Assessment pipeline."""
        as_of = as_of_date or trade_date

        # 1. Underlying Context & Directional Structure
        ctx = await self._get_underlying_direction_context(symbol, trade_date, as_of)
        dir_assess = self.evaluate_directional_structure(ctx)

        # 2. Expiry Suitability
        from app.services.historical_option_chain import HistoricalOptionChainService
        chain_svc = HistoricalOptionChainService(self.session)
        expiries = await chain_svc.get_available_expiries(symbol, trade_date, as_of)
        exp_assess = self.evaluate_expiry_suitability(
            expiries, trade_date, setup_profile, has_event_before_expiry
        )

        no_trade_reasons: list[str] = []
        if dir_assess.direction == "NO_TRADE" or dir_assess.side_recommendation == "NO_TRADE":
            no_trade_reasons.append(f"Directional assessment is {dir_assess.direction}")
            no_trade_reasons.extend(dir_assess.risk_flags)

        if exp_assess.preferred_expiry is None:
            no_trade_reasons.append("No available option expiries for trade date")

        if setup_profile == "BTST" and has_event_before_expiry:
            no_trade_reasons.append("BTST vetoed due to scheduled earnings/policy event before expiry")

        if no_trade_reasons:
            return OptionOpportunityAssessment(
                symbol=symbol.upper(),
                trade_date=trade_date,
                as_of_date=as_of,
                overall_classification="NO_TRADE",
                selected_side="NO_TRADE",
                directional_assessment=dir_assess,
                expiry_assessment=exp_assess,
                no_trade_reasons=no_trade_reasons,
                provenance={"symbol": symbol.upper(), "trade_date": str(trade_date), "as_of": str(as_of)},
            )

        target_exp = exp_assess.preferred_expiry
        side = dir_assess.side_recommendation

        # 3. Bulk Load Enriched Option Chain once in memory (zero N+1 queries)
        from app.services.advanced_options_service import AdvancedOptionsService
        adv_svc = AdvancedOptionsService(self.session)
        enriched_rows = await adv_svc.get_enriched_option_chain(symbol, trade_date, target_exp, as_of)
        chain = await chain_svc.get_historical_option_chain(symbol, trade_date, target_exp, as_of)
        spot = chain.resolved_spot or Decimal("0.00")

        # Fetch expected move
        exp_move = await adv_svc.get_expected_move(symbol, trade_date, target_exp, as_of)
        exp_move_pct = float(exp_move.expected_move_pct) if exp_move else 3.0

        # Load exposure table
        from app.services.advanced_exposure_service import AdvancedExposureService
        exp_svc = AdvancedExposureService(self.session)
        exposure_rows = await exp_svc.get_contract_exposures(symbol, trade_date, target_exp, as_of)
        exp_map = {r.contract_id: r for r in exposure_rows}

        evaluated_contracts: list[OptionStrikeAssessment] = []

        for r in enriched_rows:
            side_obs = r.call if side == "CE" else r.put
            if side_obs is None:
                continue

            contract_id = side_obs.contract_id
            strike = side_obs.strike_price
            price = side_obs.close
            greeks = side_obs.greeks
            delta = greeks.delta
            iv = side_obs.iv_result.iv
            oi = side_obs.open_interest
            vol = side_obs.volume_contracts

            exp_row = exp_map.get(contract_id)
            lot_size = exp_row.lot_size if exp_row else 1
            lot_qual = exp_row.lot_size_quality if exp_row else "UNAVAILABLE"

            # Moneyness Assessment
            money_assess = self.evaluate_moneyness(strike, spot, delta, side, exp_move_pct)

            # Score strike
            why_sel: list[str] = []
            why_rej: list[str] = []
            vetoes: list[str] = []
            factors: dict[str, Decimal] = {}

            if lot_qual == "UNAVAILABLE":
                vetoes.append("MISSING_LOT_SIZE")
                why_rej.append("Historical lot size unavailable")

            if side_obs.liquidity.classification in ("ILLIQUID", "UNKNOWN") or (oi is not None and oi < 100):
                vetoes.append("ILLIQUID_VETO")
                why_rej.append("Illiquid contract or very low open interest")

            if "LOW_DELTA_FAR_OTM" in money_assess.penalty_reasons:
                vetoes.append("LOW_DELTA_FAR_OTM")
                why_rej.append("Delta too low / far OTM contract")

            if "BREAKEVEN_OUTSIDE_EXPECTED_MOVE" in money_assess.penalty_reasons:
                vetoes.append("BREAKEVEN_OUTSIDE_EXPECTED_MOVE")
                why_rej.append("Breakeven price is outside expected move boundary")

            # Scoring factors
            # 1. Delta Score (using abs(delta) for magnitude fit)
            abs_d = abs(delta) if delta is not None else 0.50
            d_score = max(0.0, 100.0 - abs(abs_d - 0.50) * 200.0)
            factors["delta_fit"] = Decimal(str(round(d_score, 2)))

            # 2. Moneyness Fit Score
            m_score = 90.0 if money_assess.classification in ("ATM", "ITM") else (60.0 if money_assess.classification == "OTM" else 20.0)
            factors["moneyness_fit"] = Decimal(str(round(m_score, 2)))

            # 3. Liquidity & Volume Score
            l_score = 90.0 if side_obs.liquidity.classification == "HIGH" else (70.0 if side_obs.liquidity.classification == "MEDIUM" else 30.0)
            factors["liquidity"] = Decimal(str(round(l_score, 2)))

            # 4. Theta Decay Burden Score: abs(daily_theta) / premium * 100
            if price and price > Decimal("0.00"):
                price_flt = float(price)
                theta_flt = abs(greeks.theta_per_day) if greeks and greeks.theta_per_day is not None else 0.0
                theta_burden = (theta_flt / price_flt) * 100.0
            else:
                theta_burden = None

            if theta_burden is not None:
                t_score = max(0.0, 100.0 - theta_burden * 10.0)
            else:
                t_score = 50.0
            factors["theta_cost"] = Decimal(str(round(t_score, 2)))

            # Composite weighted strike score
            tot_score = (d_score * 0.35) + (m_score * 0.25) + (l_score * 0.20) + (t_score * 0.20)
            if vetoes:
                tot_score = 0.0

            if tot_score >= 65.0 and not vetoes:
                why_sel.append(f"Strong Delta fit ({abs_d:.2f})")
                why_sel.append(f"Moneyness {money_assess.classification} within expected move")
                why_sel.append(f"Good liquidity ({side_obs.liquidity.classification})")

            est_cost = (price * Decimal(str(lot_size))) if (price is not None and lot_size is not None and lot_qual != "UNAVAILABLE") else None
            cls_str = "PREFERRED" if tot_score >= 70.0 and not vetoes else ("ALTERNATIVE" if tot_score >= 50.0 and not vetoes else "REJECTED")

            evaluated_contracts.append(
                OptionStrikeAssessment(
                    contract_id=contract_id,
                    trading_symbol=side_obs.trading_symbol,
                    underlying=symbol.upper(),
                    option_type=side,
                    expiry_date=target_exp,
                    strike_price=strike,
                    moneyness=money_assess.classification,
                    price=price,
                    lot_size=lot_size if lot_qual != "UNAVAILABLE" else None,
                    estimated_lot_premium_cost=est_cost,
                    premium_at_risk_per_lot=est_cost,
                    open_interest=oi,
                    volume_contracts=vol,
                    iv=iv,
                    greeks=greeks,
                    intrinsic_value=side_obs.moneyness.intrinsic_value,
                    time_value=side_obs.moneyness.time_value,
                    breakeven_price=side_obs.breakeven.breakeven_price,
                    breakeven_distance_pct=side_obs.breakeven.distance_to_breakeven_pct,
                    theta_burden_pct=theta_burden,
                    liquidity_classification=side_obs.liquidity.classification,
                    strike_score=Decimal(str(round(tot_score, 2))),
                    classification=cls_str,
                    factor_scores=factors,
                    why_selected=why_sel,
                    why_rejected=why_rej,
                    veto_reasons=vetoes,
                    data_quality="HIGH" if lot_qual == "VERIFIED_HISTORICAL" else "MEDIUM",
                    methodology_version=METHODOLOGY_VERSIONS["ranking"],
                )
            )

        if not evaluated_contracts:
            return OptionOpportunityAssessment(
                symbol=symbol.upper(),
                trade_date=trade_date,
                as_of_date=as_of,
                overall_classification="NO_TRADE",
                selected_side=side,
                directional_assessment=dir_assess,
                expiry_assessment=exp_assess,
                no_trade_reasons=["No valid contracts evaluated on preferred side and expiry"],
                provenance={"symbol": symbol.upper(), "trade_date": str(trade_date), "as_of": str(as_of)},
            )

        # Deterministic ranking (Score desc, Liquidity desc, Contract ID asc)
        sorted_contracts = sorted(
            evaluated_contracts,
            key=lambda c: (c.strike_score, c.liquidity_classification == "HIGH", -c.contract_id),
            reverse=True,
        )

        valid_contracts = [c for c in sorted_contracts if not c.veto_reasons]
        rejected_list = [c for c in sorted_contracts if c.veto_reasons]

        pref_contract = valid_contracts[0] if valid_contracts else None
        alt_contracts = valid_contracts[1 : 1 + max_alternatives] if len(valid_contracts) > 1 else []

        if pref_contract:
            pref_contract.classification = "PREFERRED"
            # Evidence Quality Gate: Cap overall classification if evidence quality is LOW
            if dir_assess.evidence_quality == "LOW" or pref_contract.data_quality != "HIGH":
                overall_cls = "OPTION_RESEARCH_CANDIDATE"
            else:
                overall_cls = "STRONG_OPTION_RESEARCH_CANDIDATE" if pref_contract.strike_score >= 75.0 else "OPTION_RESEARCH_CANDIDATE"
        else:
            overall_cls = "NO_TRADE"
            no_trade_reasons.append("All contracts on preferred expiry were vetoed for risk/liquidity reasons")

        # Pick moneyness assessment of preferred contract
        pref_money = None
        if pref_contract:
            pref_money = self.evaluate_moneyness(
                pref_contract.strike_price, spot, pref_contract.greeks.delta, side, exp_move_pct
            )

        return OptionOpportunityAssessment(
            symbol=symbol.upper(),
            trade_date=trade_date,
            as_of_date=as_of,
            overall_classification=overall_cls,
            selected_side=side,
            directional_assessment=dir_assess,
            expiry_assessment=exp_assess,
            moneyness_assessment=pref_money,
            preferred_contract=pref_contract,
            alternative_contracts=alt_contracts,
            rejected_contracts=rejected_list,
            no_trade_reasons=no_trade_reasons,
            provenance={"symbol": symbol.upper(), "trade_date": str(trade_date), "as_of": str(as_of)},
        )
