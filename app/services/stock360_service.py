"""Stock 360° unified research snapshot orchestration service — Sprint 19.1G.

Integrates technicals, volume/delivery, relative strength, fundamentals (19.1E),
derivatives knowledge (19.1D), news & government policy (19.1F), corporate disclosures,
and risk flags into a single PIT-safe 10-dimensional research snapshot.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.derivative_contract import DerivativeContract
from app.models.sector import Sector
from app.models.stock import Stock
from app.schemas.stock360 import (
    Stock360Snapshot,
    TechnicalSummaryRead,
    VolumeDeliverySummaryRead,
)
from app.services.fundamental_corporate_service import FundamentalCorporateService
from app.services.news_policy_service import NewsPolicyService
from app.services.relative_strength_service import RelativeStrengthService


class Stock360Service:
    """Orchestrates 10-dimensional Stock 360° research snapshots."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.fundamental_service = FundamentalCorporateService(session)
        self.news_service = NewsPolicyService(session)
        self.rs_service = RelativeStrengthService(session)

    async def get_stock360_snapshot(
        self,
        symbol: str,
        evaluation_date: date | None = None,
        as_of_date: date | None = None,
    ) -> Stock360Snapshot:
        """Build point-in-time safe Stock 360° research snapshot for symbol."""
        eval_d = evaluation_date or date.today()
        as_of = as_of_date or eval_d

        # 1. Fetch stock & sector
        stmt = (
            select(Stock, Sector.name.label("sector_name"))
            .outerjoin(Sector, Stock.sector_id == Sector.id)
            .where(Stock.symbol == symbol.upper())
        )
        res = await self.session.execute(stmt)
        row = res.first()
        if not row:
            raise ValueError(f"Stock symbol '{symbol}' not found")

        stock: Stock = row[0]
        sector_name: str | None = row[1]

        # 2. Check F&O eligibility
        stmt_fo = (
            select(DerivativeContract.id)
            .where(DerivativeContract.underlying_stock_id == stock.id)
            .limit(1)
        )
        is_fno = (await self.session.execute(stmt_fo)).scalar_one_or_none() is not None

        # 3. Technicals & Volume/Delivery summary
        tech, vol_del = await self._calculate_technicals_and_volume(stock.id, eval_d)

        # 4. Relative Strength
        rs_metrics = await self.rs_service.calculate_relative_strength(stock.id, eval_d)

        # 5. Fundamentals & Valuation (19.1E)
        fund_snap = None
        try:
            fund_snap = await self.fundamental_service.get_company_fundamental_snapshot(
                symbol=stock.symbol,
                evaluation_date=eval_d,
                as_of_date=as_of,
            )
        except Exception:
            fund_snap = None

        # 6. News & Government Policy (19.1F)
        eval_dt = datetime.combine(eval_d, datetime.min.time(), tzinfo=UTC)
        news_snap = None
        try:
            news_snap = await self.news_service.get_company_news_policy_snapshot(
                symbol=stock.symbol,
                evaluation_datetime=eval_dt,
                as_of_date=as_of,
            )
        except Exception:
            news_snap = None

        # 7. Derivatives summary (19.1D) if F&O
        deriv_summary = await self._get_derivatives_summary(stock.symbol, eval_d, as_of) if is_fno else {}

        # 8. Data Coverage & Confidence Assessment
        coverage = {
            "technicals": "AVAILABLE" if tech else "NO_DATA",
            "volume_delivery": "AVAILABLE" if vol_del else "NO_DATA",
            "relative_strength": "AVAILABLE" if rs_metrics.rs_trend != "INSUFFICIENT_DATA" else "PARTIAL",
            "fundamentals": "AVAILABLE" if (fund_snap and fund_snap.latest_quarterly) else "NO_DATA",
            "news_policy": "AVAILABLE" if (news_snap and news_snap.recent_events) else "NO_DATA",
            "derivatives": "AVAILABLE" if (is_fno and deriv_summary) else ("NOT_APPLICABLE" if not is_fno else "NO_DATA"),
        }

        # Calculate completeness %
        avail_cnt = sum(1 for v in coverage.values() if v in ("AVAILABLE", "NOT_APPLICABLE"))
        completeness_pct = round((avail_cnt / len(coverage)) * 100.0, 1)

        quality_warnings: list[str] = []
        if completeness_pct < 60.0:
            quality_warnings.append("LOW_DATA_COVERAGE")
        if news_snap and news_snap.quality_warnings:
            quality_warnings.extend(news_snap.quality_warnings)

        return Stock360Snapshot(
            symbol=stock.symbol,
            stock_id=stock.id,
            company_name=stock.company_name or stock.symbol,
            evaluation_date=eval_d,
            as_of_date=as_of,
            sector_name=sector_name,
            is_fno=is_fno,
            technicals=tech,
            volume_delivery=vol_del,
            relative_strength=rs_metrics,
            fundamentals=fund_snap,
            derivatives_summary=deriv_summary,
            news_policy=news_snap,
            data_coverage=coverage,
            data_completeness_pct=completeness_pct,
            quality_warnings=quality_warnings,
            provenance={
                "pit_eval_date": eval_d.isoformat(),
                "pit_as_of_date": as_of.isoformat(),
                "is_fno_security": is_fno,
            },
        )

    async def _calculate_technicals_and_volume(
        self, stock_id: int, eval_d: date
    ) -> tuple[TechnicalSummaryRead | None, VolumeDeliverySummaryRead | None]:
        """Fetch price history and calculate core technical & volume metrics."""
        stmt = (
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock_id)
            .where(DailyPrice.trade_date <= eval_d)
            .order_by(DailyPrice.trade_date.desc())
            .limit(250)
        )
        res = await self.session.execute(stmt)
        prices = list(res.scalars().all())

        if not prices:
            return None, None

        latest = prices[0]
        close = float(latest.close)

        # Calculates 52w High / Low
        highs = [float(p.high) for p in prices if p.high is not None]
        lows = [float(p.low) for p in prices if p.low is not None]
        h52 = max(highs) if highs else close
        l52 = min(lows) if lows else close

        pct_from_h52 = round(((close - h52) / h52) * 100.0, 2) if h52 > 0 else 0.0
        pct_from_l52 = round(((close - l52) / l52) * 100.0, 2) if l52 > 0 else 0.0

        # Simple moving average calculations
        closes = [float(p.close) for p in prices]
        ema20 = sum(closes[:20]) / min(len(closes), 20) if closes else None
        ema50 = sum(closes[:50]) / min(len(closes), 50) if len(closes) >= 20 else None
        ema200 = sum(closes[:200]) / min(len(closes), 200) if len(closes) >= 50 else None

        trend = "SIDEWAYS"
        if ema20 and ema50:
            if close > ema20 and ema20 > ema50:
                trend = "UPTREND"
            elif close < ema20 and ema20 < ema50:
                trend = "DOWNTREND"

        tech = TechnicalSummaryRead(
            close=close,
            ema20=round(ema20, 2) if ema20 else None,
            ema50=round(ema50, 2) if ema50 else None,
            ema200=round(ema200, 2) if ema200 else None,
            trend_state=trend,
            pct_from_52w_high=pct_from_h52,
            pct_from_52w_low=pct_from_l52,
        )

        # Volume & Delivery
        vols = [p.volume for p in prices if p.volume is not None]
        delivs = [float(p.delivery_percentage) for p in prices if p.delivery_percentage is not None]

        avg_vol20 = sum(vols[:20]) / min(len(vols), 20) if vols else None
        rvol = round(latest.volume / avg_vol20, 2) if (avg_vol20 and avg_vol20 > 0) else 1.0

        deliv_pct = float(latest.delivery_percentage) if latest.delivery_percentage is not None else None
        avg_deliv20 = sum(delivs[:20]) / min(len(delivs), 20) if delivs else None

        vol_del = VolumeDeliverySummaryRead(
            volume=latest.volume,
            avg_volume_20d=round(avg_vol20, 0) if avg_vol20 else None,
            relative_volume_20d=rvol,
            delivery_pct=round(deliv_pct, 2) if deliv_pct is not None else None,
            avg_delivery_pct_20d=round(avg_deliv20, 2) if avg_deliv20 else None,
            volume_breakout=rvol >= 1.8,
            delivery_breakout=bool(deliv_pct and avg_deliv20 and deliv_pct > (avg_deliv20 * 1.25)),
            accumulation_signal=bool(rvol >= 1.5 and deliv_pct and deliv_pct >= 50.0),
        )

        return tech, vol_del

    async def _get_derivatives_summary(self, symbol: str, eval_d: date, as_of: date) -> dict[str, Any]:
        """Fetch basic derivatives analytics summary & advanced options context if available."""
        res: dict[str, Any] = {}
        try:
            from app.services.historical_options_analytics import HistoricalOptionsAnalyticsService
            service = HistoricalOptionsAnalyticsService(self.session)
            analytics = await service.get_expiry_options_analytics(symbol=symbol, trade_date=eval_d, as_of_date=as_of)
            if analytics:
                res.update({
                    "overall_pcr": analytics.overall_pcr,
                    "max_pain": analytics.max_pain,
                    "highest_call_oi_strike": analytics.highest_call_oi_strike,
                    "highest_put_oi_strike": analytics.highest_put_oi_strike,
                })
        except Exception:
            pass

        try:
            from app.services.advanced_options_service import AdvancedOptionsService
            adv_service = AdvancedOptionsService(self.session)
            adv_ctx = await adv_service.get_advanced_options_context(symbol=symbol, trade_date=eval_d, as_of_date=as_of)
            if adv_ctx and adv_ctx.is_fno_security:
                res.update({
                    "atm_iv": adv_ctx.atm_iv,
                    "iv_regime": adv_ctx.iv_regime,
                    "expected_move_pct": adv_ctx.expected_move_pct,
                    "skew_spread": adv_ctx.skew_spread,
                    "term_structure_slope": adv_ctx.term_structure_slope,
                })
        except Exception:
            pass

        try:
            from app.services.advanced_exposure_service import AdvancedExposureService
            exp_service = AdvancedExposureService(self.session)
            exp_ctx = await exp_service.get_advanced_exposure_context(symbol=symbol, trade_date=eval_d, as_of_date=as_of)
            if exp_ctx and exp_ctx.is_fno_security:
                res.update({
                    "gross_total_gex": exp_ctx.gross_total_gex,
                    "scenario_net_gex": exp_ctx.scenario_net_gex,
                    "gamma_flip_level": exp_ctx.gamma_flip_level,
                    "distance_to_gamma_flip_pct": exp_ctx.distance_to_gamma_flip_pct,
                    "gross_total_dex": exp_ctx.gross_total_dex,
                    "top1_gamma_strike": exp_ctx.top1_gamma_strike,
                })
        except Exception:
            pass

        return res
