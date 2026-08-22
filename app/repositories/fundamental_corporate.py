"""Repository for Fundamental and Corporate Intelligence queries and persistence."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.corporate_action import CorporateAction
from app.models.fundamental_corporate import (
    CorporateAnnouncement,
    FinancialStatement,
    ShareholdingPattern,
)
from app.models.stock import Stock


class FundamentalCorporateRepository:
    """Async repository for point-in-time safe fundamental and corporate intelligence queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_stock_by_symbol(self, symbol: str) -> Stock | None:
        """Resolve Stock master record by symbol (case-insensitive)."""
        stmt = select(Stock).where(func.upper(Stock.symbol) == symbol.strip().upper())
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    # ---------------------------------------------------------------------------
    # Bulk Upserts
    # ---------------------------------------------------------------------------

    async def upsert_financial_statements(
        self, statements: Sequence[FinancialStatement]
    ) -> tuple[int, int]:
        """Bulk upsert financial statement facts idempotently."""
        if not statements:
            return 0, 0

        inserted_count = 0
        updated_count = 0

        for stmt_obj in statements:
            stmt = insert(FinancialStatement).values(
                stock_id=stmt_obj.stock_id,
                period_type=stmt_obj.period_type,
                statement_basis=stmt_obj.statement_basis,
                period_end=stmt_obj.period_end,
                reported_at=stmt_obj.reported_at,
                available_at=stmt_obj.available_at,
                revision_type=stmt_obj.revision_type,
                currency=stmt_obj.currency,
                unit=stmt_obj.unit,
                revenue=stmt_obj.revenue,
                operating_income=stmt_obj.operating_income,
                ebit=stmt_obj.ebit,
                pbt=stmt_obj.pbt,
                pat=stmt_obj.pat,
                eps=stmt_obj.eps,
                total_assets=stmt_obj.total_assets,
                total_liabilities=stmt_obj.total_liabilities,
                total_equity=stmt_obj.total_equity,
                total_debt=stmt_obj.total_debt,
                cash_and_equivalents=stmt_obj.cash_and_equivalents,
                operating_cash_flow=stmt_obj.operating_cash_flow,
                investing_cash_flow=stmt_obj.investing_cash_flow,
                financing_cash_flow=stmt_obj.financing_cash_flow,
                free_cash_flow=stmt_obj.free_cash_flow,
                shares_outstanding=stmt_obj.shares_outstanding,
                source_name=stmt_obj.source_name,
                source_reference=stmt_obj.source_reference,
                idempotency_hash=stmt_obj.idempotency_hash or stmt_obj.compute_fingerprint(),
            )

            stmt = stmt.on_conflict_do_update(
                constraint="uq_financial_statements_identity",
                set_={
                    "revenue": stmt.excluded.revenue,
                    "operating_income": stmt.excluded.operating_income,
                    "ebit": stmt.excluded.ebit,
                    "pbt": stmt.excluded.pbt,
                    "pat": stmt.excluded.pat,
                    "eps": stmt.excluded.eps,
                    "total_assets": stmt.excluded.total_assets,
                    "total_liabilities": stmt.excluded.total_liabilities,
                    "total_equity": stmt.excluded.total_equity,
                    "total_debt": stmt.excluded.total_debt,
                    "cash_and_equivalents": stmt.excluded.cash_and_equivalents,
                    "operating_cash_flow": stmt.excluded.operating_cash_flow,
                    "investing_cash_flow": stmt.excluded.investing_cash_flow,
                    "financing_cash_flow": stmt.excluded.financing_cash_flow,
                    "free_cash_flow": stmt.excluded.free_cash_flow,
                    "shares_outstanding": stmt.excluded.shares_outstanding,
                    "available_at": stmt.excluded.available_at,
                    "source_reference": stmt.excluded.source_reference,
                },
            )

            res = await self.session.execute(stmt)
            if res.rowcount > 0:
                inserted_count += 1

        return inserted_count, updated_count

    async def upsert_corporate_announcements(
        self, announcements: Sequence[CorporateAnnouncement]
    ) -> tuple[int, int]:
        """Bulk upsert corporate announcements idempotently."""
        if not announcements:
            return 0, 0

        inserted_count = 0
        for ann in announcements:
            stmt = insert(CorporateAnnouncement).values(
                stock_id=ann.stock_id,
                category=ann.category,
                announcement_date=ann.announcement_date,
                available_at=ann.available_at,
                title=ann.title,
                summary=ann.summary,
                corporate_action_id=ann.corporate_action_id,
                source_name=ann.source_name,
                source_reference=ann.source_reference,
                idempotency_hash=ann.idempotency_hash or ann.compute_fingerprint(),
            )
            stmt = stmt.on_conflict_do_update(
                constraint="corporate_announcements_idempotency_hash_key",
                set_={
                    "summary": stmt.excluded.summary,
                    "available_at": stmt.excluded.available_at,
                },
            )
            res = await self.session.execute(stmt)
            if res.rowcount > 0:
                inserted_count += 1

        return inserted_count, 0

    async def upsert_shareholding_patterns(
        self, patterns: Sequence[ShareholdingPattern]
    ) -> tuple[int, int]:
        """Bulk upsert shareholding patterns idempotently."""
        if not patterns:
            return 0, 0

        inserted_count = 0
        for p in patterns:
            stmt = insert(ShareholdingPattern).values(
                stock_id=p.stock_id,
                period_end=p.period_end,
                reported_at=p.reported_at,
                available_at=p.available_at,
                promoter_holding_pct=p.promoter_holding_pct,
                fii_holding_pct=p.fii_holding_pct,
                dii_holding_pct=p.dii_holding_pct,
                public_holding_pct=p.public_holding_pct,
                promoter_pledged_pct=p.promoter_pledged_pct,
                promoter_holding_change=p.promoter_holding_change,
                source_name=p.source_name,
                source_reference=p.source_reference,
                idempotency_hash=p.idempotency_hash or p.compute_fingerprint(),
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_shareholding_patterns_identity",
                set_={
                    "promoter_holding_pct": stmt.excluded.promoter_holding_pct,
                    "fii_holding_pct": stmt.excluded.fii_holding_pct,
                    "dii_holding_pct": stmt.excluded.dii_holding_pct,
                    "public_holding_pct": stmt.excluded.public_holding_pct,
                    "promoter_pledged_pct": stmt.excluded.promoter_pledged_pct,
                    "promoter_holding_change": stmt.excluded.promoter_holding_change,
                    "available_at": stmt.excluded.available_at,
                },
            )
            res = await self.session.execute(stmt)
            if res.rowcount > 0:
                inserted_count += 1

        return inserted_count, 0

    # ---------------------------------------------------------------------------
    # Point-in-Time Safe Queries
    # ---------------------------------------------------------------------------

    async def get_financial_history(
        self,
        stock_id: int,
        period_type: str | None = None,
        statement_basis: str = "CONSOLIDATED",
        as_of_date: date | None = None,
        limit: int = 20,
    ) -> list[FinancialStatement]:
        """Fetch historical financial statement facts with PIT safety (available_at <= as_of_date)."""
        stmt = (
            select(FinancialStatement)
            .where(FinancialStatement.stock_id == stock_id)
            .where(FinancialStatement.statement_basis == statement_basis)
        )
        if period_type:
            stmt = stmt.where(FinancialStatement.period_type == period_type)
        if as_of_date:
            stmt = stmt.where(FinancialStatement.available_at <= as_of_date)

        stmt = stmt.order_by(FinancialStatement.period_end.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_latest_financial_statement(
        self,
        stock_id: int,
        period_type: str,
        statement_basis: str = "CONSOLIDATED",
        as_of_date: date | None = None,
    ) -> FinancialStatement | None:
        """Fetch latest PIT financial statement for period_type."""
        history = await self.get_financial_history(
            stock_id=stock_id,
            period_type=period_type,
            statement_basis=statement_basis,
            as_of_date=as_of_date,
            limit=1,
        )
        return history[0] if history else None

    async def get_corporate_announcements(
        self,
        stock_id: int,
        as_of_date: date | None = None,
        limit: int = 20,
    ) -> list[CorporateAnnouncement]:
        """Fetch PIT corporate announcements for a stock."""
        stmt = select(CorporateAnnouncement).where(CorporateAnnouncement.stock_id == stock_id)
        if as_of_date:
            stmt = stmt.where(CorporateAnnouncement.available_at <= as_of_date)
        stmt = stmt.order_by(CorporateAnnouncement.announcement_date.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_recent_corporate_actions(
        self,
        stock_id: int,
        as_of_date: date | None = None,
        limit: int = 10,
    ) -> list[CorporateAction]:
        """Fetch PIT corporate actions (reusing existing 19.1C CorporateAction model)."""
        stmt = select(CorporateAction).where(CorporateAction.stock_id == stock_id)
        if as_of_date:
            stmt = stmt.where(CorporateAction.available_at <= as_of_date)
        stmt = stmt.order_by(CorporateAction.ex_date.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_shareholding_history(
        self,
        stock_id: int,
        as_of_date: date | None = None,
        limit: int = 12,
    ) -> list[ShareholdingPattern]:
        """Fetch PIT shareholding pattern history."""
        stmt = select(ShareholdingPattern).where(ShareholdingPattern.stock_id == stock_id)
        if as_of_date:
            stmt = stmt.where(ShareholdingPattern.available_at <= as_of_date)
        stmt = stmt.order_by(ShareholdingPattern.period_end.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_coverage_summary(self, as_of_date: date | None = None) -> dict:
        """Compute platform fundamental & corporate coverage statistics."""
        as_of = as_of_date or date.today()

        # Stock count
        st_count_res = await self.session.execute(select(func.count(Stock.id)).where(Stock.is_active.is_(True)))
        total_stocks = st_count_res.scalar_one() or 0

        # Statements stats
        st_stmt = select(
            func.count(FinancialStatement.id),
            func.count(func.distinct(FinancialStatement.stock_id)),
            func.min(FinancialStatement.period_end),
            func.max(FinancialStatement.period_end),
        ).where(FinancialStatement.available_at <= as_of)
        st_res = await self.session.execute(st_stmt)
        total_stmts, stocks_with_stmts, first_period, latest_period = st_res.one()

        # Announcements stats
        ann_stmt = select(
            func.count(CorporateAnnouncement.id),
            func.count(func.distinct(CorporateAnnouncement.stock_id)),
        ).where(CorporateAnnouncement.available_at <= as_of)
        ann_res = await self.session.execute(ann_stmt)
        total_anns, stocks_with_anns = ann_res.one()

        # Shareholding stats
        sh_stmt = select(
            func.count(ShareholdingPattern.id),
            func.count(func.distinct(ShareholdingPattern.stock_id)),
        ).where(ShareholdingPattern.available_at <= as_of)
        sh_res = await self.session.execute(sh_stmt)
        total_sh, stocks_with_sh = sh_res.one()

        coverage_status = "PARTIAL" if total_stmts > 0 else "NO_DATA"
        if stocks_with_stmts > 0 and total_stocks > 0 and (stocks_with_stmts / total_stocks) > 0.8:
            coverage_status = "ROBUST"

        return {
            "as_of_date": as_of,
            "total_stocks_count": total_stocks,
            "stocks_with_financials_count": stocks_with_stmts or 0,
            "stocks_with_announcements_count": stocks_with_anns or 0,
            "stocks_with_shareholding_count": stocks_with_sh or 0,
            "first_financial_period": first_period,
            "latest_financial_period": latest_period,
            "total_statements_count": total_stmts or 0,
            "total_announcements_count": total_anns or 0,
            "total_shareholding_records_count": total_sh or 0,
            "quality_issues_count": 0,
            "coverage_status": coverage_status,
        }
