from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from loguru import logger
from sqlalchemy import select

from app.core.config import settings
from app.db.session import get_session_factory
from app.models.alert import AlertEvent
from app.schemas.notification import sanitize_notification_error
from app.services.daily_summary import DailySummaryExecution, DailySummaryService
from app.services.notification_service import NotificationService

_CREDENTIAL_PATTERN = re.compile(r"(://)([^:/\s@]+):([^@/\s]+)@")


def sanitize_job_error(error: object) -> str:
    value = _CREDENTIAL_PATTERN.sub(r"\1***:***@", str(error))
    return sanitize_notification_error(value) or "Notification job failed"


@dataclass
class DeliveryJobResult:
    status: str
    recovered: int = 0
    dispatched: int = 0
    events_processed: int = 0
    duration_ms: float = 0
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


async def run_pending_delivery_job() -> DeliveryJobResult:
    """Recover stale rows and deliver a bounded batch using a fresh session."""
    started = datetime.now(UTC)
    if not settings.notifications_enabled:
        logger.info("Notification delivery job skipped; notifications are disabled")
        return DeliveryJobResult(status="disabled")

    logger.info("Notification delivery job started")
    try:
        async with get_session_factory()() as session:
            service = NotificationService(session)
            recovered = await service.recover_stale_deliveries()
            dispatched = await service.deliver_pending(batch_size=settings.notification_batch_size)
    except Exception as exc:
        duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        error = sanitize_job_error(exc)
        logger.error(
            "Notification delivery job failed duration_ms={:.1f}: {}",
            duration_ms,
            error,
        )
        return DeliveryJobResult(
            status="failed",
            duration_ms=duration_ms,
            error=error,
        )

    duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
    logger.info(
        "Notification delivery job completed recovered={} dispatched={} duration_ms={:.1f}",
        recovered,
        dispatched,
        duration_ms,
    )
    return DeliveryJobResult(
        status="completed",
        recovered=recovered,
        dispatched=dispatched,
        duration_ms=duration_ms,
    )


async def deliver_alert_events_for_date(trade_date: date) -> DeliveryJobResult:
    """Create/send alert deliveries after a completed pipeline using a fresh session."""
    started = datetime.now(UTC)
    if not settings.notifications_enabled:
        return DeliveryJobResult(status="disabled")
    try:
        async with get_session_factory()() as session:
            event_ids = list(
                await session.scalars(
                    select(AlertEvent.id)
                    .where(AlertEvent.trade_date == trade_date)
                    .order_by(AlertEvent.id)
                )
            )
            service = NotificationService(session)
            for event_id in event_ids:
                await service.deliver_alert(event_id)
    except Exception as exc:
        duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        error = sanitize_job_error(exc)
        logger.error(
            "Post-pipeline alert notification failed date={} duration_ms={:.1f}: {}",
            trade_date,
            duration_ms,
            error,
        )
        return DeliveryJobResult(
            status="failed",
            duration_ms=duration_ms,
            error=error,
        )

    duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
    logger.info(
        "Post-pipeline alert notification completed date={} events={} duration_ms={:.1f}",
        trade_date,
        len(event_ids),
        duration_ms,
    )
    return DeliveryJobResult(
        status="completed",
        events_processed=len(event_ids),
        duration_ms=duration_ms,
    )


async def run_daily_summary_job(summary_date: date) -> DailySummaryExecution:
    """Run a daily summary with a fresh scheduler-owned database session."""
    async with get_session_factory()() as session:
        return await DailySummaryService(session).run(summary_date)
