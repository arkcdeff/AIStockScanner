from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import is_product_symbol
from app.repositories.stock_detail import StockDetailRepository
from app.schemas.stock_detail import (
    DataHealthSummary,
    FundamentalsOverviewResponse,
    PerformanceReturns,
    ScannerSnapshotResponse,
    TechnicalSnapshotResponse,
    ValuationOverviewResponse,
)


class StockDetailService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = StockDetailRepository(session)

    async def get_overview(self, symbol: str) -> dict[str, Any] | None:
        if not is_product_symbol(symbol):
            return None
        stock = await self.repo.get_stock(symbol)
        if not stock:
            return None

        sector_name = None
        if stock.sector_ref:
            sector_name = stock.sector_ref.name

        latest_price = await self.repo.get_latest_price(stock.id)
        history = await self.repo.get_history_summary(stock.id)
        if not latest_price:
            return {
                "stock": stock,
                "sector_name": sector_name,
                "latest_price": None,
                "price_change": None,
                "price_change_pct": None,
                "avg_volume_20d": None,
                "avg_delivery_20d": None,
                "latest_indicator": None,
                "latest_analytics": None,
                "scanner_matches": [],
                "history_sessions": history["sessions"],
                "history_first_date": history["first_date"],
            }

        averages = await self.repo.get_20d_averages(stock.id, latest_price.trade_date)
        latest_indicator = await self.repo.get_latest_indicator(stock.id)
        latest_analytics = await self.repo.get_latest_analytics(stock.id)
        eval_date = latest_price.trade_date if latest_price else None
        scanner_matches = await self.repo.get_scanner_matches(stock.id, as_of_date=eval_date)

        price_change = None
        price_change_pct = None
        if latest_price.previous_close and latest_price.previous_close > 0:
            price_change = float(latest_price.close - latest_price.previous_close)
            price_change_pct = (price_change / float(latest_price.previous_close)) * 100

        return {
            "stock": stock,
            "sector_name": sector_name,
            "latest_price": latest_price,
            "price_change": price_change,
            "price_change_pct": price_change_pct,
            "avg_volume_20d": averages.get("avg_volume"),
            "avg_delivery_20d": averages.get("avg_delivery"),
            "latest_indicator": latest_indicator,
            "latest_analytics": latest_analytics,
            "scanner_matches": scanner_matches,
            "history_sessions": history["sessions"],
            "history_first_date": history["first_date"],
        }

    async def get_stock360_overview(self, symbol: str) -> dict[str, Any] | None:
        """Assemble all Stock360 data in a single service call."""
        overview = await self.get_overview(symbol)
        if overview is None:
            return None

        stock = overview["stock"]
        latest_price = overview["latest_price"]
        eval_date = latest_price.trade_date if latest_price else None

        # Technical snapshot
        tech_snapshot = self._build_technical_snapshot(
            overview["latest_indicator"], overview["latest_analytics"]
        )

        # Scanner snapshot
        scanner_summary = await self.repo.get_scanner_match_summary(stock.id, as_of_date=eval_date)
        scanner_snapshot = ScannerSnapshotResponse(**scanner_summary)

        # Performance returns
        perf = PerformanceReturns()
        if latest_price:
            returns = await self.repo.get_period_returns(stock.id, latest_price.trade_date)
            perf = PerformanceReturns(**returns)

        # Data health
        health_data = await self.repo.get_data_health(stock.id)
        data_health = DataHealthSummary(**health_data)

        # Fundamentals & Valuation (Stage 2.6.2)
        fundamentals, valuation = await self._build_fundamentals_and_valuation(
            stock, latest_price
        )

        return {
            **overview,
            "technical_snapshot": tech_snapshot,
            "scanner_snapshot": scanner_snapshot,
            "performance": perf,
            "data_health": data_health,
            "fundamentals": fundamentals,
            "valuation": valuation,
        }

    async def _build_fundamentals_and_valuation(
        self, stock: Any, latest_price: Any | None
    ) -> tuple[FundamentalsOverviewResponse, ValuationOverviewResponse]:
        from datetime import date
        from decimal import Decimal

        from app.repositories.fundamental_corporate import FundamentalCorporateRepository
        from app.schemas.stock_detail import FinancialStatementItem
        from app.services.fundamental_corporate_service import FundamentalCorporateService

        eval_d = latest_price.trade_date if latest_price else date.today()
        close_price = float(latest_price.close) if latest_price else None

        fund_repo = FundamentalCorporateRepository(self.session)
        fund_svc = FundamentalCorporateService(self.session)

        # 1. Fetch statements history
        basis = "CONSOLIDATED"
        history = await fund_repo.get_financial_history(
            stock_id=stock.id,
            period_type=None,
            statement_basis="CONSOLIDATED",
            as_of_date=eval_d,
            limit=8,
        )
        if not history:
            history = await fund_repo.get_financial_history(
                stock_id=stock.id,
                period_type=None,
                statement_basis="STANDALONE",
                as_of_date=eval_d,
                limit=8,
            )
            if history:
                basis = "STANDALONE"

        if not history:
            return (
                FundamentalsOverviewResponse(
                    available=False,
                    symbol=stock.symbol,
                    statement_basis="CONSOLIDATED",
                ),
                ValuationOverviewResponse(
                    available=False,
                    symbol=stock.symbol,
                    evaluation_date=eval_d,
                    close_price=close_price,
                    statement_basis="CONSOLIDATED",
                ),
            )

        # 2. Get PIT fundamental snapshot
        snapshot = await fund_svc.get_company_fundamental_snapshot(
            symbol=stock.symbol,
            evaluation_date=eval_d,
            as_of_date=eval_d,
            statement_basis=basis,
        )

        # Map recent statements
        recent_items = []
        for stmt in history:
            recent_items.append(
                FinancialStatementItem(
                    period_end=stmt.period_end,
                    period_type=stmt.period_type,
                    statement_basis=stmt.statement_basis,
                    revenue=float(stmt.revenue) if stmt.revenue is not None else None,
                    operating_income=float(stmt.operating_income) if stmt.operating_income is not None else None,
                    ebit=float(stmt.ebit) if stmt.ebit is not None else None,
                    pbt=float(stmt.pbt) if stmt.pbt is not None else None,
                    pat=float(stmt.pat) if stmt.pat is not None else None,
                    eps=float(stmt.eps) if stmt.eps is not None else None,
                    total_equity=float(stmt.total_equity) if stmt.total_equity is not None else None,
                    total_debt=float(stmt.total_debt) if stmt.total_debt is not None else None,
                    operating_cash_flow=float(stmt.operating_cash_flow) if stmt.operating_cash_flow is not None else None,
                    free_cash_flow=float(stmt.free_cash_flow) if stmt.free_cash_flow is not None else None,
                    available_at=stmt.available_at,
                    reported_at=stmt.reported_at,
                )
            )

        latest_stmt = snapshot.latest_quarterly or snapshot.latest_annual

        # Descriptors
        roe_val = float(snapshot.margins.roe_pct) if (snapshot.margins and snapshot.margins.roe_pct is not None) else None
        roe_desc = None
        if roe_val is not None:
            if roe_val >= 15.0:
                roe_desc = "Strong"
            elif roe_val >= 10.0:
                roe_desc = "Moderate"
            else:
                roe_desc = "Weak"

        rev_growth_yoy = float(snapshot.growth.revenue_growth_yoy) if (snapshot.growth and snapshot.growth.revenue_growth_yoy is not None) else None
        rev_desc = "Insufficient history" if rev_growth_yoy is None else ("Improving" if rev_growth_yoy >= 5.0 else ("Declining" if rev_growth_yoy <= -5.0 else "Stable"))

        pat_growth_yoy = float(snapshot.growth.profit_growth_yoy) if (snapshot.growth and snapshot.growth.profit_growth_yoy is not None) else None
        pat_desc = "Insufficient history" if pat_growth_yoy is None else ("Improving" if pat_growth_yoy >= 5.0 else ("Declining" if pat_growth_yoy <= -5.0 else "Stable"))

        fund_resp = FundamentalsOverviewResponse(
            available=True,
            symbol=stock.symbol,
            statement_basis=basis,
            latest_period_end=latest_stmt.period_end if latest_stmt else None,
            latest_period_type=latest_stmt.period_type if latest_stmt else None,
            reported_at=latest_stmt.reported_at if latest_stmt else None,
            available_at=latest_stmt.available_at if latest_stmt else None,
            revenue=float(latest_stmt.revenue) if (latest_stmt and latest_stmt.revenue is not None) else None,
            operating_income=float(latest_stmt.operating_income) if (latest_stmt and latest_stmt.operating_income is not None) else None,
            pbt=float(latest_stmt.pbt) if (latest_stmt and latest_stmt.pbt is not None) else None,
            pat=float(latest_stmt.pat) if (latest_stmt and latest_stmt.pat is not None) else None,
            eps=float(latest_stmt.eps) if (latest_stmt and latest_stmt.eps is not None) else None,
            revenue_growth_yoy=rev_growth_yoy,
            revenue_growth_qoq=float(snapshot.growth.revenue_growth_qoq) if (snapshot.growth and snapshot.growth.revenue_growth_qoq is not None) else None,
            profit_growth_yoy=pat_growth_yoy,
            profit_growth_qoq=float(snapshot.growth.profit_growth_qoq) if (snapshot.growth and snapshot.growth.profit_growth_qoq is not None) else None,
            eps_growth_yoy=float(snapshot.growth.eps_growth_yoy) if (snapshot.growth and snapshot.growth.eps_growth_yoy is not None) else None,
            operating_margin_pct=float(snapshot.margins.operating_margin_pct) if (snapshot.margins and snapshot.margins.operating_margin_pct is not None) else None,
            net_profit_margin_pct=float(snapshot.margins.net_profit_margin_pct) if (snapshot.margins and snapshot.margins.net_profit_margin_pct is not None) else None,
            roe_pct=roe_val,
            roce_pct=float(snapshot.margins.roce_pct) if (snapshot.margins and snapshot.margins.roce_pct is not None) else None,
            roe_descriptor=roe_desc,
            revenue_descriptor=rev_desc,
            pat_descriptor=pat_desc,
            total_debt=float(latest_stmt.total_debt) if (latest_stmt and latest_stmt.total_debt is not None) else None,
            total_equity=float(latest_stmt.total_equity) if (latest_stmt and latest_stmt.total_equity is not None) else None,
            debt_to_equity=float(snapshot.solvency_cashflow.debt_to_equity) if (snapshot.solvency_cashflow and snapshot.solvency_cashflow.debt_to_equity is not None) else None,
            cash_and_equivalents=float(latest_stmt.cash_and_equivalents) if (latest_stmt and latest_stmt.cash_and_equivalents is not None) else None,
            operating_cash_flow=float(latest_stmt.operating_cash_flow) if (latest_stmt and latest_stmt.operating_cash_flow is not None) else None,
            free_cash_flow=float(snapshot.solvency_cashflow.free_cash_flow) if (snapshot.solvency_cashflow and snapshot.solvency_cashflow.free_cash_flow is not None) else None,
            operating_cash_flow_to_pat=float(snapshot.solvency_cashflow.operating_cash_flow_to_pat) if (snapshot.solvency_cashflow and snapshot.solvency_cashflow.operating_cash_flow_to_pat is not None) else None,
            recent_statements=recent_items,
            quality_warnings=snapshot.quality_warnings or [],
        )

        # Book value per share calculation
        bvps = None
        if latest_stmt and latest_stmt.shares_outstanding and latest_stmt.shares_outstanding > 0 and latest_stmt.total_equity:
            bvps = float((latest_stmt.total_equity * Decimal("10000000")) / latest_stmt.shares_outstanding)

        val_resp = ValuationOverviewResponse(
            available=True if (snapshot.valuation and (snapshot.valuation.pe_ratio is not None or snapshot.valuation.pb_ratio is not None)) else (latest_stmt is not None),
            symbol=stock.symbol,
            evaluation_date=eval_d,
            close_price=close_price,
            financial_period_end=latest_stmt.period_end if latest_stmt else None,
            statement_basis=basis,
            pe_ratio=float(snapshot.valuation.pe_ratio) if (snapshot.valuation and snapshot.valuation.pe_ratio is not None) else None,
            pb_ratio=float(snapshot.valuation.pb_ratio) if (snapshot.valuation and snapshot.valuation.pb_ratio is not None) else None,
            ps_ratio=float(snapshot.valuation.ps_ratio) if (snapshot.valuation and snapshot.valuation.ps_ratio is not None) else None,
            book_value_per_share=bvps,
            dividend_yield_pct=float(snapshot.valuation.dividend_yield_pct) if (snapshot.valuation and snapshot.valuation.dividend_yield_pct is not None) else None,
            ev_to_ebitda=None,
            peer_group_type=snapshot.relative_valuation.peer_group_type if snapshot.relative_valuation else None,
            peer_count=snapshot.relative_valuation.peer_count if snapshot.relative_valuation else 0,
            industry_pe=float(snapshot.relative_valuation.median_pe) if (snapshot.relative_valuation and snapshot.relative_valuation.median_pe is not None) else None,
            industry_pb=float(snapshot.relative_valuation.median_pb) if (snapshot.relative_valuation and snapshot.relative_valuation.median_pb is not None) else None,
            industry_ps=float(snapshot.relative_valuation.median_ps) if (snapshot.relative_valuation and snapshot.relative_valuation.median_ps is not None) else None,
            pe_premium_discount_pct=float(snapshot.relative_valuation.pe_premium_discount_pct) if (snapshot.relative_valuation and snapshot.relative_valuation.pe_premium_discount_pct is not None) else None,
            pb_premium_discount_pct=float(snapshot.relative_valuation.pb_premium_discount_pct) if (snapshot.relative_valuation and snapshot.relative_valuation.pb_premium_discount_pct is not None) else None,
            ps_premium_discount_pct=float(snapshot.relative_valuation.ps_premium_discount_pct) if (snapshot.relative_valuation and snapshot.relative_valuation.ps_premium_discount_pct is not None) else None,
            valuation_classification=snapshot.relative_valuation.valuation_classification if snapshot.relative_valuation else None,
            industry_pe_note=None,
            historical_band_note="Historical valuation band unavailable",
        )

        return fund_resp, val_resp


    @staticmethod
    def _build_technical_snapshot(
        indicator: Any | None, analytics: Any | None
    ) -> TechnicalSnapshotResponse:
        """Build a compact technical snapshot from indicator + analytics data."""
        if not indicator and not analytics:
            return TechnicalSnapshotResponse()

        rsi = None
        rsi_label = None
        supertrend_dir = None
        adx_val = None
        adx_label = None

        if indicator:
            if indicator.rsi_14 is not None:
                rsi = float(indicator.rsi_14)
                if rsi < 30:
                    rsi_label = "Oversold"
                elif rsi > 70:
                    rsi_label = "Overbought"
                else:
                    rsi_label = "Neutral"

            if indicator.supertrend_direction is not None:
                supertrend_dir = "Bullish" if indicator.supertrend_direction == 1 else "Bearish"

            if indicator.adx is not None:
                adx_val = float(indicator.adx)
                if adx_val >= 25:
                    adx_label = "Strong trend"
                elif adx_val >= 15:
                    adx_label = "Weak trend"
                else:
                    adx_label = "No trend"

        trend_state = None
        overall_score = None
        if analytics:
            trend_state = getattr(analytics, "trend_rating", None)
            if analytics.overall_score is not None:
                overall_score = float(analytics.overall_score)

        return TechnicalSnapshotResponse(
            rsi_14=rsi,
            rsi_label=rsi_label,
            trend_state=trend_state,
            overall_score=overall_score,
            supertrend_direction=supertrend_dir,
            adx=adx_val,
            adx_label=adx_label,
        )
