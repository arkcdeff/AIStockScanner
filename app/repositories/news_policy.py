"""Repository for News and Government Policy Intelligence queries and persistence."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.news_policy import NewsEntityLink, NewsEvent


class NewsPolicyRepository:
    """Async repository for persistence and PIT-safe querying of news & policy intelligence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bulk_upsert_news_events(self, events: Sequence[NewsEvent]) -> int:
        """Bulk upsert NewsEvent records with ON CONFLICT (idempotency_hash) DO NOTHING."""
        if not events:
            return 0

        inserted_count = 0
        for evt in events:
            stmt = (
                insert(NewsEvent)
                .values(
                    headline=evt.headline,
                    summary=evt.summary,
                    source_name=evt.source_name,
                    source_type=evt.source_type,
                    source_reference=evt.source_reference,
                    published_at=evt.published_at,
                    available_at=evt.available_at,
                    effective_at=evt.effective_at,
                    fetched_at=evt.fetched_at or datetime.now(UTC),
                    event_type=evt.event_type or "COMPANY",
                    category=evt.category or "OTHER",
                    scope_type=evt.scope_type or "STOCK",
                    government_body=evt.government_body,
                    regulator=evt.regulator,
                    language=evt.language or "en",
                    deterministic_context=evt.deterministic_context or "UNKNOWN",
                    event_severity=evt.event_severity or "MEDIUM",
                    event_risk_flag=evt.event_risk_flag,
                    confidence_score=evt.confidence_score,
                    reason_code=evt.reason_code,
                    stock_id=evt.stock_id,
                    sector_id=evt.sector_id,
                    market_index_id=evt.market_index_id,
                    corporate_announcement_id=evt.corporate_announcement_id,
                    econ_event_id=evt.econ_event_id,
                    idempotency_hash=evt.idempotency_hash,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_hash"])
            )
            res = await self.session.execute(stmt)
            if res.rowcount and res.rowcount > 0:
                inserted_count += res.rowcount

        await self.session.flush()
        return inserted_count

    async def add_entity_link(self, link: NewsEntityLink) -> NewsEntityLink:
        """Add a normalized news entity impact link."""
        self.session.add(link)
        await self.session.flush()
        return link

    async def get_company_news(
        self,
        stock_id: int,
        as_of_datetime: datetime,
        limit: int = 50,
        days_window: int | None = None,
    ) -> list[NewsEvent]:
        """Fetch PIT-safe news events for a specific stock (available_at <= as_of_datetime)."""
        stmt = (
            select(NewsEvent)
            .where(NewsEvent.stock_id == stock_id)
            .where(NewsEvent.available_at <= as_of_datetime)
        )
        if days_window is not None and days_window > 0:
            cutoff = as_of_datetime.timestamp() - (days_window * 86400)
            cutoff_dt = datetime.fromtimestamp(cutoff, tz=UTC)
            stmt = stmt.where(NewsEvent.published_at >= cutoff_dt)

        stmt = stmt.order_by(NewsEvent.published_at.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_sector_news(
        self,
        sector_id: int,
        as_of_datetime: datetime,
        limit: int = 50,
    ) -> list[NewsEvent]:
        """Fetch PIT-safe news/policy events for a sector (available_at <= as_of_datetime)."""
        stmt = (
            select(NewsEvent)
            .where(NewsEvent.sector_id == sector_id)
            .where(NewsEvent.available_at <= as_of_datetime)
            .order_by(NewsEvent.published_at.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_market_policy_events(
        self,
        as_of_datetime: datetime,
        limit: int = 50,
        event_types: list[str] | None = None,
    ) -> list[NewsEvent]:
        """Fetch PIT-safe government policy, RBI, and SEBI regulatory events."""
        stmt = select(NewsEvent).where(NewsEvent.available_at <= as_of_datetime)
        if event_types:
            stmt = stmt.where(NewsEvent.event_type.in_(event_types))
        else:
            stmt = stmt.where(
                NewsEvent.event_type.in_(["GOVERNMENT_POLICY", "RBI_MONETARY", "SEBI_REGULATORY", "MACRO"])
            )

        stmt = stmt.order_by(NewsEvent.published_at.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_coverage_summary(self) -> dict[str, int]:
        """Get summary statistics of news and policy coverage."""
        total_stmt = select(func.count(NewsEvent.id))
        total_cnt = (await self.session.execute(total_stmt)).scalar_one() or 0

        co_stmt = select(func.count(NewsEvent.id)).where(NewsEvent.event_type == "COMPANY")
        co_cnt = (await self.session.execute(co_stmt)).scalar_one() or 0

        gov_stmt = select(func.count(NewsEvent.id)).where(NewsEvent.event_type == "GOVERNMENT_POLICY")
        gov_cnt = (await self.session.execute(gov_stmt)).scalar_one() or 0

        rbi_stmt = select(func.count(NewsEvent.id)).where(NewsEvent.event_type == "RBI_MONETARY")
        rbi_cnt = (await self.session.execute(rbi_stmt)).scalar_one() or 0

        sebi_stmt = select(func.count(NewsEvent.id)).where(NewsEvent.event_type == "SEBI_REGULATORY")
        sebi_cnt = (await self.session.execute(sebi_stmt)).scalar_one() or 0

        macro_stmt = select(func.count(NewsEvent.id)).where(NewsEvent.event_type == "MACRO")
        macro_cnt = (await self.session.execute(macro_stmt)).scalar_one() or 0

        stocks_stmt = select(func.count(func.distinct(NewsEvent.stock_id))).where(NewsEvent.stock_id.is_not(None))
        stocks_cnt = (await self.session.execute(stocks_stmt)).scalar_one() or 0

        sectors_stmt = select(func.count(func.distinct(NewsEvent.sector_id))).where(NewsEvent.sector_id.is_not(None))
        sectors_cnt = (await self.session.execute(sectors_stmt)).scalar_one() or 0

        return {
            "total_events_count": total_cnt,
            "company_events_count": co_cnt,
            "government_policy_events_count": gov_cnt,
            "rbi_monetary_events_count": rbi_cnt,
            "sebi_regulatory_events_count": sebi_cnt,
            "macro_events_count": macro_cnt,
            "unique_stocks_covered": stocks_cnt,
            "unique_sectors_covered": sectors_cnt,
        }
