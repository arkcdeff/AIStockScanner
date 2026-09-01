"""Stock Intelligence / Decision Context Aggregation Service — Stage 2.6.5 Phase F.

Synthesizes point-in-time safe evidence across Technicals, Fundamentals, Valuation,
News & Policy, Corporate Events, Volume/Delivery, Sector, and Market Context
into a deterministic research decision context.

Strict Invariants:
  - MISSING != ZERO (Missing data recorded as UNAVAILABLE in missing_inputs/watch_items).
  - COMPLETENESS != CONFIDENCE (Completeness = data availability; Confidence = signal reliability/concordance).
  - PIT ENFORCED (trade_date <= eval_d, available_at <= eval_d).
  - ZERO / NEUTRAL PRESERVED (Real zero/neutral values are preserved).
  - DETERMINISTIC (Same inputs produce identical output with zero randomness).
  - NO BUY/SELL CLAIMS (Decision context only; not investment advice).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.sector import Sector
from app.models.stock import Stock
from app.models.technical_analysis import TechnicalAnalysis
from app.schemas.stock_intelligence import (
    EvidenceDirection,
    EvidenceDomain,
    EvidenceItem,
    FactorItem,
    OverallState,
    StockIntelligence,
)
from app.services.fundamental_corporate_service import FundamentalCorporateService
from app.services.news_policy_service import NewsPolicyService


class StockIntelligenceService:
    """Orchestrates deterministic decision context aggregation for a single stock."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.fund_svc = FundamentalCorporateService(session)
        self.news_svc = NewsPolicyService(session)

    async def get_stock_intelligence(
        self,
        symbol: str,
        evaluation_date: date | None = None,
        as_of_date: date | None = None,
    ) -> StockIntelligence:
        """Compute point-in-time deterministic Stock Intelligence context for symbol."""
        # 1. Resolve stock
        stmt_stock = (
            select(Stock, Sector.name.label("sector_name"))
            .outerjoin(Sector, Stock.sector_id == Sector.id)
            .where(func.upper(Stock.symbol) == symbol.strip().upper())
        )
        res_stock = await self.session.execute(stmt_stock)
        stock_row = res_stock.first()

        eval_d = evaluation_date or date.today()
        as_of = as_of_date or eval_d

        if not stock_row:
            return StockIntelligence(
                symbol=symbol.upper(),
                evaluation_date=eval_d,
                as_of_date=as_of,
                overall_state=OverallState.NOT_AVAILABLE,
                state_reason=f"Stock symbol '{symbol}' was not found in stock master.",
                confidence=0.0,
                confidence_label="INSUFFICIENT",
                completeness_pct=0.0,
                completeness_label="CRITICAL_GAPS",
                missing_inputs=[d.value for d in EvidenceDomain],
                domain_coverage={d.value: "UNAVAILABLE" for d in EvidenceDomain},
                quality_warnings=["STOCK_NOT_FOUND"],
                provenance={"evaluation_date": eval_d.isoformat(), "as_of_date": as_of.isoformat()},
            )

        stock: Stock = stock_row[0]
        sector_name: str | None = stock_row[1]

        # 2. Fetch latest PIT price history
        stmt_prices = (
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock.id)
            .where(DailyPrice.trade_date <= eval_d)
            .order_by(DailyPrice.trade_date.desc())
            .limit(200)
        )
        prices = list((await self.session.execute(stmt_prices)).scalars().all())

        if not prices:
            return StockIntelligence(
                symbol=stock.symbol,
                stock_id=stock.id,
                company_name=stock.company_name or stock.symbol,
                evaluation_date=eval_d,
                as_of_date=as_of,
                overall_state=OverallState.NOT_AVAILABLE,
                state_reason=f"No price observations available for {stock.symbol} on or before {eval_d.isoformat()}.",
                confidence=0.0,
                confidence_label="INSUFFICIENT",
                completeness_pct=0.0,
                completeness_label="CRITICAL_GAPS",
                missing_inputs=[d.value for d in EvidenceDomain],
                domain_coverage={d.value: "UNAVAILABLE" for d in EvidenceDomain},
                quality_warnings=["NO_PRICE_DATA_ON_EVALUATION_DATE"],
                provenance={"evaluation_date": eval_d.isoformat(), "as_of_date": as_of.isoformat()},
            )

        latest_price = prices[0]
        actual_eval_d = latest_price.trade_date if evaluation_date is None else eval_d

        # 3. Gather evidence from all 8 domains
        evidence_list: list[EvidenceItem] = []
        domain_coverage: dict[str, str] = {}
        missing_inputs: list[str] = []

        # Domain A: Technical
        await self._collect_technical_evidence(
            stock.id, latest_price, prices, actual_eval_d, evidence_list, domain_coverage, missing_inputs
        )

        # Domain B: Fundamental & Quality
        await self._collect_fundamental_evidence(
            stock.symbol, actual_eval_d, as_of, evidence_list, domain_coverage, missing_inputs
        )

        # Domain C: Valuation & Relative Valuation
        await self._collect_valuation_evidence(
            stock.symbol, actual_eval_d, as_of, evidence_list, domain_coverage, missing_inputs
        )

        # Domain D: News & Policy
        await self._collect_news_evidence(
            stock.symbol, actual_eval_d, as_of, evidence_list, domain_coverage, missing_inputs
        )

        # Domain E: Corporate Announcements
        await self._collect_corporate_evidence(
            stock.symbol, actual_eval_d, as_of, evidence_list, domain_coverage, missing_inputs
        )

        # Domain F: Volume & Delivery
        self._collect_delivery_evidence(
            latest_price, prices, actual_eval_d, evidence_list, domain_coverage, missing_inputs
        )

        # Domain G: Sector Context
        self._collect_sector_evidence(
            stock, sector_name, actual_eval_d, evidence_list, domain_coverage, missing_inputs
        )

        # Domain H: Market Regime Context
        await self._collect_market_context_evidence(
            actual_eval_d, evidence_list, domain_coverage, missing_inputs
        )

        # 4. Classify Factors
        supporting_factors: list[FactorItem] = []
        risk_factors: list[FactorItem] = []
        watch_items: list[FactorItem] = []

        for ev in evidence_list:
            item = FactorItem(
                domain=ev.domain,
                factor=ev.factor,
                description=ev.description,
                direction=ev.direction,
                value=ev.value,
                source=ev.source,
            )
            if ev.direction == EvidenceDirection.POSITIVE:
                supporting_factors.append(item)
            elif ev.direction == EvidenceDirection.NEGATIVE:
                risk_factors.append(item)
            elif ev.direction == EvidenceDirection.UNAVAILABLE:
                watch_items.append(item)
            elif ev.direction == EvidenceDirection.NEUTRAL:
                pass

        # 5. Compute Completeness (Availability across the 8 domains)
        total_domains = len(EvidenceDomain)
        available_domains_count = sum(
            1 for d, status in domain_coverage.items() if status in ("AVAILABLE", "PARTIAL")
        )
        completeness_pct = round((available_domains_count / total_domains) * 100.0, 1)

        if completeness_pct >= 75.0:
            completeness_label = "ROBUST"
        elif completeness_pct >= 50.0:
            completeness_label = "PARTIAL"
        elif completeness_pct >= 25.0:
            completeness_label = "LOW"
        else:
            completeness_label = "CRITICAL_GAPS"

        # 6. Deterministic Overall State & Confidence Aggregation
        p_count = len(supporting_factors)
        r_count = len(risk_factors)
        active_evidence_count = p_count + r_count + sum(
            1 for e in evidence_list if e.direction == EvidenceDirection.NEUTRAL and e.status != "UNAVAILABLE"
        )

        overall_state, state_reason, confidence, confidence_label = self._determine_overall_state(
            p_count=p_count,
            r_count=r_count,
            active_count=active_evidence_count,
            available_domains_count=available_domains_count,
        )

        # Quality warnings
        quality_warnings: list[str] = []
        if completeness_pct < 50.0:
            quality_warnings.append("LOW_DATA_COMPLETENESS")
        if "delivery" in missing_inputs:
            quality_warnings.append("DELIVERY_DATA_UNAVAILABLE")
        if "fundamental" in missing_inputs:
            quality_warnings.append("FINANCIAL_STATEMENTS_UNAVAILABLE")

        return StockIntelligence(
            symbol=stock.symbol,
            stock_id=stock.id,
            company_name=stock.company_name or stock.symbol,
            evaluation_date=actual_eval_d,
            as_of_date=as_of,
            overall_state=overall_state,
            state_reason=state_reason,
            confidence=confidence,
            confidence_label=confidence_label,
            completeness_pct=completeness_pct,
            completeness_label=completeness_label,
            supporting_factors=supporting_factors,
            risk_factors=risk_factors,
            watch_items=watch_items,
            missing_inputs=missing_inputs,
            evidence=evidence_list,
            domain_coverage=domain_coverage,
            quality_warnings=quality_warnings,
            provenance={
                "evaluation_date": actual_eval_d.isoformat(),
                "as_of_date": as_of.isoformat(),
                "price_sessions_used": len(prices),
                "domains_evaluated": total_domains,
                "domains_available": available_domains_count,
                **(
                    {"sector_source": "ind_niftytotalmarket_list.csv#69ee01618f0a6a9ca57f63522329d03f4c179cf8cf0da29b5b80804f008dc1ac"}
                    if stock.sector_id is not None
                    else {}
                ),
            },
        )

    # ---------------------------------------------------------------------------
    # Domain Collectors
    # ---------------------------------------------------------------------------

    async def _collect_technical_evidence(
        self,
        stock_id: int,
        latest_price: DailyPrice,
        prices: list[DailyPrice],
        eval_d: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        close = float(latest_price.close)
        closes = [float(p.close) for p in prices]

        # Calculate moving averages
        ema20 = sum(closes[:20]) / min(len(closes), 20) if len(closes) >= 10 else None
        ema50 = sum(closes[:50]) / min(len(closes), 50) if len(closes) >= 30 else None
        ema200 = sum(closes[:200]) / min(len(closes), 200) if len(closes) >= 100 else None

        # Fetch latest indicator & analytics
        stmt_ind = (
            select(IndicatorResult)
            .where(IndicatorResult.stock_id == stock_id)
            .where(IndicatorResult.trade_date <= eval_d)
            .order_by(IndicatorResult.trade_date.desc())
            .limit(1)
        )
        ind: IndicatorResult | None = (await self.session.execute(stmt_ind)).scalar_one_or_none()

        stmt_an = (
            select(TechnicalAnalysis)
            .where(TechnicalAnalysis.stock_id == stock_id)
            .where(TechnicalAnalysis.trade_date <= eval_d)
            .order_by(TechnicalAnalysis.trade_date.desc())
            .limit(1)
        )
        an: TechnicalAnalysis | None = (await self.session.execute(stmt_an)).scalar_one_or_none()

        has_tech = False

        # EMA20 Factor
        if ema20 is not None:
            has_tech = True
            if close > ema20:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="PRICE_ABOVE_EMA20",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Price ({close:.2f}) is trading above 20-day EMA ({ema20:.2f}), signaling short-term strength.",
                    value=round(close - ema20, 2),
                    evaluation_date=eval_d,
                    source="DailyPrice / TechnicalEngine",
                    status="AVAILABLE",
                ))
            elif close < ema20:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="PRICE_BELOW_EMA20",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Price ({close:.2f}) is trading below 20-day EMA ({ema20:.2f}), signaling short-term weakness.",
                    value=round(close - ema20, 2),
                    evaluation_date=eval_d,
                    source="DailyPrice / TechnicalEngine",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="PRICE_AT_EMA20",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Price ({close:.2f}) is trading exactly at 20-day EMA.",
                    value=0.0,
                    evaluation_date=eval_d,
                    source="DailyPrice / TechnicalEngine",
                    status="AVAILABLE",
                ))
        else:
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.TECHNICAL.value,
                factor="EMA20_INSUFFICIENT_HISTORY",
                direction=EvidenceDirection.UNAVAILABLE,
                description="20-day EMA is unavailable due to insufficient price history.",
                evaluation_date=eval_d,
                source="DailyPrice",
                status="UNAVAILABLE",
            ))

        # EMA50 Factor
        if ema50 is not None:
            has_tech = True
            if close > ema50:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="PRICE_ABOVE_EMA50",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Price ({close:.2f}) is above 50-day EMA ({ema50:.2f}), confirming intermediate uptrend.",
                    value=round(close - ema50, 2),
                    evaluation_date=eval_d,
                    source="DailyPrice / TechnicalEngine",
                    status="AVAILABLE",
                ))
            elif close < ema50:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="PRICE_BELOW_EMA50",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Price ({close:.2f}) is below 50-day EMA ({ema50:.2f}), confirming intermediate downtrend.",
                    value=round(close - ema50, 2),
                    evaluation_date=eval_d,
                    source="DailyPrice / TechnicalEngine",
                    status="AVAILABLE",
                ))
        else:
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.TECHNICAL.value,
                factor="EMA50_INSUFFICIENT_HISTORY",
                direction=EvidenceDirection.UNAVAILABLE,
                description="50-day EMA is unavailable due to limited historical sessions.",
                evaluation_date=eval_d,
                source="DailyPrice",
                status="UNAVAILABLE",
            ))

        # EMA200 Factor
        if ema200 is not None:
            has_tech = True
            if close > ema200:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="PRICE_ABOVE_EMA200",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Price ({close:.2f}) is above 200-day EMA ({ema200:.2f}) in long-term bull regime.",
                    value=round(close - ema200, 2),
                    evaluation_date=eval_d,
                    source="DailyPrice / TechnicalEngine",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="PRICE_BELOW_EMA200",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Price ({close:.2f}) is below 200-day EMA ({ema200:.2f}) in long-term bear regime.",
                    value=round(close - ema200, 2),
                    evaluation_date=eval_d,
                    source="DailyPrice / TechnicalEngine",
                    status="AVAILABLE",
                ))
        else:
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.TECHNICAL.value,
                factor="EMA200_INSUFFICIENT_HISTORY",
                direction=EvidenceDirection.UNAVAILABLE,
                description="200-day EMA is unavailable (requires 100+ trading sessions).",
                evaluation_date=eval_d,
                source="DailyPrice",
                status="UNAVAILABLE",
            ))

        # RSI (14)
        if ind and ind.rsi_14 is not None:
            has_tech = True
            rsi_val = float(ind.rsi_14)
            if rsi_val >= 70.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="RSI_OVERBOUGHT",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"14-day RSI is overbought at {rsi_val:.1f} (exhaustion risk).",
                    value=rsi_val,
                    evaluation_date=eval_d,
                    source="IndicatorEngine",
                    status="AVAILABLE",
                ))
            elif rsi_val <= 30.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="RSI_OVERSOLD",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"14-day RSI is oversold at {rsi_val:.1f} (potential mean-reversion).",
                    value=rsi_val,
                    evaluation_date=eval_d,
                    source="IndicatorEngine",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="RSI_NEUTRAL",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"14-day RSI is neutral at {rsi_val:.1f}.",
                    value=rsi_val,
                    evaluation_date=eval_d,
                    source="IndicatorEngine",
                    status="AVAILABLE",
                ))

        # Supertrend
        if ind and ind.supertrend_direction is not None:
            has_tech = True
            if ind.supertrend_direction == 1:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="SUPERTREND_BULLISH",
                    direction=EvidenceDirection.POSITIVE,
                    description="Supertrend indicator is bullish.",
                    value=1,
                    evaluation_date=eval_d,
                    source="IndicatorEngine",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="SUPERTREND_BEARISH",
                    direction=EvidenceDirection.NEGATIVE,
                    description="Supertrend indicator is bearish.",
                    value=-1,
                    evaluation_date=eval_d,
                    source="IndicatorEngine",
                    status="AVAILABLE",
                ))

        # Analytics Trend Rating
        if an and getattr(an, "trend_rating", None):
            has_tech = True
            trend = str(an.trend_rating).upper()
            if "BULL" in trend or "UP" in trend:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="TECHNICAL_TREND_BULLISH",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Technical analysis rating is {trend}.",
                    value=trend,
                    evaluation_date=eval_d,
                    source="TechnicalAnalysisEngine",
                    status="AVAILABLE",
                ))
            elif "BEAR" in trend or "DOWN" in trend:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.TECHNICAL.value,
                    factor="TECHNICAL_TREND_BEARISH",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Technical analysis rating is {trend}.",
                    value=trend,
                    evaluation_date=eval_d,
                    source="TechnicalAnalysisEngine",
                    status="AVAILABLE",
                ))

        if has_tech:
            domain_coverage[EvidenceDomain.TECHNICAL.value] = "AVAILABLE"
        else:
            domain_coverage[EvidenceDomain.TECHNICAL.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.TECHNICAL.value)

    async def _collect_fundamental_evidence(
        self,
        symbol: str,
        eval_d: date,
        as_of: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        fund_snap = None
        try:
            fund_snap = await self.fund_svc.get_company_fundamental_snapshot(
                symbol=symbol, evaluation_date=eval_d, as_of_date=as_of
            )
        except Exception:
            fund_snap = None

        if not fund_snap or not (fund_snap.latest_quarterly or fund_snap.latest_annual):
            domain_coverage[EvidenceDomain.FUNDAMENTAL.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.FUNDAMENTAL.value)
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.FUNDAMENTAL.value,
                factor="FUNDAMENTALS_UNAVAILABLE",
                direction=EvidenceDirection.UNAVAILABLE,
                description="Financial statement disclosures are unavailable on or before evaluation date.",
                evaluation_date=eval_d,
                source="FundamentalCorporateService",
                status="UNAVAILABLE",
            ))
            return

        domain_coverage[EvidenceDomain.FUNDAMENTAL.value] = "AVAILABLE"
        latest_stmt = fund_snap.latest_quarterly or fund_snap.latest_annual
        assert latest_stmt is not None

        # Revenue Growth (YoY or QoQ)
        rev_g = None
        if fund_snap.growth:
            rev_g = (
                float(fund_snap.growth.revenue_growth_yoy)
                if fund_snap.growth.revenue_growth_yoy is not None
                else (
                    float(fund_snap.growth.revenue_growth_qoq)
                    if fund_snap.growth.revenue_growth_qoq is not None
                    else None
                )
            )

        if rev_g is not None:
            if rev_g >= 10.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="REVENUE_GROWTH_STRONG",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Strong revenue growth of {rev_g:+.1f}%.",
                    value=rev_g,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            elif rev_g <= -5.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="REVENUE_GROWTH_NEGATIVE",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Revenue contraction of {rev_g:+.1f}%.",
                    value=rev_g,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="REVENUE_GROWTH_MODEST",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Modest revenue growth of {rev_g:+.1f}%.",
                    value=rev_g,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))

        # Net Profit (PAT) Growth (YoY or QoQ)
        pat_g = None
        if fund_snap.growth:
            pat_g = (
                float(fund_snap.growth.profit_growth_yoy)
                if fund_snap.growth.profit_growth_yoy is not None
                else (
                    float(fund_snap.growth.profit_growth_qoq)
                    if fund_snap.growth.profit_growth_qoq is not None
                    else None
                )
            )

        if pat_g is not None:
            if pat_g >= 10.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="PROFIT_GROWTH_STRONG",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Strong net profit growth of {pat_g:+.1f}%.",
                    value=pat_g,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            elif pat_g <= -5.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="PROFIT_GROWTH_NEGATIVE",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Net profit contraction of {pat_g:+.1f}%.",
                    value=pat_g,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="PROFIT_GROWTH_STABLE",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Stable net profit performance ({pat_g:+.1f}%).",
                    value=pat_g,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))

        # Return on Equity (ROE)
        if fund_snap.margins and fund_snap.margins.roe_pct is not None:
            roe = float(fund_snap.margins.roe_pct)
            if roe >= 15.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="ROE_STRONG",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"High Return on Equity (ROE) of {roe:.1f}%.",
                    value=roe,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            elif roe < 8.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="ROE_WEAK",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Subdued Return on Equity (ROE) of {roe:.1f}%.",
                    value=roe,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="ROE_MODERATE",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Moderate Return on Equity (ROE) of {roe:.1f}%.",
                    value=roe,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))

        # Leverage (Debt-to-Equity)
        if fund_snap.solvency_cashflow and fund_snap.solvency_cashflow.debt_to_equity is not None:
            de = float(fund_snap.solvency_cashflow.debt_to_equity)
            if de <= 0.5:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="DEBT_EQUITY_CONSERVATIVE",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Conservative balance sheet leverage (D/E ratio: {de:.2f}).",
                    value=de,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            elif de > 1.5:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="DEBT_EQUITY_ELEVATED",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Elevated debt-to-equity leverage (D/E ratio: {de:.2f}).",
                    value=de,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.FUNDAMENTAL.value,
                    factor="DEBT_EQUITY_MODERATE",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Moderate debt-to-equity leverage ({de:.2f}).",
                    value=de,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
        elif latest_stmt.total_debt == Decimal("0"):
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.FUNDAMENTAL.value,
                factor="ZERO_DEBT",
                direction=EvidenceDirection.POSITIVE,
                description="Company reports zero total debt on balance sheet.",
                value=0.0,
                evaluation_date=eval_d,
                source="FundamentalCorporateService",
                status="AVAILABLE",
            ))

    async def _collect_valuation_evidence(
        self,
        symbol: str,
        eval_d: date,
        as_of: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        fund_snap = None
        try:
            fund_snap = await self.fund_svc.get_company_fundamental_snapshot(
                symbol=symbol, evaluation_date=eval_d, as_of_date=as_of
            )
        except Exception:
            fund_snap = None

        has_val = False
        if fund_snap and fund_snap.valuation:
            val = fund_snap.valuation
            if val.pe_ratio is not None:
                has_val = True
                pe = float(val.pe_ratio)
                if pe < 15.0:
                    evidence.append(EvidenceItem(
                        domain=EvidenceDomain.VALUATION.value,
                        factor="PE_RATIO_FAVORABLE",
                        direction=EvidenceDirection.POSITIVE,
                        description=f"P/E ratio is attractive at {pe:.1f}x.",
                        value=pe,
                        evaluation_date=eval_d,
                        source="FundamentalCorporateService",
                        status="AVAILABLE",
                    ))
                elif pe > 50.0:
                    evidence.append(EvidenceItem(
                        domain=EvidenceDomain.VALUATION.value,
                        factor="PE_RATIO_ELEVATED",
                        direction=EvidenceDirection.NEGATIVE,
                        description=f"P/E ratio is high at {pe:.1f}x.",
                        value=pe,
                        evaluation_date=eval_d,
                        source="FundamentalCorporateService",
                        status="AVAILABLE",
                    ))
                else:
                    evidence.append(EvidenceItem(
                        domain=EvidenceDomain.VALUATION.value,
                        factor="PE_RATIO_REASONABLE",
                        direction=EvidenceDirection.NEUTRAL,
                        description=f"P/E ratio is reasonable at {pe:.1f}x.",
                        value=pe,
                        evaluation_date=eval_d,
                        source="FundamentalCorporateService",
                        status="AVAILABLE",
                    ))

        # Relative Valuation vs Peers / Industry
        if fund_snap and fund_snap.relative_valuation and fund_snap.relative_valuation.valuation_classification:
            has_val = True
            rel_class = fund_snap.relative_valuation.valuation_classification
            prem_pct = (
                float(fund_snap.relative_valuation.pe_premium_discount_pct)
                if fund_snap.relative_valuation.pe_premium_discount_pct is not None
                else None
            )
            if rel_class == "ATTRACTIVE":
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.VALUATION.value,
                    factor="RELATIVE_VALUATION_DISCOUNT",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Trading at an attractive valuation discount relative to {fund_snap.relative_valuation.peer_group_type.lower()} median ({prem_pct:+.1f}% vs peers).",
                    value=prem_pct,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            elif rel_class == "EXPENSIVE":
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.VALUATION.value,
                    factor="RELATIVE_VALUATION_PREMIUM",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Trading at a premium relative to {fund_snap.relative_valuation.peer_group_type.lower()} median ({prem_pct:+.1f}% vs peers).",
                    value=prem_pct,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.VALUATION.value,
                    factor="RELATIVE_VALUATION_IN_LINE",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Valuation is aligned with {fund_snap.relative_valuation.peer_group_type.lower()} peer median.",
                    value=prem_pct,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService",
                    status="AVAILABLE",
                ))
        else:
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.VALUATION.value,
                factor="RELATIVE_VALUATION_UNAVAILABLE",
                direction=EvidenceDirection.UNAVAILABLE,
                description="Peer group relative valuation comparison is unavailable.",
                evaluation_date=eval_d,
                source="FundamentalCorporateService",
                status="UNAVAILABLE",
            ))

        if has_val:
            domain_coverage[EvidenceDomain.VALUATION.value] = "AVAILABLE"
        else:
            domain_coverage[EvidenceDomain.VALUATION.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.VALUATION.value)

    async def _collect_news_evidence(
        self,
        symbol: str,
        eval_d: date,
        as_of: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        eval_dt = datetime.combine(eval_d, datetime.min.time(), tzinfo=UTC)
        news_snap = None
        try:
            news_snap = await self.news_svc.get_company_news_policy_snapshot(
                symbol=symbol, evaluation_datetime=eval_dt, as_of_date=as_of
            )
        except Exception:
            news_snap = None

        if not news_snap or not news_snap.recent_events:
            domain_coverage[EvidenceDomain.NEWS.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.NEWS.value)
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.NEWS.value,
                factor="NEWS_POLICY_UNAVAILABLE",
                direction=EvidenceDirection.UNAVAILABLE,
                description="No verified news or policy events recorded on or before evaluation date.",
                evaluation_date=eval_d,
                source="NewsPolicyService",
                status="UNAVAILABLE",
            ))
            return

        domain_coverage[EvidenceDomain.NEWS.value] = "AVAILABLE"
        pos_cnt = news_snap.positive_context_count
        neg_cnt = news_snap.negative_context_count

        if pos_cnt > neg_cnt or news_snap.policy_tailwind:
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.NEWS.value,
                factor="NEWS_SENTIMENT_POSITIVE",
                direction=EvidenceDirection.POSITIVE,
                description=f"Positive news & policy context ({pos_cnt} positive vs {neg_cnt} negative context events).",
                value=pos_cnt,
                evaluation_date=eval_d,
                source="NewsPolicyService",
                status="AVAILABLE",
            ))
        elif neg_cnt > pos_cnt or news_snap.policy_headwind or news_snap.regulatory_flag:
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.NEWS.value,
                factor="NEWS_SENTIMENT_NEGATIVE",
                direction=EvidenceDirection.NEGATIVE,
                description=f"Adverse news & policy context ({neg_cnt} negative vs {pos_cnt} positive context events).",
                value=neg_cnt,
                evaluation_date=eval_d,
                source="NewsPolicyService",
                status="AVAILABLE",
            ))
        else:
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.NEWS.value,
                factor="NEWS_SENTIMENT_BALANCED",
                direction=EvidenceDirection.NEUTRAL,
                description=f"Balanced news & policy sentiment across {len(news_snap.recent_events)} event(s).",
                value=0,
                evaluation_date=eval_d,
                source="NewsPolicyService",
                status="AVAILABLE",
            ))

    async def _collect_corporate_evidence(
        self,
        symbol: str,
        eval_d: date,
        as_of: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        anns: list[dict] = []
        try:
            anns = await self.fund_svc.get_corporate_announcements(
                symbol=symbol, as_of_date=eval_d, limit=5
            )
        except Exception:
            anns = []

        if not anns:
            domain_coverage[EvidenceDomain.CORPORATE_EVENTS.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.CORPORATE_EVENTS.value)
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.CORPORATE_EVENTS.value,
                factor="CORPORATE_EVENTS_UNAVAILABLE",
                direction=EvidenceDirection.UNAVAILABLE,
                description="No corporate announcements recorded on or before evaluation date.",
                evaluation_date=eval_d,
                source="FundamentalCorporateService",
                status="UNAVAILABLE",
            ))
            return

        domain_coverage[EvidenceDomain.CORPORATE_EVENTS.value] = "AVAILABLE"
        positive_cats = {"DIVIDEND", "BONUS", "BUYBACK", "CAPEX", "ORDER_WIN", "EXPANSION"}
        adverse_cats = {"LITIGATION", "REGULATORY", "CREDIT_RATING_DOWNGRADE"}

        has_pos = False
        has_neg = False
        for a in anns:
            cat = str(a.get("category") or a.get("event_type") or "").upper()
            title = a.get("title", "")
            if cat in positive_cats:
                has_pos = True
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.CORPORATE_EVENTS.value,
                    factor=f"CORPORATE_{cat}",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"Recent positive corporate event ({cat}): {title}",
                    value=cat,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService / CorporateAnnouncements",
                    status="AVAILABLE",
                ))
            elif cat in adverse_cats:
                has_neg = True
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.CORPORATE_EVENTS.value,
                    factor=f"CORPORATE_{cat}",
                    direction=EvidenceDirection.NEGATIVE,
                    description=f"Recent adverse corporate event ({cat}): {title}",
                    value=cat,
                    evaluation_date=eval_d,
                    source="FundamentalCorporateService / CorporateAnnouncements",
                    status="AVAILABLE",
                ))

        if not has_pos and not has_neg:
            latest_title = anns[0].get("title", "Corporate Announcement")
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.CORPORATE_EVENTS.value,
                factor="CORPORATE_ANNOUNCEMENT_INFORMATIONAL",
                direction=EvidenceDirection.NEUTRAL,
                description=f"Recent corporate disclosure: {latest_title}",
                value=anns[0].get("category"),
                evaluation_date=eval_d,
                source="FundamentalCorporateService / CorporateAnnouncements",
                status="AVAILABLE",
            ))

    def _collect_delivery_evidence(
        self,
        latest_price: DailyPrice,
        prices: list[DailyPrice],
        eval_d: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        if latest_price.delivery_percentage is not None:
            domain_coverage[EvidenceDomain.DELIVERY.value] = "AVAILABLE"
            deliv_pct = float(latest_price.delivery_percentage)
            delivs = [float(p.delivery_percentage) for p in prices if p.delivery_percentage is not None]
            avg_deliv = sum(delivs[:20]) / min(len(delivs), 20) if delivs else deliv_pct

            if deliv_pct >= 50.0 and deliv_pct > avg_deliv * 1.2:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.DELIVERY.value,
                    factor="DELIVERY_ACCUMULATION_STRONG",
                    direction=EvidenceDirection.POSITIVE,
                    description=f"High delivery volume ({deliv_pct:.1f}%) signals strong institutional holding demand.",
                    value=deliv_pct,
                    evaluation_date=eval_d,
                    source="DailyPrice / DeliveryData",
                    status="AVAILABLE",
                ))
            elif deliv_pct < 20.0:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.DELIVERY.value,
                    factor="DELIVERY_VOLUME_LOW",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Low delivery percentage ({deliv_pct:.1f}%) reflects predominant intraday activity.",
                    value=deliv_pct,
                    evaluation_date=eval_d,
                    source="DailyPrice / DeliveryData",
                    status="AVAILABLE",
                ))
            else:
                evidence.append(EvidenceItem(
                    domain=EvidenceDomain.DELIVERY.value,
                    factor="DELIVERY_VOLUME_NORMAL",
                    direction=EvidenceDirection.NEUTRAL,
                    description=f"Standard delivery percentage ({deliv_pct:.1f}%).",
                    value=deliv_pct,
                    evaluation_date=eval_d,
                    source="DailyPrice / DeliveryData",
                    status="AVAILABLE",
                ))
        else:
            domain_coverage[EvidenceDomain.DELIVERY.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.DELIVERY.value)
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.DELIVERY.value,
                factor="DELIVERY_DATA_UNAVAILABLE",
                direction=EvidenceDirection.UNAVAILABLE,
                description="Delivery volume & percentage data is unavailable on evaluation date.",
                evaluation_date=eval_d,
                source="DailyPrice",
                status="UNAVAILABLE",
            ))

    def _collect_sector_evidence(
        self,
        stock: Stock,
        sector_name: str | None,
        eval_d: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        industry_val = stock.industry or (stock.industry_ref.name if stock.industry_ref else None)
        has_canonical_sector = bool(sector_name and sector_name.strip() and sector_name.strip().lower() != "unclassified")

        if has_canonical_sector:
            domain_coverage[EvidenceDomain.SECTOR.value] = "AVAILABLE"
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.SECTOR.value,
                factor="SECTOR_MAPPING_ESTABLISHED",
                direction=EvidenceDirection.NEUTRAL,
                description=f"Security mapped to {sector_name} sector.",
                value=sector_name,
                evaluation_date=eval_d,
                source="ind_niftytotalmarket_list.csv#69ee01618f0a6a9ca57f63522329d03f4c179cf8cf0da29b5b80804f008dc1ac",
                status="AVAILABLE",
            ))
        elif industry_val:
            domain_coverage[EvidenceDomain.SECTOR.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.SECTOR.value)
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.SECTOR.value,
                factor="SECTOR_UNMAPPED",
                direction=EvidenceDirection.UNAVAILABLE,
                description=f"Canonical sector is unclassified (industry mapped to {industry_val}).",
                value=industry_val,
                evaluation_date=eval_d,
                source="StockMaster",
                status="UNAVAILABLE",
            ))
        else:
            domain_coverage[EvidenceDomain.SECTOR.value] = "UNAVAILABLE"
            missing_inputs.append(EvidenceDomain.SECTOR.value)
            evidence.append(EvidenceItem(
                domain=EvidenceDomain.SECTOR.value,
                factor="SECTOR_UNMAPPED",
                direction=EvidenceDirection.UNAVAILABLE,
                description="Sector/Industry classification is unassigned.",
                evaluation_date=eval_d,
                source="StockMaster",
                status="UNAVAILABLE",
            ))

    async def _collect_market_context_evidence(
        self,
        eval_d: date,
        evidence: list[EvidenceItem],
        domain_coverage: dict[str, str],
        missing_inputs: list[str],
    ) -> None:
        try:
            from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
            stmt = (
                select(MarketIntelligenceSnapshot)
                .where(MarketIntelligenceSnapshot.market_date <= eval_d)
                .order_by(MarketIntelligenceSnapshot.market_date.desc())
                .limit(1)
            )
            snap = (await self.session.execute(stmt)).scalar_one_or_none()
            if snap and snap.regime:
                domain_coverage[EvidenceDomain.MARKET_CONTEXT.value] = "AVAILABLE"
                regime = str(snap.regime).upper()
                if "BULL" in regime:
                    evidence.append(EvidenceItem(
                        domain=EvidenceDomain.MARKET_CONTEXT.value,
                        factor="MARKET_REGIME_BULLISH",
                        direction=EvidenceDirection.POSITIVE,
                        description=f"Broader market environment is in a {regime} regime.",
                        value=regime,
                        evaluation_date=eval_d,
                        source="MarketIntelligenceSnapshot",
                        status="AVAILABLE",
                    ))
                elif "BEAR" in regime:
                    evidence.append(EvidenceItem(
                        domain=EvidenceDomain.MARKET_CONTEXT.value,
                        factor="MARKET_REGIME_BEARISH",
                        direction=EvidenceDirection.NEGATIVE,
                        description=f"Broader market environment is in a {regime} regime.",
                        value=regime,
                        evaluation_date=eval_d,
                        source="MarketIntelligenceSnapshot",
                        status="AVAILABLE",
                    ))
                else:
                    evidence.append(EvidenceItem(
                        domain=EvidenceDomain.MARKET_CONTEXT.value,
                        factor="MARKET_REGIME_NEUTRAL",
                        direction=EvidenceDirection.NEUTRAL,
                        description=f"Broader market regime is {regime}.",
                        value=regime,
                        evaluation_date=eval_d,
                        source="MarketIntelligenceSnapshot",
                        status="AVAILABLE",
                    ))
                return
        except Exception:
            pass

        domain_coverage[EvidenceDomain.MARKET_CONTEXT.value] = "UNAVAILABLE"
        missing_inputs.append(EvidenceDomain.MARKET_CONTEXT.value)
        evidence.append(EvidenceItem(
            domain=EvidenceDomain.MARKET_CONTEXT.value,
            factor="MARKET_REGIME_UNAVAILABLE",
            direction=EvidenceDirection.UNAVAILABLE,
            description="Broader market regime context is unavailable for evaluation date.",
            evaluation_date=eval_d,
            source="MarketIntelligence",
            status="UNAVAILABLE",
        ))

    # ---------------------------------------------------------------------------
    # Deterministic Aggregation Engine
    # ---------------------------------------------------------------------------

    @staticmethod
    def _determine_overall_state(
        p_count: int,
        r_count: int,
        active_count: int,
        available_domains_count: int,
    ) -> tuple[OverallState, str, float, str]:
        """Classify deterministic decision context and calculate evidence confidence."""
        MIN_ACTIVE_FACTORS = 3

        if active_count < MIN_ACTIVE_FACTORS:
            confidence = round(min(0.35, active_count / (MIN_ACTIVE_FACTORS * 2.0)), 2)
            return (
                OverallState.INSUFFICIENT_DATA,
                f"Insufficient verified evidence (found {active_count} active factor(s); minimum {MIN_ACTIVE_FACTORS} required).",
                confidence,
                "INSUFFICIENT",
            )

        # Sufficient active evidence exists (active_count >= 3)
        if p_count >= 2 and p_count >= 2 * r_count and p_count > r_count:
            state = OverallState.POSITIVE
            reason = f"Positive evidence clearly dominates across {p_count} supporting factor(s) vs {r_count} risk factor(s)."
            concordance = p_count / (p_count + r_count + 0.5)
        elif r_count >= 2 and r_count >= 2 * p_count and r_count > p_count:
            state = OverallState.NEGATIVE
            reason = f"Risk factors clearly dominate across {r_count} adverse signal(s) vs {p_count} supporting factor(s)."
            concordance = r_count / (p_count + r_count + 0.5)
        elif p_count >= 2 and r_count >= 2:
            state = OverallState.MIXED
            reason = f"Mixed signals present: {p_count} supporting factor(s) and {r_count} risk factor(s) concurrently active."
            concordance = 0.55
        else:
            state = OverallState.NEUTRAL
            reason = f"Balanced signals across {active_count} factor(s) with no directional asymmetry."
            concordance = 0.60

        breadth_weight = min(1.0, max(0.4, available_domains_count / 5.0))
        confidence = round(min(0.95, max(0.20, concordance * breadth_weight)), 2)

        if confidence >= 0.70:
            confidence_label = "HIGH"
        elif confidence >= 0.50:
            confidence_label = "MODERATE"
        elif confidence >= 0.30:
            confidence_label = "LOW"
        else:
            confidence_label = "INSUFFICIENT"

        return state, reason, confidence, confidence_label
