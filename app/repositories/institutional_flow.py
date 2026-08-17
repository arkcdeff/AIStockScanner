from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from app.models.institutional_flow import InstitutionalFlow
from app.repositories.base import BaseRepository


class InstitutionalFlowRepository(BaseRepository["InstitutionalFlow"]):
    def __init__(self, session: Any) -> None:
        super().__init__(InstitutionalFlow, session)

    async def get_for_date(
        self, trade_date: date, investor_type: str
    ) -> InstitutionalFlow | None:
        statement = select(InstitutionalFlow).where(
            InstitutionalFlow.trade_date == trade_date,
            InstitutionalFlow.investor_type == investor_type,
        )
        result = await self.session.scalars(statement)
        return result.one_or_none()

    async def get_range(
        self, start_date: date, end_date: date, investor_type: str
    ) -> list[InstitutionalFlow]:
        statement = select(InstitutionalFlow).where(
            InstitutionalFlow.trade_date >= start_date,
            InstitutionalFlow.trade_date <= end_date,
            InstitutionalFlow.investor_type == investor_type,
        )
        statement = statement.order_by(InstitutionalFlow.trade_date.desc())
        result = await self.session.scalars(statement)
        return list(result.all())

    async def get_range_for_types(
        self,
        start_date: date,
        end_date: date,
        investor_types: tuple[str, ...] = ("FII", "DII"),
    ) -> list[InstitutionalFlow]:
        """Load explicitly bounded institutional histories in one SQL query."""
        if not investor_types:
            return []
        statement = (
            select(InstitutionalFlow)
            .where(
                InstitutionalFlow.trade_date >= start_date,
                InstitutionalFlow.trade_date <= end_date,
                InstitutionalFlow.investor_type.in_(investor_types),
            )
            .order_by(
                InstitutionalFlow.investor_type,
                InstitutionalFlow.trade_date.desc(),
            )
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def upsert(
        self,
        trade_date: date,
        investor_type: str,
        buy_value: float | None = None,
        sell_value: float | None = None,
        net_value: float | None = None,
        **kwargs: Any,
    ) -> InstitutionalFlow:
        existing = await self.get_for_date(trade_date, investor_type)
        if existing:
            existing.buy_value = buy_value
            existing.sell_value = sell_value
            existing.net_value = net_value
            # Update cumulative values if provided
            if "one_day_cumulative" in kwargs:
                existing.one_day_cumulative = kwargs["one_day_cumulative"]
            if "three_day_cumulative" in kwargs:
                existing.three_day_cumulative = kwargs["three_day_cumulative"]
            if "five_day_cumulative" in kwargs:
                existing.five_day_cumulative = kwargs["five_day_cumulative"]
            if "twenty_day_cumulative" in kwargs:
                existing.twenty_day_cumulative = kwargs["twenty_day_cumulative"]
            for field in ("source_name", "observed_at", "fetched_at", "freshness_status"):
                if field in kwargs:
                    setattr(existing, field, kwargs[field])
            await self.session.flush()
            return existing
        else:
            new = InstitutionalFlow(
                trade_date=trade_date,
                investor_type=investor_type,
                buy_value=buy_value,
                sell_value=sell_value,
                net_value=net_value,
                **kwargs,
            )
            self.session.add(new)
            await self.session.flush()
            return new