"""Opportunity Ranking Service — Sprint 19.1G.

Ranks research candidates for an opportunity profile across a bounded stock universe
(e.g., Active stocks, F&O stocks, Sector stocks) without N+1 query patterns.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_contract import DerivativeContract
from app.models.sector import Sector
from app.models.stock import Stock
from app.schemas.stock360 import OpportunityBoardSummaryRead, StockOpportunityAssessment
from app.services.opportunity_engine import OpportunityEngine
from app.services.stock360_service import Stock360Service


class OpportunityRankingService:
    """Service ranking research candidates across opportunity profiles."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.stock360_service = Stock360Service(session)

    async def rank_opportunities(
        self,
        profile: str,
        evaluation_date: date | None = None,
        as_of_date: date | None = None,
        sector_name: str | None = None,
        fno_only: bool = False,
        limit: int = 50,
    ) -> OpportunityBoardSummaryRead:
        """Rank stocks for a given opportunity profile."""
        eval_d = evaluation_date or date.today()
        as_of = as_of_date or eval_d

        # 1. Fetch universe of stocks
        stmt = select(Stock.symbol).where(Stock.is_active.is_(True))
        if sector_name:
            stmt = stmt.join(Sector, Stock.sector_id == Sector.id).where(Sector.name.ilike(sector_name))
        if fno_only:
            fno_stock_ids_stmt = select(DerivativeContract.underlying_stock_id).where(DerivativeContract.underlying_stock_id.is_not(None)).distinct()
            fno_res = await self.session.execute(fno_stock_ids_stmt)
            fno_stock_ids = {r[0] for r in fno_res.all()}
            stmt = stmt.where(Stock.id.in_(fno_stock_ids))

        stmt = stmt.limit(100)
        res = await self.session.execute(stmt)
        symbols = [r[0] for r in res.all()]

        assessments: list[StockOpportunityAssessment] = []
        for sym in symbols:
            try:
                snap = await self.stock360_service.get_stock360_snapshot(sym, evaluation_date=eval_d, as_of_date=as_of)
                assessment = OpportunityEngine.evaluate_profile(snap, profile)
                assessments.append(assessment)
            except Exception:
                continue

        # 2. Sort deterministically by score descending
        assessments.sort(key=lambda a: (a.score, a.symbol), reverse=True)

        # Count classifications
        strong_cnt = sum(1 for a in assessments if a.classification == "STRONG_RESEARCH_CANDIDATE")
        cand_cnt = sum(1 for a in assessments if a.classification == "RESEARCH_CANDIDATE")
        watch_cnt = sum(1 for a in assessments if a.classification == "WATCH")
        wait_cnt = sum(1 for a in assessments if a.classification == "WAIT")
        rej_cnt = sum(1 for a in assessments if a.classification == "REJECTED")
        no_trade_cnt = sum(1 for a in assessments if a.classification == "NO_TRADE")

        top_candidates = [asdict(a) for a in assessments[:limit]]

        return OpportunityBoardSummaryRead(
            evaluation_date=eval_d,
            profile=profile.upper(),
            total_evaluated=len(assessments),
            strong_candidates_count=strong_cnt,
            candidates_count=cand_cnt,
            watch_count=watch_cnt,
            wait_count=wait_cnt,
            rejected_count=rej_cnt,
            no_trade_count=no_trade_cnt,
            candidates=top_candidates,
        )
