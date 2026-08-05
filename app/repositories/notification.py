from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.alert import AlertEvent
from app.models.notification import (
    DailySummaryRun,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.models.stock import Stock
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

    async def list_filtered(
        self,
        *,
        enabled: bool | None = None,
        provider: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[NotificationChannel]:
        statement = select(NotificationChannel)
        if enabled is not None:
            statement = statement.where(NotificationChannel.enabled.is_(enabled))
        if provider is not None:
            statement = statement.where(NotificationChannel.provider == provider)
        statement = statement.order_by(NotificationChannel.id).offset(offset).limit(limit)
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

    async def list_filtered(
        self,
        *,
        channel_id: int | None = None,
        event_type: str | None = None,
        enabled: bool | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[NotificationPreference]:
        statement = select(NotificationPreference)
        if channel_id is not None:
            statement = statement.where(NotificationPreference.channel_id == channel_id)
        if event_type is not None:
            statement = statement.where(NotificationPreference.event_type == event_type)
        if enabled is not None:
            statement = statement.where(NotificationPreference.enabled.is_(enabled))
        statement = statement.order_by(NotificationPreference.id).offset(offset).limit(limit)
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

    async def status_summary(self, *, stale_minutes: int) -> dict:
        now = datetime.now(UTC)
        today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
        stale_cutoff = now - timedelta(minutes=stale_minutes)
        statement = select(
            func.count().filter(NotificationDelivery.status == "pending").label("pending_count"),
            func.count().filter(NotificationDelivery.status == "sending").label("sending_count"),
            func.count()
            .filter(NotificationDelivery.status == "retry_scheduled")
            .label("retry_scheduled_count"),
            func.count().filter(NotificationDelivery.status == "failed").label("failed_count"),
            func.count()
            .filter(
                NotificationDelivery.status.in_(["sent", "read"]),
                NotificationDelivery.sent_at >= today_start,
            )
            .label("sent_today"),
            func.count().filter(NotificationDelivery.status == "sent").label("unread_count"),
            func.max(NotificationDelivery.created_at).label("last_delivery_at"),
            func.count()
            .filter(
                NotificationDelivery.status == "sending",
                NotificationDelivery.updated_at < stale_cutoff,
            )
            .label("stale_sending_count"),
        )
        row = (await self.session.execute(statement)).one()
        return {
            "pending_count": row.pending_count or 0,
            "sending_count": row.sending_count or 0,
            "retry_scheduled_count": row.retry_scheduled_count or 0,
            "failed_count": row.failed_count or 0,
            "sent_today": row.sent_today or 0,
            "unread_count": row.unread_count or 0,
            "last_delivery_at": row.last_delivery_at,
            "stale_sending_count": row.stale_sending_count or 0,
        }

    @staticmethod
    def _detail_options():
        return (
            joinedload(NotificationDelivery.channel),
            joinedload(NotificationDelivery.alert_event).joinedload(AlertEvent.stock),
            joinedload(NotificationDelivery.alert_event).joinedload(AlertEvent.alert_rule),
        )

    async def list_history(
        self,
        *,
        status: str | None = None,
        provider: str | None = None,
        notification_type: str | None = None,
        channel_id: int | None = None,
        stock_symbol: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        unread_only: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> list[NotificationDelivery]:
        statement = select(NotificationDelivery)
        if provider is not None:
            statement = statement.join(NotificationChannel).where(
                NotificationChannel.provider == provider
            )
        if stock_symbol is not None:
            statement = (
                statement.join(AlertEvent, NotificationDelivery.alert_event_id == AlertEvent.id)
                .join(Stock, AlertEvent.stock_id == Stock.id)
                .where(func.upper(Stock.symbol) == stock_symbol.upper())
            )
        if status is not None:
            statement = statement.where(NotificationDelivery.status == status)
        if notification_type is not None:
            statement = statement.where(NotificationDelivery.notification_type == notification_type)
        if channel_id is not None:
            statement = statement.where(NotificationDelivery.channel_id == channel_id)
        if date_from is not None:
            statement = statement.where(NotificationDelivery.created_at >= date_from)
        if date_to is not None:
            statement = statement.where(NotificationDelivery.created_at < date_to)
        if unread_only:
            statement = statement.where(NotificationDelivery.status == "sent")
        statement = (
            statement.options(*self._detail_options())
            .order_by(NotificationDelivery.created_at.desc(), NotificationDelivery.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def get_detail(self, delivery_id: int) -> NotificationDelivery | None:
        statement = (
            select(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .options(*self._detail_options())
        )
        return await self.session.scalar(statement)


class DailySummaryRunRepository(BaseRepository[DailySummaryRun]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DailySummaryRun, session)

    async def get_for_date(self, summary_date: date) -> DailySummaryRun | None:
        statement = select(DailySummaryRun).where(DailySummaryRun.summary_date == summary_date)
        return await self.session.scalar(statement)

    async def list_recent(self, limit: int = 20) -> list[DailySummaryRun]:
        statement = (
            select(DailySummaryRun).order_by(DailySummaryRun.summary_date.desc()).limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def latest(self) -> DailySummaryRun | None:
        statement = select(DailySummaryRun).order_by(DailySummaryRun.summary_date.desc()).limit(1)
        return await self.session.scalar(statement)
