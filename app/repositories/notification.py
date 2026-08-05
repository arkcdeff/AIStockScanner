from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.notification import (
    DailySummaryRun,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.repositories.base import BaseRepository


class NotificationChannelRepository(BaseRepository[NotificationChannel]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(NotificationChannel, session)

    async def get_by_name(self, name: str) -> NotificationChannel | None:
        statement = select(NotificationChannel).where(NotificationChannel.name == name)
        return await self.session.scalar(statement)

    async def list_enabled(self) -> list[NotificationChannel]:
        statement = (
            select(NotificationChannel)
            .where(NotificationChannel.enabled.is_(True))
            .order_by(NotificationChannel.id)
        )
        return list(await self.session.scalars(statement))

    async def list_eligible(self) -> list[NotificationChannel]:
        """Return enabled channels with their preferences eagerly loaded.

        One query avoids the N+1 lookups a caller would otherwise perform when
        evaluating preferences per alert/channel pair.
        """
        statement = (
            select(NotificationChannel)
            .where(NotificationChannel.enabled.is_(True))
            .options(selectinload(NotificationChannel.preferences))
            .order_by(NotificationChannel.id)
        )
        return list(await self.session.scalars(statement))

    async def list_with_preferences(self) -> list[NotificationChannel]:
        from sqlalchemy.orm import selectinload

        statement = (
            select(NotificationChannel)
            .options(selectinload(NotificationChannel.preferences))
            .order_by(NotificationChannel.id)
        )
        return list(await self.session.scalars(statement))


class NotificationPreferenceRepository(BaseRepository[NotificationPreference]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(NotificationPreference, session)

    async def get_for_channel(
        self, channel_id: int, event_type: str
    ) -> NotificationPreference | None:
        statement = select(NotificationPreference).where(
            NotificationPreference.channel_id == channel_id,
            NotificationPreference.event_type == event_type,
        )
        return await self.session.scalar(statement)

    async def list_for_channel(self, channel_id: int) -> list[NotificationPreference]:
        statement = (
            select(NotificationPreference)
            .where(NotificationPreference.channel_id == channel_id)
            .order_by(NotificationPreference.id)
        )
        return list(await self.session.scalars(statement))

    async def list_enabled_for_event(self, event_type: str) -> list[NotificationPreference]:
        from sqlalchemy.orm import selectinload

        statement = (
            select(NotificationPreference)
            .where(
                NotificationPreference.event_type == event_type,
                NotificationPreference.enabled.is_(True),
            )
            .options(selectinload(NotificationPreference.channel))
            .order_by(NotificationPreference.id)
        )
        return list(await self.session.scalars(statement))


class NotificationDeliveryRepository(BaseRepository[NotificationDelivery]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(NotificationDelivery, session)

    async def list_for_channel(
        self, channel_id: int, limit: int = 100
    ) -> list[NotificationDelivery]:
        statement = (
            select(NotificationDelivery)
            .where(NotificationDelivery.channel_id == channel_id)
            .order_by(NotificationDelivery.id.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def list_pending_retries(self, limit: int = 100) -> list[NotificationDelivery]:
        statement = (
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status.in_(["pending", "failed"]),
                NotificationDelivery.next_retry_at.isnot(None),
            )
            .order_by(NotificationDelivery.next_retry_at)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def list_by_event(self, alert_event_id: int) -> list[NotificationDelivery]:
        statement = (
            select(NotificationDelivery)
            .where(NotificationDelivery.alert_event_id == alert_event_id)
            .order_by(NotificationDelivery.id)
        )
        return list(await self.session.scalars(statement))

    async def existing_for_event_channel(
        self, alert_event_id: int, channel_id: int
    ) -> NotificationDelivery | None:
        """Return an existing delivery for the alert/channel pair (if any)."""
        statement = (
            select(NotificationDelivery)
            .where(
                NotificationDelivery.alert_event_id == alert_event_id,
                NotificationDelivery.channel_id == channel_id,
            )
            .order_by(NotificationDelivery.id)
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def list_due(
        self, *, limit: int = 100, batch_size: int | None = None
    ) -> list[NotificationDelivery]:
        """Return deliveries that are pending or scheduled for retry.

        ``pending`` rows are always returned (first attempt). ``retry_scheduled``
        and ``failed`` rows are returned only when their ``next_retry_at`` has
        passed. Channels are eager-loaded to avoid N+1 during dispatch.
        """
        take = batch_size or limit
        statement = (
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status.in_(["pending", "retry_scheduled", "failed"]),
                (
                    NotificationDelivery.next_retry_at.is_(None)
                    | (NotificationDelivery.next_retry_at <= datetime.now())
                ),
            )
            .options(selectinload(NotificationDelivery.channel))
            .order_by(NotificationDelivery.next_retry_at)
            .limit(take)
        )
        return list(await self.session.scalars(statement))

    async def get_with_channel(self, delivery_id: int) -> NotificationDelivery | None:
        statement = (
            select(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .options(selectinload(NotificationDelivery.channel))
        )
        return await self.session.scalar(statement)

    async def list_stale_sending(
        self, *, cutoff: datetime, limit: int = 100
    ) -> list[NotificationDelivery]:
        """Return ``sending`` rows that have not progressed before ``cutoff``."""
        statement = (
            select(NotificationDelivery)
            .where(
                NotificationDelivery.status == "sending",
                NotificationDelivery.updated_at < cutoff,
            )
            .order_by(NotificationDelivery.updated_at)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))


class DailySummaryRunRepository(BaseRepository[DailySummaryRun]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DailySummaryRun, session)

    async def get_for_date(self, summary_date: date) -> DailySummaryRun | None:
        statement = select(DailySummaryRun).where(DailySummaryRun.summary_date == summary_date)
        return await self.session.scalar(statement)

    async def list_recent(self, limit: int = 20) -> list[DailySummaryRun]:
        statement = (
            select(DailySummaryRun)
            .order_by(DailySummaryRun.summary_date.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def latest(self) -> DailySummaryRun | None:
        statement = (
            select(DailySummaryRun)
            .order_by(DailySummaryRun.summary_date.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)