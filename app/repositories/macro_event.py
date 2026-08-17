from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.models.macro_event import EconEvent
from app.repositories.base import BaseRepository


class EconEventRepository(BaseRepository["EconEvent"]):
    def __init__(self, session: Any) -> None:
        super().__init__(EconEvent, session)

    async def get_for_code_reference(
        self, event_code: str, reference_time: datetime
    ) -> EconEvent | None:
        statement = select(EconEvent).where(
            EconEvent.event_code == event_code,
            EconEvent.reference_time == reference_time,
        )
        result = await self.session.scalars(statement)
        return result.one_or_none()

    async def get_range_for_codes(
        self,
        start_time: datetime,
        end_time: datetime,
        codes: tuple[str, ...] = (),
    ) -> list[EconEvent]:
        """Bulk-load a bounded reference-time window in a single query."""
        statement = select(EconEvent).where(
            EconEvent.reference_time >= start_time,
            EconEvent.reference_time <= end_time,
        )
        if codes:
            statement = statement.where(EconEvent.event_code.in_(codes))
        statement = statement.order_by(
            EconEvent.event_code,
            EconEvent.reference_time.desc(),
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def upsert(
        self,
        event_code: str,
        reference_time: datetime,
        actual: float | None = None,
        forecast: float | None = None,
        previous: float | None = None,
        **kwargs: Any,
    ) -> EconEvent:
        existing = await self.get_for_code_reference(event_code, reference_time)
        if existing:
            existing.actual = actual
            existing.forecast = forecast
            existing.previous = previous
            for field in ("unit", "source_name", "source_reference", "observed_at",
                          "available_at", "fetched_at", "freshness_status"):
                if field in kwargs:
                    setattr(existing, field, kwargs[field])
            await self.session.flush()
            return existing
        new = EconEvent(
            event_code=event_code,
            reference_time=reference_time,
            actual=actual,
            forecast=forecast,
            previous=previous,
            **kwargs,
        )
        self.session.add(new)
        await self.session.flush()
        return new
