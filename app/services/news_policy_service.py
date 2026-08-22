"""Service layer for News and Government Policy Intelligence operations and analytics."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fundamental_corporate import CorporateAnnouncement
from app.models.news_policy import NewsEvent
from app.models.sector import Sector
from app.models.security_alias import SecurityAlias
from app.models.stock import Stock
from app.repositories.news_policy import NewsPolicyRepository
from app.schemas.news_policy import (
    CompanyNewsPolicySnapshot,
    MarketNewsPolicySnapshot,
    NewsCoverageRead,
    NewsEventRead,
    SectorNewsPolicySnapshot,
    UpcomingEventRiskRead,
)


class NewsPolicyService:
    """Service layer orchestrating news and policy intelligence, PIT snapshots, and risk calculation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = NewsPolicyRepository(session)

    async def import_news_events(self, events: Sequence[NewsEvent]) -> int:
        """Import and persist news & policy events with entity resolution."""
        # Resolve stocks by symbol or alias if stock_id is missing but headline/text contains symbol
        resolved_events: list[NewsEvent] = []
        for evt in events:
            if evt.stock_id is None and evt.headline:
                resolved_id = await self.resolve_stock_from_text(evt.headline)
                if resolved_id:
                    evt.stock_id = resolved_id
            resolved_events.append(evt)

        return await self.repo.bulk_upsert_news_events(resolved_events)

    async def resolve_stock_from_text(self, text: str) -> int | None:
        """Resolve a stock ID from text using Stock symbol or SecurityAlias."""
        tokens = [t.strip().upper() for t in text.split() if t.strip()]
        if not tokens:
            return None

        # 1. Direct symbol search
        stmt = select(Stock.id).where(Stock.symbol.in_(tokens)).limit(1)
        res = await self.session.execute(stmt)
        sid = res.scalar_one_or_none()
        if sid:
            return sid

        # 2. SecurityAlias search
        stmt_alias = select(SecurityAlias.stock_id).where(SecurityAlias.symbol.in_(tokens)).limit(1)
        res_alias = await self.session.execute(stmt_alias)
        return res_alias.scalar_one_or_none()

    async def get_company_news_policy_snapshot(
        self,
        symbol: str,
        evaluation_datetime: datetime | None = None,
        as_of_date: date | None = None,
    ) -> CompanyNewsPolicySnapshot:
        """Build PIT news & policy snapshot for a stock."""
        eval_dt = evaluation_datetime or datetime.now(UTC)
        if eval_dt.tzinfo is None:
            eval_dt = eval_dt.replace(tzinfo=UTC)
        as_of = as_of_date or eval_dt.date()

        # Resolve stock
        stmt = select(Stock).where(Stock.symbol == symbol.upper())
        res = await self.session.execute(stmt)
        stock = res.scalar_one_or_none()
        if not stock:
            raise ValueError(f"Stock with symbol '{symbol}' not found")

        # Fetch PIT news (available_at <= eval_dt)
        raw_events = await self.repo.get_company_news(stock.id, as_of_datetime=eval_dt, limit=50)
        events_read = [NewsEventRead.model_validate(e) for e in raw_events]

        pos_cnt = sum(1 for e in events_read if e.deterministic_context == "POSITIVE_CONTEXT")
        neg_cnt = sum(1 for e in events_read if e.deterministic_context == "NEGATIVE_CONTEXT")
        mix_cnt = sum(1 for e in events_read if e.deterministic_context == "MIXED")
        neu_cnt = sum(1 for e in events_read if e.deterministic_context in ("NEUTRAL", "UNKNOWN"))

        # Find latest results event
        latest_results = next((e for e in events_read if e.category in ("RESULTS", "FINANCIAL_RESULTS")), None)

        # Risk flags calculation
        overnight_risk = any(
            e.event_risk_flag in ("OVERNIGHT_EVENT_RISK", "RESULTS_PENDING")
            or e.category in ("RESULTS", "FINANCIAL_RESULTS")
            for e in events_read
        )
        policy_headwind = any(e.event_type == "GOVERNMENT_POLICY" and e.deterministic_context == "NEGATIVE_CONTEXT" for e in events_read)
        policy_tailwind = any(e.event_type == "GOVERNMENT_POLICY" and e.deterministic_context == "POSITIVE_CONTEXT" for e in events_read)
        regulatory_flag = any(e.event_type == "SEBI_REGULATORY" or e.category == "REGULATORY" for e in events_read)

        # Also pull from existing CorporateAnnouncements to augment upcoming risk
        upcoming_risks = await self._get_upcoming_risks_for_stock(stock.id, as_of)
        if any(r.overnight_risk for r in upcoming_risks):
            overnight_risk = True

        quality_warnings: list[str] = []
        if not events_read:
            quality_warnings.append("NO_RECENT_NEWS_EVENTS")
        if stock.sector_id is None:
            quality_warnings.append("SECTOR_UNASSIGNED")

        return CompanyNewsPolicySnapshot(
            symbol=stock.symbol,
            stock_id=stock.id,
            evaluation_datetime=eval_dt,
            as_of_date=as_of,
            recent_events=events_read,
            positive_context_count=pos_cnt,
            negative_context_count=neg_cnt,
            mixed_context_count=mix_cnt,
            neutral_context_count=neu_cnt,
            upcoming_event_risks=upcoming_risks,
            latest_results_event=latest_results,
            overnight_event_risk=overnight_risk,
            policy_headwind=policy_headwind,
            policy_tailwind=policy_tailwind,
            regulatory_flag=regulatory_flag,
            quality_warnings=quality_warnings,
            provenance={
                "events_retrieved": len(events_read),
                "pit_eval_cutoff": eval_dt.isoformat(),
            },
        )

    async def get_sector_news_policy_snapshot(
        self,
        sector_identifier: str | int,
        evaluation_datetime: datetime | None = None,
        as_of_date: date | None = None,
    ) -> SectorNewsPolicySnapshot:
        """Build PIT news & policy snapshot for a sector."""
        eval_dt = evaluation_datetime or datetime.now(UTC)
        if eval_dt.tzinfo is None:
            eval_dt = eval_dt.replace(tzinfo=UTC)
        as_of = as_of_date or eval_dt.date()

        # Resolve sector
        if isinstance(sector_identifier, int):
            stmt = select(Sector).where(Sector.id == sector_identifier)
        else:
            stmt = select(Sector).where(Sector.name.ilike(str(sector_identifier)))
        res = await self.session.execute(stmt)
        sector = res.scalar_one_or_none()
        if not sector:
            raise ValueError(f"Sector '{sector_identifier}' not found")

        raw_events = await self.repo.get_sector_news(sector.id, as_of_datetime=eval_dt, limit=50)
        events_read = [NewsEventRead.model_validate(e) for e in raw_events]

        policy_cnt = sum(1 for e in events_read if e.event_type == "GOVERNMENT_POLICY")
        reg_cnt = sum(1 for e in events_read if e.event_type == "SEBI_REGULATORY")
        pos_cnt = sum(1 for e in events_read if e.deterministic_context == "POSITIVE_CONTEXT")
        neg_cnt = sum(1 for e in events_read if e.deterministic_context == "NEGATIVE_CONTEXT")

        return SectorNewsPolicySnapshot(
            sector_name=sector.name,
            sector_id=sector.id,
            evaluation_datetime=eval_dt,
            as_of_date=as_of,
            recent_events=events_read,
            policy_changes_count=policy_cnt,
            regulatory_changes_count=reg_cnt,
            positive_events_count=pos_cnt,
            negative_events_count=neg_cnt,
            impacted_stocks_count=len({e.stock_id for e in events_read if e.stock_id is not None}),
            quality_warnings=[] if events_read else ["NO_SECTOR_NEWS_EVENTS"],
        )

    async def get_market_news_policy_snapshot(
        self,
        evaluation_datetime: datetime | None = None,
        as_of_date: date | None = None,
    ) -> MarketNewsPolicySnapshot:
        """Build PIT news & policy snapshot for the entire market."""
        eval_dt = evaluation_datetime or datetime.now(UTC)
        if eval_dt.tzinfo is None:
            eval_dt = eval_dt.replace(tzinfo=UTC)
        as_of = as_of_date or eval_dt.date()

        raw_events = await self.repo.get_market_policy_events(as_of_datetime=eval_dt, limit=50)
        events_read = [NewsEventRead.model_validate(e) for e in raw_events]

        rbi_events = [e for e in events_read if e.event_type == "RBI_MONETARY"]
        sebi_events = [e for e in events_read if e.event_type == "SEBI_REGULATORY"]
        gov_events = [e for e in events_read if e.event_type == "GOVERNMENT_POLICY"]

        # Aggregate sentiment context
        pos = sum(1 for e in events_read if e.deterministic_context == "POSITIVE_CONTEXT")
        neg = sum(1 for e in events_read if e.deterministic_context == "NEGATIVE_CONTEXT")
        sentiment = "POSITIVE_CONTEXT" if pos > neg else ("NEGATIVE_CONTEXT" if neg > pos else "NEUTRAL")

        return MarketNewsPolicySnapshot(
            evaluation_datetime=eval_dt,
            as_of_date=as_of,
            recent_market_events=events_read,
            rbi_monetary_events=rbi_events,
            sebi_regulatory_events=sebi_events,
            government_policy_events=gov_events,
            upcoming_market_risks=[],
            overall_sentiment_context=sentiment,
            quality_warnings=[] if events_read else ["NO_MARKET_POLICY_EVENTS"],
        )

    async def _get_upcoming_risks_for_stock(
        self, stock_id: int, as_of: date
    ) -> list[UpcomingEventRiskRead]:
        """Fetch upcoming corporate board meetings or earnings announcements."""
        stmt = (
            select(CorporateAnnouncement)
            .where(CorporateAnnouncement.stock_id == stock_id)
            .where(CorporateAnnouncement.available_at <= as_of)
            .where(CorporateAnnouncement.category.in_(["BOARD_MEETING", "FINANCIAL_RESULTS"]))
            .order_by(CorporateAnnouncement.announcement_date.desc())
            .limit(5)
        )
        res = await self.session.execute(stmt)
        anns = res.scalars().all()

        risks: list[UpcomingEventRiskRead] = []
        for a in anns:
            if a.announcement_date >= as_of:
                risks.append(
                    UpcomingEventRiskRead(
                        event_type="CORPORATE_ANNOUNCEMENT",
                        category=a.category,
                        title=a.title,
                        scheduled_at=a.announcement_date,
                        stock_id=stock_id,
                        event_risk_flag="BOARD_MEETING_PENDING" if a.category == "BOARD_MEETING" else "RESULTS_PENDING",
                        overnight_risk=True,
                    )
                )
        return risks

    async def get_news_coverage(self) -> NewsCoverageRead:
        """Fetch summary coverage statistics."""
        sum_dict = await self.repo.get_coverage_summary()
        return NewsCoverageRead(
            total_events_count=sum_dict["total_events_count"],
            company_events_count=sum_dict["company_events_count"],
            government_policy_events_count=sum_dict["government_policy_events_count"],
            rbi_monetary_events_count=sum_dict["rbi_monetary_events_count"],
            sebi_regulatory_events_count=sum_dict["sebi_regulatory_events_count"],
            macro_events_count=sum_dict["macro_events_count"],
            unique_stocks_covered=sum_dict["unique_stocks_covered"],
            unique_sectors_covered=sum_dict["unique_sectors_covered"],
            coverage_status="OPTIMAL" if sum_dict["total_events_count"] > 0 else "PARTIAL",
        )
