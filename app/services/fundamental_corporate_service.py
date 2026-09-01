"""Service layer for Fundamental and Corporate Intelligence operations and analytics."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.fundamental_parser import (
    CorporateAnnouncementParser,
    FundamentalStatementParser,
    ShareholdingPatternParser,
)
from app.models.daily_price import DailyPrice
from app.models.fundamental_corporate import (
    CorporateAnnouncement,
    FinancialStatement,
    ShareholdingPattern,
)
from app.models.stock import Stock
from app.repositories.fundamental_corporate import FundamentalCorporateRepository
from app.schemas.fundamental_corporate import (
    CompanyFundamentalSnapshot,
    CorporateAnnouncementRead,
    FinancialGrowthMetrics,
    FinancialMarginMetrics,
    FinancialStatementRead,
    FundamentalsCoverageRead,
    PITValuationMetrics,
    QualityScoreInputs,
    RelativeValuationMetrics,
    ShareholdingPatternRead,
    SolvencyAndCashFlowMetrics,
)


class FundamentalCorporateService:
    """Service orchestrating fundamental imports, point-in-time metrics, and quality score inputs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = FundamentalCorporateRepository(session)

    # ---------------------------------------------------------------------------
    # Import Ingestion Orchestration
    # ---------------------------------------------------------------------------

    async def import_financial_statements(self, content: str) -> dict:
        """Parse and import raw CSV financial statement disclosures."""
        parsed_records = FundamentalStatementParser.parse_csv_content(content)
        statements_to_upsert: list[FinancialStatement] = []

        for rec in parsed_records:
            stock = await self.repo.get_stock_by_symbol(rec["symbol"])
            if not stock:
                raise ValueError(f"Unknown stock symbol '{rec['symbol']}'")

            stmt_obj = FinancialStatement(
                stock_id=stock.id,
                period_type=rec["period_type"],
                statement_basis=rec["statement_basis"],
                period_end=rec["period_end"],
                reported_at=rec["reported_at"],
                available_at=rec["available_at"],
                revision_type=rec["revision_type"],
                currency="INR",
                unit="INR_CRORE",
                revenue=rec["revenue"],
                operating_income=rec["operating_income"],
                ebit=rec["ebit"],
                pbt=rec["pbt"],
                pat=rec["pat"],
                eps=rec["eps"],
                total_assets=rec["total_assets"],
                total_liabilities=rec["total_liabilities"],
                total_equity=rec["total_equity"],
                total_debt=rec["total_debt"],
                cash_and_equivalents=rec["cash_and_equivalents"],
                operating_cash_flow=rec["operating_cash_flow"],
                investing_cash_flow=rec["investing_cash_flow"],
                financing_cash_flow=rec["financing_cash_flow"],
                free_cash_flow=rec["free_cash_flow"],
                shares_outstanding=rec["shares_outstanding"],
                source_name=rec["source_name"],
                source_reference=rec["source_reference"],
            )
            stmt_obj.idempotency_hash = stmt_obj.compute_fingerprint()
            statements_to_upsert.append(stmt_obj)

        inserted, updated = await self.repo.upsert_financial_statements(statements_to_upsert)
        await self.session.commit()
        return {"processed": len(parsed_records), "inserted": inserted, "updated": updated}

    async def import_corporate_announcements(self, content: str) -> dict:
        """Parse and import raw CSV corporate announcements."""
        parsed_records = CorporateAnnouncementParser.parse_csv_content(content)
        announcements_to_upsert: list[CorporateAnnouncement] = []

        for rec in parsed_records:
            stock = await self.repo.get_stock_by_symbol(rec["symbol"])
            if not stock:
                raise ValueError(f"Unknown stock symbol '{rec['symbol']}'")

            ann_obj = CorporateAnnouncement(
                stock_id=stock.id,
                category=rec["category"],
                announcement_date=rec["announcement_date"],
                available_at=rec["available_at"],
                title=rec["title"],
                summary=rec["summary"],
                corporate_action_id=int(rec["corporate_action_id"]) if rec["corporate_action_id"] else None,
                source_name=rec["source_name"],
                source_reference=rec["source_reference"],
            )
            ann_obj.idempotency_hash = ann_obj.compute_fingerprint()
            announcements_to_upsert.append(ann_obj)

        inserted, _ = await self.repo.upsert_corporate_announcements(announcements_to_upsert)
        await self.session.commit()
        return {"processed": len(parsed_records), "inserted": inserted}

    async def import_shareholding_patterns(self, content: str) -> dict:
        """Parse and import raw CSV shareholding pattern records."""
        parsed_records = ShareholdingPatternParser.parse_csv_content(content)
        patterns_to_upsert: list[ShareholdingPattern] = []

        for rec in parsed_records:
            stock = await self.repo.get_stock_by_symbol(rec["symbol"])
            if not stock:
                raise ValueError(f"Unknown stock symbol '{rec['symbol']}'")

            p_obj = ShareholdingPattern(
                stock_id=stock.id,
                period_end=rec["period_end"],
                reported_at=rec["reported_at"],
                available_at=rec["available_at"],
                promoter_holding_pct=rec["promoter_holding_pct"],
                fii_holding_pct=rec["fii_holding_pct"],
                dii_holding_pct=rec["dii_holding_pct"],
                public_holding_pct=rec["public_holding_pct"],
                promoter_pledged_pct=rec["promoter_pledged_pct"],
                promoter_holding_change=rec["promoter_holding_change"],
                source_name=rec["source_name"],
                source_reference=rec["source_reference"],
            )
            p_obj.idempotency_hash = p_obj.compute_fingerprint()
            patterns_to_upsert.append(p_obj)

        inserted, _ = await self.repo.upsert_shareholding_patterns(patterns_to_upsert)
        await self.session.commit()
        return {"processed": len(parsed_records), "inserted": inserted}

    # ---------------------------------------------------------------------------
    # Analytics & Company Fundamental Snapshot
    # ---------------------------------------------------------------------------

    async def get_company_fundamental_snapshot(
        self,
        symbol: str,
        evaluation_date: date,
        as_of_date: date | None = None,
        statement_basis: str = "CONSOLIDATED",
    ) -> CompanyFundamentalSnapshot:
        """Reconstruct point-in-time safe company fundamental snapshot as of cutoff."""
        as_of = as_of_date or evaluation_date
        stock = await self.repo.get_stock_by_symbol(symbol)
        if not stock:
            raise ValueError(f"Unknown stock symbol '{symbol}'")

        quality_warnings: list[str] = []

        # 1. Fetch PIT quarterly history
        q_history = await self.repo.get_financial_history(
            stock_id=stock.id,
            period_type="QUARTERLY",
            statement_basis=statement_basis,
            as_of_date=as_of,
            limit=8,
        )

        # Fallback to STANDALONE if CONSOLIDATED is missing
        if not q_history and statement_basis == "CONSOLIDATED":
            q_history = await self.repo.get_financial_history(
                stock_id=stock.id,
                period_type="QUARTERLY",
                statement_basis="STANDALONE",
                as_of_date=as_of,
                limit=8,
            )
            if q_history:
                statement_basis = "STANDALONE"
                quality_warnings.append("CONSOLIDATED_UNAVAILABLE_FALLBACK_TO_STANDALONE")

        # 2. Fetch PIT annual history
        a_history = await self.repo.get_financial_history(
            stock_id=stock.id,
            period_type="ANNUAL",
            statement_basis=statement_basis,
            as_of_date=as_of,
            limit=4,
        )

        latest_q = q_history[0] if q_history else None
        latest_a = a_history[0] if a_history else None

        if not latest_q and not latest_a:
            quality_warnings.append("NO_POINT_IN_TIME_FINANCIAL_STATEMENTS")

        # 3. Compute Growth Metrics
        growth = self._calculate_growth_metrics(q_history)

        # 4. Compute Margin & Profitability Metrics
        margins = self._calculate_margin_metrics(latest_q or latest_a)

        # 5. Compute Solvency & Cash Flow Quality Metrics
        solvency = self._calculate_solvency_metrics(latest_q or latest_a)

        # 6. Compute PIT Valuation Metrics
        valuation = await self._calculate_pit_valuation(
            stock_id=stock.id,
            evaluation_date=evaluation_date,
            as_of=as_of,
            latest_stmt=latest_q or latest_a,
        )

        # 6.5 Compute Relative Valuation Metrics
        relative_val = await self._calculate_relative_valuation(
            stock=stock,
            evaluation_date=evaluation_date,
            as_of=as_of,
            statement_basis=statement_basis,
            stock_valuation=valuation,
        )

        # 7. Compute Quality Score Inputs
        quality_inputs = self._calculate_quality_score_inputs(
            growth=growth,
            margins=margins,
            solvency=solvency,
            quality_warnings=quality_warnings,
        )

        # 8. Fetch Shareholding, Corporate Actions, and Announcements
        sh_history = await self.repo.get_shareholding_history(stock.id, as_of_date=as_of, limit=1)
        latest_sh = sh_history[0] if sh_history else None

        c_actions = await self.repo.get_recent_corporate_actions(stock.id, as_of_date=as_of, limit=5)
        c_anns = await self.repo.get_corporate_announcements(stock.id, as_of_date=as_of, limit=5)

        return CompanyFundamentalSnapshot(
            symbol=stock.symbol,
            stock_id=stock.id,
            evaluation_date=evaluation_date,
            as_of_date=as_of,
            statement_basis=statement_basis,
            latest_quarterly=FinancialStatementRead.model_validate(latest_q) if latest_q else None,
            latest_annual=FinancialStatementRead.model_validate(latest_a) if latest_a else None,
            growth=growth,
            margins=margins,
            solvency_cashflow=solvency,
            valuation=valuation,
            relative_valuation=relative_val,
            quality_inputs=quality_inputs,
            latest_shareholding=ShareholdingPatternRead.model_validate(latest_sh) if latest_sh else None,
            recent_corporate_actions=[
                {
                    "action_type": ca.action_type,
                    "ex_date": ca.ex_date.isoformat() if ca.ex_date else None,
                    "purpose": ca.notes or ca.action_type,
                    "ratio_numerator": ca.ratio_numerator,
                    "ratio_denominator": ca.ratio_denominator,
                }
                for ca in c_actions
            ],
            recent_announcements=[CorporateAnnouncementRead.model_validate(a) for a in c_anns],
            quality_warnings=quality_warnings,
            provenance={
                "financial_records_count": len(q_history) + len(a_history),
                "shareholding_available": latest_sh is not None,
                "announcements_count": len(c_anns),
            },
        )

    # ---------------------------------------------------------------------------
    # Internal Metric Calculations
    # ---------------------------------------------------------------------------

    def _calculate_growth_metrics(
        self, q_history: list[FinancialStatement]
    ) -> FinancialGrowthMetrics:
        """Calculate YoY and QoQ growth percentages safely."""
        if not q_history or len(q_history) < 2:
            return FinancialGrowthMetrics()

        latest = q_history[0]
        prev_q = q_history[1]

        # Find YoY quarter (4 quarters prior)
        yoy_q = q_history[4] if len(q_history) >= 5 else None

        rev_yoy = self._calc_pct_change(latest.revenue, yoy_q.revenue if yoy_q else None)
        rev_qoq = self._calc_pct_change(latest.revenue, prev_q.revenue)

        pat_yoy = self._calc_pct_change(latest.pat, yoy_q.pat if yoy_q else None)
        pat_qoq = self._calc_pct_change(latest.pat, prev_q.pat)

        eps_yoy = self._calc_pct_change(latest.eps, yoy_q.eps if yoy_q else None)

        return FinancialGrowthMetrics(
            revenue_growth_yoy=rev_yoy,
            revenue_growth_qoq=rev_qoq,
            profit_growth_yoy=pat_yoy,
            profit_growth_qoq=pat_qoq,
            eps_growth_yoy=eps_yoy,
        )

    def _calculate_margin_metrics(
        self, stmt: FinancialStatement | None
    ) -> FinancialMarginMetrics:
        """Calculate operating margin %, net profit margin %, ROE %, and ROCE %."""
        if not stmt:
            return FinancialMarginMetrics()

        op_margin = None
        if stmt.revenue and stmt.revenue > Decimal("0") and stmt.operating_income is not None:
            op_margin = (stmt.operating_income / stmt.revenue) * Decimal("100")

        net_margin = None
        if stmt.revenue and stmt.revenue > Decimal("0") and stmt.pat is not None:
            net_margin = (stmt.pat / stmt.revenue) * Decimal("100")

        roe = None
        if stmt.total_equity and stmt.total_equity > Decimal("0") and stmt.pat is not None:
            roe = (stmt.pat / stmt.total_equity) * Decimal("100")

        roce = None
        capital_employed = (stmt.total_equity or Decimal("0")) + (stmt.total_debt or Decimal("0"))
        if capital_employed > Decimal("0") and stmt.ebit is not None:
            roce = (stmt.ebit / capital_employed) * Decimal("100")

        return FinancialMarginMetrics(
            operating_margin_pct=op_margin,
            net_profit_margin_pct=net_margin,
            roe_pct=roe,
            roce_pct=roce,
        )

    def _calculate_solvency_metrics(
        self, stmt: FinancialStatement | None
    ) -> SolvencyAndCashFlowMetrics:
        """Calculate Debt/Equity, Interest Coverage, and Cash Flow metrics."""
        if not stmt:
            return SolvencyAndCashFlowMetrics()

        de_ratio = None
        if stmt.total_equity and stmt.total_equity > Decimal("0") and stmt.total_debt is not None:
            de_ratio = stmt.total_debt / stmt.total_equity

        ocf_pat = None
        if stmt.pat and stmt.pat > Decimal("0") and stmt.operating_cash_flow is not None:
            ocf_pat = stmt.operating_cash_flow / stmt.pat

        fcf = stmt.free_cash_flow
        if fcf is None and stmt.operating_cash_flow is not None and stmt.investing_cash_flow is not None:
            # Derived FCF = OCF + CapEx (where CapEx is negative investing cash flow)
            fcf = stmt.operating_cash_flow + stmt.investing_cash_flow

        return SolvencyAndCashFlowMetrics(
            debt_to_equity=de_ratio,
            interest_coverage=None,
            operating_cash_flow_to_pat=ocf_pat,
            free_cash_flow=fcf,
        )

    async def _calculate_pit_valuation(
        self,
        stock_id: int,
        evaluation_date: date,
        as_of: date,
        latest_stmt: FinancialStatement | None,
    ) -> PITValuationMetrics:
        """Calculate point-in-time valuation ratios using PIT price and PIT financials."""
        stmt = (
            select(DailyPrice.close)
            .where(DailyPrice.stock_id == stock_id)
            .where(DailyPrice.trade_date <= evaluation_date)
            .order_by(DailyPrice.trade_date.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        close_price = res.scalar_one_or_none()

        if not close_price or not latest_stmt:
            return PITValuationMetrics(evaluation_date=evaluation_date, close_price=close_price)

        pe = None
        if latest_stmt.eps and latest_stmt.eps > Decimal("0"):
            pe = close_price / latest_stmt.eps

        pb = None
        if latest_stmt.shares_outstanding and latest_stmt.shares_outstanding > 0 and latest_stmt.total_equity:
            # Book value per share = Total Equity (Crore * 10^7) / Shares Outstanding
            bvps = (latest_stmt.total_equity * Decimal("10000000")) / latest_stmt.shares_outstanding
            if bvps > Decimal("0"):
                pb = close_price / bvps

        ps = None
        if latest_stmt.shares_outstanding and latest_stmt.shares_outstanding > 0 and latest_stmt.revenue:
            rev_per_share = (latest_stmt.revenue * Decimal("10000000")) / latest_stmt.shares_outstanding
            if rev_per_share > Decimal("0"):
                ps = close_price / rev_per_share

        return PITValuationMetrics(
            evaluation_date=evaluation_date,
            close_price=close_price,
            pe_ratio=pe,
            pb_ratio=pb,
            ps_ratio=ps,
            dividend_yield_pct=None,
        )

    async def _calculate_relative_valuation(
        self,
        stock: Stock,
        evaluation_date: date,
        as_of: date,
        statement_basis: str,
        stock_valuation: PITValuationMetrics,
    ) -> RelativeValuationMetrics | None:
        """Calculate peer group valuation median and stock premium/discount."""
        from app.models.comparable_company import ComparableCompany

        # Determine peers based on hierarchy
        stmt = select(ComparableCompany.peer_stock_id).where(ComparableCompany.stock_id == stock.id).where(ComparableCompany.is_active.is_(True))
        peer_ids = list((await self.session.execute(stmt)).scalars().all())
        peer_group_type = "COMPARABLE_COMPANY"

        if not peer_ids and stock.industry_id:
            from app.models.stock import Stock
            stmt = select(Stock.id).where(Stock.industry_id == stock.industry_id).where(Stock.is_active.is_(True)).where(Stock.id != stock.id)
            peer_ids = list((await self.session.execute(stmt)).scalars().all())
            peer_group_type = "INDUSTRY"

        if not peer_ids and stock.industry:
            from app.models.stock import Stock
            stmt = select(Stock.id).where(func.upper(Stock.industry) == stock.industry.upper()).where(Stock.is_active.is_(True)).where(Stock.id != stock.id)
            peer_ids = list((await self.session.execute(stmt)).scalars().all())
            if peer_ids:
                peer_group_type = "INDUSTRY"

        if not peer_ids and stock.sector_id:
            from app.models.stock import Stock
            stmt = select(Stock.id).where(Stock.sector_id == stock.sector_id).where(Stock.is_active.is_(True)).where(Stock.id != stock.id)
            peer_ids = list((await self.session.execute(stmt)).scalars().all())
            peer_group_type = "SECTOR"

        if not peer_ids:
            return None

        # Bulk fetch financials and prices
        latest_financials = await self.repo.get_bulk_latest_financials(peer_ids, as_of, statement_basis)
        latest_prices = await self.repo.get_bulk_latest_prices(peer_ids, evaluation_date)

        pe_list, pb_list, ps_list = [], [], []
        for fin in latest_financials:
            price = latest_prices.get(fin.stock_id)
            if not price:
                continue

            if fin.eps and fin.eps > 0:
                pe_list.append(price / fin.eps)

            if fin.shares_outstanding and fin.shares_outstanding > 0 and fin.total_equity:
                bvps = (fin.total_equity * Decimal("10000000")) / fin.shares_outstanding
                if bvps > 0:
                    pb_list.append(price / bvps)

            if fin.shares_outstanding and fin.shares_outstanding > 0 and fin.revenue:
                rev_per_share = (fin.revenue * Decimal("10000000")) / fin.shares_outstanding
                if rev_per_share > 0:
                    ps_list.append(price / rev_per_share)

        import statistics
        median_pe = Decimal(statistics.median(pe_list)) if pe_list else None
        median_pb = Decimal(statistics.median(pb_list)) if pb_list else None
        median_ps = Decimal(statistics.median(ps_list)) if ps_list else None

        pe_premium = None
        if stock_valuation.pe_ratio and median_pe:
            pe_premium = ((stock_valuation.pe_ratio - median_pe) / median_pe) * Decimal("100")

        pb_premium = None
        if stock_valuation.pb_ratio and median_pb:
            pb_premium = ((stock_valuation.pb_ratio - median_pb) / median_pb) * Decimal("100")

        ps_premium = None
        if stock_valuation.ps_ratio and median_ps:
            ps_premium = ((stock_valuation.ps_ratio - median_ps) / median_ps) * Decimal("100")

        # Valuation Classification
        cls = "FAIRLY_VALUED"
        if pe_premium is not None:
            if pe_premium > Decimal("20"):
                cls = "EXPENSIVE"
            elif pe_premium < Decimal("-20"):
                cls = "ATTRACTIVE"

        return RelativeValuationMetrics(
            peer_group_type=peer_group_type,
            peer_count=len(peer_ids),
            median_pe=median_pe,
            median_pb=median_pb,
            median_ps=median_ps,
            pe_premium_discount_pct=pe_premium,
            pb_premium_discount_pct=pb_premium,
            ps_premium_discount_pct=ps_premium,
            valuation_classification=cls,
        )

    def _calculate_quality_score_inputs(
        self,
        growth: FinancialGrowthMetrics,
        margins: FinancialMarginMetrics,
        solvency: SolvencyAndCashFlowMetrics,
        quality_warnings: list[str],
    ) -> QualityScoreInputs:
        """Construct deterministic quality score component inputs."""
        fund_q = Decimal("50.0")
        if margins.roe_pct and margins.roe_pct > Decimal("15.0"):
            fund_q += Decimal("20.0")
        if margins.net_profit_margin_pct and margins.net_profit_margin_pct > Decimal("10.0"):
            fund_q += Decimal("15.0")

        growth_q = Decimal("50.0")
        if growth.revenue_growth_yoy and growth.revenue_growth_yoy > Decimal("10.0"):
            growth_q += Decimal("25.0")
        if growth.profit_growth_yoy and growth.profit_growth_yoy > Decimal("15.0"):
            growth_q += Decimal("25.0")

        cf_q = Decimal("50.0")
        if solvency.operating_cash_flow_to_pat and solvency.operating_cash_flow_to_pat >= Decimal("1.0"):
            cf_q += Decimal("30.0")

        bs_q = Decimal("50.0")
        if solvency.debt_to_equity is not None:
            if solvency.debt_to_equity <= Decimal("0.5"):
                bs_q += Decimal("30.0")
            elif solvency.debt_to_equity > Decimal("1.5"):
                bs_q -= Decimal("20.0")

        return QualityScoreInputs(
            fundamental_quality_score=min(fund_q, Decimal("100.0")),
            growth_quality_score=min(growth_q, Decimal("100.0")),
            cashflow_quality_score=min(cf_q, Decimal("100.0")),
            balance_sheet_quality_score=min(bs_q, Decimal("100.0")),
            corporate_risk_flags=list(quality_warnings),
        )

    @staticmethod
    def _calc_pct_change(curr: Decimal | None, prev: Decimal | None) -> Decimal | None:
        """Compute percentage change safely."""
        if curr is None or prev is None or prev == Decimal("0"):
            return None
        return ((curr - prev) / abs(prev)) * Decimal("100")

    async def get_corporate_announcements(
        self,
        symbol: str,
        as_of_date: date | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Return PIT-safe corporate announcements for a stock."""
        stock = await self.repo.get_stock_by_symbol(symbol)
        if not stock:
            raise ValueError(f"Stock {symbol} not found")

        announcements = await self.repo.get_corporate_announcements(
            stock.id, as_of_date=as_of_date, limit=limit
        )
        return [
            {
                "id": a.id,
                "title": a.title,
                "event_type": a.category,
                "category": a.category,
                "announcement_date": a.announcement_date.isoformat() if a.announcement_date else None,
                "available_at": a.available_at.isoformat() if a.available_at else None,
                "source": a.source_name,
                "source_name": a.source_name,
                "url": a.source_reference,
                "summary": a.summary,
            }
            for a in announcements
        ]

    async def get_fundamentals_coverage(self, as_of_date: date | None = None) -> FundamentalsCoverageRead:
        """Return platform fundamental coverage metadata."""
        summary = await self.repo.get_coverage_summary(as_of_date)
        return FundamentalsCoverageRead(**summary)
