"""Opportunity Profile evaluation engine — Sprint 19.1G.

Evaluates Stock360Snapshots against 5 profile methodologies:
  1. INVESTMENT_QUALITY
  2. QUALITY_PULLBACK (Fallen Leader Detection)
  3. BREAKOUT
  4. SWING
  5. BTST_READINESS (with Overnight Event Risk VETO)
"""
from __future__ import annotations

from app.schemas.stock360 import (
    ComponentContributionRead,
    Stock360Snapshot,
    StockOpportunityAssessment,
)


class OpportunityEngine:
    """Evaluates Stock 360 snapshots against 5 research opportunity profiles."""

    METHODOLOGY_VERSION = "v1.0"

    @classmethod
    def evaluate_profile(
        cls,
        snapshot: Stock360Snapshot,
        profile: str,
    ) -> StockOpportunityAssessment:
        """Route evaluation to profile-specific methodology."""
        prof_upper = profile.upper()
        if prof_upper == "INVESTMENT_QUALITY":
            return cls._evaluate_investment_quality(snapshot)
        elif prof_upper == "QUALITY_PULLBACK":
            return cls._evaluate_quality_pullback(snapshot)
        elif prof_upper == "BREAKOUT":
            return cls._evaluate_breakout(snapshot)
        elif prof_upper == "SWING":
            return cls._evaluate_swing(snapshot)
        elif prof_upper == "BTST_READINESS":
            return cls._evaluate_btst_readiness(snapshot)
        else:
            raise ValueError(f"Unknown opportunity profile '{profile}'")

    @classmethod
    def _evaluate_investment_quality(cls, snap: Stock360Snapshot) -> StockOpportunityAssessment:
        """Evaluate long-term investment quality profile."""
        components: list[ComponentContributionRead] = []
        evidence: list[str] = []
        contradictions: list[str] = []
        risks: list[str] = []
        vetoes: list[str] = []

        # 1. Fundamental Quality (Weight: 40%)
        fund_score = 50.0
        if snap.fundamentals and snap.fundamentals.quality_inputs:
            fund_score = float(snap.fundamentals.quality_inputs.fundamental_quality_score)
            evidence.append(f"Fundamental Quality Score: {fund_score:.1f}/100")
            if snap.fundamentals.solvency_cashflow and snap.fundamentals.solvency_cashflow.debt_to_equity:
                de = float(snap.fundamentals.solvency_cashflow.debt_to_equity)
                if de < 0.5:
                    evidence.append(f"Low Debt-to-Equity ratio ({de:.2f})")
                elif de > 2.0:
                    contradictions.append(f"High Debt-to-Equity ratio ({de:.2f})")
        else:
            contradictions.append("Missing detailed fundamental financial statements")

        components.append(
            ComponentContributionRead(
                component_name="Fundamental Quality",
                score=fund_score,
                weight=0.40,
                weighted_contribution=fund_score * 0.40,
                status_reason="Evaluated from balance sheet and cash flow quality",
            )
        )

        # 2. Growth Quality (Weight: 25%)
        growth_score = 50.0
        if snap.fundamentals and snap.fundamentals.growth:
            g = snap.fundamentals.growth
            if g.revenue_growth_qoq is not None and g.revenue_growth_qoq > 0:
                growth_score += 20.0
                evidence.append(f"Positive QoQ revenue growth (+{float(g.revenue_growth_qoq):.1f}%)")
            if g.profit_growth_qoq is not None and g.profit_growth_qoq > 0:
                growth_score += 20.0
                evidence.append(f"Positive QoQ profit growth (+{float(g.profit_growth_qoq):.1f}%)")
        growth_score = min(100.0, max(0.0, growth_score))

        components.append(
            ComponentContributionRead(
                component_name="Growth Quality",
                score=growth_score,
                weight=0.25,
                weighted_contribution=growth_score * 0.25,
                status_reason="Evaluated from revenue and profit growth trajectory",
            )
        )

        # 3. Valuation Context (Weight: 15%)
        val_score = 60.0
        if snap.fundamentals and snap.fundamentals.valuation and snap.fundamentals.valuation.pe_ratio:
            pe = float(snap.fundamentals.valuation.pe_ratio)
            if pe < 25.0:
                val_score = 80.0
                evidence.append(f"Reasonable P/E valuation ({pe:.1f}x)")
            elif pe > 60.0:
                val_score = 40.0
                contradictions.append(f"Elevated P/E valuation ({pe:.1f}x)")

        components.append(
            ComponentContributionRead(
                component_name="Valuation Context",
                score=val_score,
                weight=0.15,
                weighted_contribution=val_score * 0.15,
                status_reason="Evaluated from point-in-time P/E ratio",
            )
        )

        # 4. Long-term Technical Structure (Weight: 20%)
        tech_score = 50.0
        if snap.technicals:
            if snap.technicals.trend_state == "UPTREND":
                tech_score = 85.0
                evidence.append("Primary trend structure is UPTREND")
            elif snap.technicals.trend_state == "DOWNTREND":
                tech_score = 30.0
                contradictions.append("Primary trend structure is DOWNTREND")

        components.append(
            ComponentContributionRead(
                component_name="Technical Structure",
                score=tech_score,
                weight=0.20,
                weighted_contribution=tech_score * 0.20,
                status_reason="Evaluated from 50D/200D EMA trend structure",
            )
        )

        # News & Policy Risk Modifiers
        if snap.news_policy:
            if snap.news_policy.policy_headwind:
                risks.append("Adverse government policy headwind detected")
            if snap.news_policy.regulatory_flag:
                risks.append("Recent SEBI or exchange regulatory disclosure")

        total_score = round(sum(c.weighted_contribution for c in components), 1)
        classification = cls._classify_score(total_score, vetoes, risks)

        return StockOpportunityAssessment(
            symbol=snap.symbol,
            stock_id=snap.stock_id,
            profile="INVESTMENT_QUALITY",
            evaluation_date=snap.evaluation_date,
            as_of_date=snap.as_of_date,
            score=total_score,
            classification=classification,
            methodology_version=cls.METHODOLOGY_VERSION,
            supporting_evidence=evidence,
            contradicting_evidence=contradictions,
            risk_flags=risks,
            veto_reasons=vetoes,
            data_completeness_pct=snap.data_completeness_pct,
            evidence_quality="HIGH" if snap.data_completeness_pct >= 75.0 else "MEDIUM",
            component_contributions=components,
            sector_name=snap.sector_name,
            is_fno=snap.is_fno,
            provenance={"eval_profile": "INVESTMENT_QUALITY"},
        )

    @classmethod
    def _evaluate_quality_pullback(cls, snap: Stock360Snapshot) -> StockOpportunityAssessment:
        """Evaluate Quality Pullback (Fallen Leader Detection) profile."""
        components: list[ComponentContributionRead] = []
        evidence: list[str] = []
        contradictions: list[str] = []
        risks: list[str] = []
        vetoes: list[str] = []

        # Condition 1: High Fundamental Quality
        fund_qual = 50.0
        if snap.fundamentals and snap.fundamentals.quality_inputs:
            fund_qual = float(snap.fundamentals.quality_inputs.fundamental_quality_score)
            if fund_qual >= 65.0:
                evidence.append(f"Strong underlying business quality ({fund_qual:.1f}/100)")
            else:
                contradictions.append(f"Sub-par fundamental quality ({fund_qual:.1f}/100)")
        else:
            contradictions.append("Missing fundamental quality data")

        # Condition 2: Drawdown from 52-week High (-8% to -35%)
        dd_score = 50.0
        if snap.technicals and snap.technicals.pct_from_52w_high is not None:
            dd = snap.technicals.pct_from_52w_high
            if -35.0 <= dd <= -8.0:
                dd_score = 90.0
                evidence.append(f"Controlled pullback from 52w High ({dd:.1f}%)")
            elif dd < -35.0:
                dd_score = 30.0
                risks.append(f"Severe drawdown from 52w High ({dd:.1f}%) — Falling knife risk")
            else:
                dd_score = 20.0
                contradictions.append("Insufficient pullback from high (near 52w high)")

        components.append(
            ComponentContributionRead(
                component_name="Fundamental Foundation",
                score=fund_qual,
                weight=0.40,
                weighted_contribution=fund_qual * 0.40,
                status_reason="Fundamental quality must remain intact",
            )
        )
        components.append(
            ComponentContributionRead(
                component_name="Controlled Drawdown",
                score=dd_score,
                weight=0.30,
                weighted_contribution=dd_score * 0.30,
                status_reason="Pullback between 8% and 35% from peak",
            )
        )

        # Condition 3: Volume & Delivery Moderation
        vol_score = 60.0
        if snap.volume_delivery:
            if snap.volume_delivery.accumulation_signal:
                vol_score = 90.0
                evidence.append("Accumulation signal: high delivery % during stabilization")
            elif snap.volume_delivery.relative_volume_20d < 1.2:
                vol_score = 75.0
                evidence.append("Selling pressure moderating (low relative volume)")

        components.append(
            ComponentContributionRead(
                component_name="Volume Moderation",
                score=vol_score,
                weight=0.30,
                weighted_contribution=vol_score * 0.30,
                status_reason="Volume and delivery stabilization context",
            )
        )

        # News / Event Risk Contradiction Veto check
        if snap.news_policy:
            if snap.news_policy.negative_context_count > 2:
                risks.append("Multiple recent adverse news/policy events")
            if snap.news_policy.overnight_event_risk:
                risks.append("Pending high-volatility event/results disclosure")

        total_score = round(sum(c.weighted_contribution for c in components), 1)
        classification = cls._classify_score(total_score, vetoes, risks)

        return StockOpportunityAssessment(
            symbol=snap.symbol,
            stock_id=snap.stock_id,
            profile="QUALITY_PULLBACK",
            evaluation_date=snap.evaluation_date,
            as_of_date=snap.as_of_date,
            score=total_score,
            classification=classification,
            methodology_version=cls.METHODOLOGY_VERSION,
            supporting_evidence=evidence,
            contradicting_evidence=contradictions,
            risk_flags=risks,
            veto_reasons=vetoes,
            data_completeness_pct=snap.data_completeness_pct,
            evidence_quality="HIGH" if snap.data_completeness_pct >= 70.0 else "MEDIUM",
            component_contributions=components,
            sector_name=snap.sector_name,
            is_fno=snap.is_fno,
            provenance={"eval_profile": "QUALITY_PULLBACK"},
        )

    @classmethod
    def _evaluate_breakout(cls, snap: Stock360Snapshot) -> StockOpportunityAssessment:
        """Evaluate Price & Volume Breakout profile."""
        components: list[ComponentContributionRead] = []
        evidence: list[str] = []
        contradictions: list[str] = []
        risks: list[str] = []
        vetoes: list[str] = []

        # 1. Price Location (Weight: 40%)
        price_score = 50.0
        if snap.technicals and snap.technicals.pct_from_52w_high is not None:
            if snap.technicals.pct_from_52w_high >= -3.0:
                price_score = 95.0
                evidence.append("Close within 3% of 52-week High (Breakout location)")
            elif snap.technicals.trend_state == "UPTREND":
                price_score = 75.0
                evidence.append("Strong uptrend price location")

        components.append(
            ComponentContributionRead(
                component_name="Breakout Location",
                score=price_score,
                weight=0.40,
                weighted_contribution=price_score * 0.40,
                status_reason="Price near 52w High or key resistance",
            )
        )

        # 2. Volume & Delivery Confirmation (Weight: 40%)
        vol_score = 40.0
        if snap.volume_delivery:
            if snap.volume_delivery.volume_breakout:
                vol_score += 35.0
                evidence.append(f"Volume expansion: Rvol {snap.volume_delivery.relative_volume_20d}x")
            if snap.volume_delivery.delivery_breakout:
                vol_score += 25.0
                evidence.append("Delivery breakout confirmation")

        components.append(
            ComponentContributionRead(
                component_name="Volume & Delivery Confirmation",
                score=vol_score,
                weight=0.40,
                weighted_contribution=vol_score * 0.40,
                status_reason="High volume and delivery confirmation",
            )
        )

        # 3. Relative Strength (Weight: 20%)
        rs_score = 50.0
        if snap.relative_strength and snap.relative_strength.rs_trend == "OUTPERFORMING":
            rs_score = 90.0
            evidence.append("Outperforming Nifty 50 benchmark")

        components.append(
            ComponentContributionRead(
                component_name="Relative Strength",
                score=rs_score,
                weight=0.20,
                weighted_contribution=rs_score * 0.20,
                status_reason="Benchmark relative performance",
            )
        )

        total_score = round(sum(c.weighted_contribution for c in components), 1)
        classification = cls._classify_score(total_score, vetoes, risks)

        return StockOpportunityAssessment(
            symbol=snap.symbol,
            stock_id=snap.stock_id,
            profile="BREAKOUT",
            evaluation_date=snap.evaluation_date,
            as_of_date=snap.as_of_date,
            score=total_score,
            classification=classification,
            methodology_version=cls.METHODOLOGY_VERSION,
            supporting_evidence=evidence,
            contradicting_evidence=contradictions,
            risk_flags=risks,
            veto_reasons=vetoes,
            data_completeness_pct=snap.data_completeness_pct,
            evidence_quality="HIGH",
            component_contributions=components,
            sector_name=snap.sector_name,
            is_fno=snap.is_fno,
            provenance={"eval_profile": "BREAKOUT"},
        )

    @classmethod
    def _evaluate_swing(cls, snap: Stock360Snapshot) -> StockOpportunityAssessment:
        """Evaluate Swing Trading Opportunity profile."""
        components: list[ComponentContributionRead] = []
        evidence: list[str] = []
        contradictions: list[str] = []
        risks: list[str] = []
        vetoes: list[str] = []

        tech_score = 50.0
        if snap.technicals and snap.technicals.trend_state == "UPTREND":
            tech_score = 80.0
            evidence.append("Uptrend structure confirmed")

        components.append(
            ComponentContributionRead(
                component_name="Swing Structure",
                score=tech_score,
                weight=0.50,
                weighted_contribution=tech_score * 0.50,
                status_reason="Primary trend and moving average alignment",
            )
        )

        vol_score = 50.0
        if snap.volume_delivery and snap.volume_delivery.relative_volume_20d >= 1.2:
            vol_score = 75.0
            evidence.append("Healthy volume participation")

        components.append(
            ComponentContributionRead(
                component_name="Volume Participation",
                score=vol_score,
                weight=0.50,
                weighted_contribution=vol_score * 0.50,
                status_reason="Volume participation for multi-day swing",
            )
        )

        total_score = round(sum(c.weighted_contribution for c in components), 1)
        classification = cls._classify_score(total_score, vetoes, risks)

        return StockOpportunityAssessment(
            symbol=snap.symbol,
            stock_id=snap.stock_id,
            profile="SWING",
            evaluation_date=snap.evaluation_date,
            as_of_date=snap.as_of_date,
            score=total_score,
            classification=classification,
            methodology_version=cls.METHODOLOGY_VERSION,
            supporting_evidence=evidence,
            contradicting_evidence=contradictions,
            risk_flags=risks,
            veto_reasons=vetoes,
            data_completeness_pct=snap.data_completeness_pct,
            evidence_quality="HIGH",
            component_contributions=components,
            sector_name=snap.sector_name,
            is_fno=snap.is_fno,
            provenance={"eval_profile": "SWING"},
        )

    @classmethod
    def _evaluate_btst_readiness(cls, snap: Stock360Snapshot) -> StockOpportunityAssessment:
        """Evaluate BTST (Buy Today Sell Tomorrow) Readiness profile with Overnight Risk VETO."""
        components: list[ComponentContributionRead] = []
        evidence: list[str] = []
        contradictions: list[str] = []
        risks: list[str] = []
        vetoes: list[str] = []

        # -----------------------------------------------------------------------
        # HARD VETO 1: Overnight Event Risk (Earnings / Board Meeting Tomorrow)
        # -----------------------------------------------------------------------
        if snap.news_policy:
            if snap.news_policy.overnight_event_risk or any(
                e.event_risk_flag in ("OVERNIGHT_EVENT_RISK", "RESULTS_PENDING")
                or e.category in ("FINANCIAL_RESULTS", "RESULTS")
                for e in snap.news_policy.recent_events
            ):
                vetoes.append("OVERNIGHT_EVENT_RISK_ACTIVE: Pending high-volatility event/results disclosure tomorrow")

        if snap.news_policy and snap.news_policy.upcoming_event_risks:
            for r in snap.news_policy.upcoming_event_risks:
                if r.overnight_risk:
                    vetoes.append(f"UPCOMING_EVENT_VETO: Scheduled {r.category} ({r.title})")

        # 1. Volume & Delivery Confirmation (Weight: 50%)
        vol_score = 40.0
        if snap.volume_delivery:
            if snap.volume_delivery.relative_volume_20d >= 1.5:
                vol_score += 35.0
                evidence.append(f"BTST Volume expansion: Rvol {snap.volume_delivery.relative_volume_20d}x")
            if snap.volume_delivery.delivery_pct and snap.volume_delivery.delivery_pct >= 40.0:
                vol_score += 25.0
                evidence.append(f"High Delivery % confirmation ({snap.volume_delivery.delivery_pct:.1f}%)")

        components.append(
            ComponentContributionRead(
                component_name="End-of-Day Volume & Delivery",
                score=vol_score,
                weight=0.50,
                weighted_contribution=vol_score * 0.50,
                status_reason="Strong relative volume and delivery at close",
            )
        )

        # 2. Price Location & Momentum (Weight: 50%)
        price_score = 50.0
        if snap.technicals and snap.technicals.pct_from_52w_high is not None and snap.technicals.pct_from_52w_high >= -5.0:
            price_score = 85.0
            evidence.append("Closing near high/breakout structure")

        components.append(
            ComponentContributionRead(
                component_name="EOD Price Momentum",
                score=price_score,
                weight=0.50,
                weighted_contribution=price_score * 0.50,
                status_reason="Closing location near top of daily range",
            )
        )

        total_score = round(sum(c.weighted_contribution for c in components), 1)
        classification = cls._classify_score(total_score, vetoes, risks)

        return StockOpportunityAssessment(
            symbol=snap.symbol,
            stock_id=snap.stock_id,
            profile="BTST_READINESS",
            evaluation_date=snap.evaluation_date,
            as_of_date=snap.as_of_date,
            score=total_score,
            classification=classification,
            methodology_version=cls.METHODOLOGY_VERSION,
            supporting_evidence=evidence,
            contradicting_evidence=contradictions,
            risk_flags=risks,
            veto_reasons=vetoes,
            data_completeness_pct=snap.data_completeness_pct,
            evidence_quality="HIGH",
            component_contributions=components,
            sector_name=snap.sector_name,
            is_fno=snap.is_fno,
            provenance={"eval_profile": "BTST_READINESS"},
        )

    @staticmethod
    def _classify_score(score: float, vetoes: list[str], risks: list[str]) -> str:
        """Classify numerical score into research status categories."""
        if vetoes:
            return "REJECTED"
        if score >= 80.0:
            return "STRONG_RESEARCH_CANDIDATE"
        elif score >= 65.0:
            return "RESEARCH_CANDIDATE"
        elif score >= 50.0:
            return "WATCH"
        elif score >= 35.0:
            return "WAIT"
        else:
            return "NO_TRADE"
