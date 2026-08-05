from __future__ import annotations

import re
from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.alert import AlertEvent, AlertRule
from app.models.daily_price import DailyPrice
from app.models.notification import (
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.models.technical_analysis import TechnicalAnalysis
from app.notifications.base import NotificationMessage, NotificationResult
from app.notifications.exceptions import NotificationError
from app.notifications.manager import NotificationManager
from app.notifications.retry import RetryEngine
from app.repositories.notification import (
    NotificationChannelRepository,
    NotificationDeliveryRepository,
    NotificationPreferenceRepository,
)

EVENT_TYPE_ALERT = "alert_triggered"

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_RETRY_SCHEDULED = "retry_scheduled"
STATUS_SKIPPED = "skipped"
STATUS_READ = "read"


class NotificationConflictError(RuntimeError):
    """Raised when a requested delivery transition is not allowed."""


class NotificationService:
    """Delivers notifications for alert events through configured channels.

    The service loads an :class:`AlertEvent`, selects eligible channels,
    evaluates preferences, renders provider-specific content, persists a
    ``NotificationDelivery`` row, and dispatches through the provider factory.
    Database transactions are never held open across the network provider call.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        manager: NotificationManager | None = None,
        retry: RetryEngine | None = None,
    ) -> None:
        self.session = session
        self.manager = manager or NotificationManager()
        self.retry = retry or RetryEngine()
        self.channel_repo = NotificationChannelRepository(session)
        self.pref_repo = NotificationPreferenceRepository(session)
        self.delivery_repo = NotificationDeliveryRepository(session)

    # --------------------------------------------------------------- loading

    async def _load_event_payload(self, alert_event_id: int) -> dict | None:
        event = await self.session.get(AlertEvent, alert_event_id)
        if event is None:
            return None
        rule = (
            await self.session.get(AlertRule, event.alert_rule_id)
            if event.alert_rule_id is not None
            else None
        )
        stock = await self.session.get(Stock, event.stock_id)

        latest_price: DailyPrice | None = None
        if stock is not None:
            latest_price = await self.session.scalar(
                select(DailyPrice)
                .where(DailyPrice.stock_id == stock.id)
                .order_by(DailyPrice.trade_date.desc())
                .limit(1)
            )
        analysis: TechnicalAnalysis | None = None
        if stock is not None:
            analysis = await self.session.scalar(
                select(TechnicalAnalysis)
                .where(TechnicalAnalysis.stock_id == stock.id)
                .order_by(TechnicalAnalysis.trade_date.desc())
                .limit(1)
            )
        scan_count = 0
        if stock is not None:
            scan_count = (
                await self.session.scalar(
                    select(ScanResult.stock_id).where(ScanResult.stock_id == stock.id).limit(1)
                )
                is not None
            )

        return {
            "event": event,
            "rule": rule,
            "stock": stock,
            "latest_price": latest_price,
            "analysis": analysis,
            "scan_count": scan_count,
        }

    def _build_alert_context(self, payload: dict) -> dict:
        event: AlertEvent = payload["event"]
        rule: AlertRule | None = payload["rule"]
        stock: Stock | None = payload["stock"]
        price: DailyPrice | None = payload["latest_price"]
        analysis: TechnicalAnalysis | None = payload["analysis"]

        return {
            "symbol": stock.symbol if stock else "N/A",
            "alert_name": rule.name if rule else "N/A",
            "trigger_value": event.trigger_value,
            "latest_close": float(price.close) if price else None,
            "technical_score": analysis.overall_score if analysis else None,
            "trend_rating": analysis.trend_rating if analysis else None,
            "support": analysis.support if analysis else None,
            "resistance": analysis.resistance if analysis else None,
            "alert_time": event.created_at,
        }

    def _priority(self, payload: dict) -> int:
        analysis = payload.get("analysis")
        if analysis is not None and analysis.overall_score is not None:
            return int(analysis.overall_score)
        return 0

    # -------------------------------------------------------- preference eval

    def _in_quiet_hours(self, preference: NotificationPreference, now: datetime) -> bool:
        start = preference.quiet_hours_start
        end = preference.quiet_hours_end
        if start is None or end is None:
            return False
        if start == end:
            return True
        current = now.time()
        if start < end:
            return start <= current < end
        # overnight window (start > end) wraps past midnight
        return current >= start or current < end

    async def _evaluate_channel(
        self,
        channel: NotificationChannel,
        *,
        event_type: str,
        priority: int,
        now: datetime,
    ) -> tuple[str, NotificationPreference | None]:
        """Return a (decision, preference) pair.

        ``decision`` is one of STATUS_SKIPPED, STATUS_PENDING, STATUS_RETRY_SCHEDULED.
        """
        preference = next(
            (p for p in channel.preferences if p.event_type == event_type),
            None,
        )
        if channel.enabled is False:
            return STATUS_SKIPPED, preference
        if preference is not None:
            if preference.enabled is False:
                return STATUS_SKIPPED, preference
            if preference.minimum_priority is not None and priority < preference.minimum_priority:
                return STATUS_SKIPPED, preference
            if self._in_quiet_hours(preference, now):
                return STATUS_SKIPPED, preference
        return STATUS_PENDING, preference

    # ---------------------------------------------------------------- deliver

    async def deliver_alert(self, alert_event_id: int) -> dict[str, int | list[int]]:
        """Evaluate, render and dispatch an alert to all eligible channels."""
        summary: dict[str, int | list[int]] = {
            STATUS_SENT: 0,
            STATUS_FAILED: 0,
            STATUS_RETRY_SCHEDULED: 0,
            STATUS_SKIPPED: 0,
            "total": 0,
            "delivery_ids": [],
        }
        payload = await self._load_event_payload(alert_event_id)
        if payload is None:
            logger.warning(f"Notification: alert event {alert_event_id} not found")
            return summary

        priority = self._priority(payload)
        channels = await self.channel_repo.list_with_preferences()
        now = datetime.now().astimezone()

        for channel in channels:
            decision, _pref = await self._evaluate_channel(
                channel, event_type=EVENT_TYPE_ALERT, priority=priority, now=now
            )
            summary["total"] = int(summary["total"]) + 1
            if decision == STATUS_SKIPPED:
                delivery_id = await self._record_skipped(alert_event_id, channel)
                summary[STATUS_SKIPPED] = int(summary[STATUS_SKIPPED]) + 1
                delivery_ids = summary["delivery_ids"]
                assert isinstance(delivery_ids, list)
                if delivery_id not in delivery_ids:
                    delivery_ids.append(delivery_id)
                continue
            out = await self._dispatch(channel, payload)
            status = str(out["status"])
            if status not in {
                STATUS_SENT,
                STATUS_FAILED,
                STATUS_RETRY_SCHEDULED,
                STATUS_SKIPPED,
            }:
                status = STATUS_FAILED
            summary[status] = int(summary[status]) + 1
            delivery_ids = summary["delivery_ids"]
            assert isinstance(delivery_ids, list)
            delivery_id = int(out["delivery_id"])
            if delivery_id not in delivery_ids:
                delivery_ids.append(delivery_id)
        return summary

    async def _record_skipped(self, alert_event_id: int, channel: NotificationChannel) -> int:
        existing = await self.delivery_repo.existing_for_event_channel(alert_event_id, channel.id)
        if existing is not None:
            return existing.id
        delivery = NotificationDelivery(
            alert_event_id=alert_event_id,
            channel_id=channel.id,
            notification_type=EVENT_TYPE_ALERT,
            status=STATUS_SKIPPED,
        )
        self.session.add(delivery)
        await self.session.commit()
        return delivery.id

    async def _dispatch(self, channel: NotificationChannel, payload: dict) -> dict[str, int | str]:
        """Short-tx -> provider call -> short-tx update."""
        event_id = payload["event"].id
        existing = await self.delivery_repo.existing_for_event_channel(event_id, channel.id)
        if existing is not None:
            return {"status": STATUS_SKIPPED, "delivery_id": existing.id}

        context = self._build_alert_context(payload)
        rendered = self.manager.render(
            notification_type="alert", provider=channel.provider, context=context
        )
        delivery = NotificationDelivery(
            alert_event_id=event_id,
            channel_id=channel.id,
            notification_type=EVENT_TYPE_ALERT,
            subject=rendered["subject"],
            rendered_body=rendered["text"],
            status=STATUS_SENDING,
            attempt_count=1,
        )
        self.session.add(delivery)
        await self.session.commit()

        delivery_id = delivery.id
        config = channel.config_json or {}
        recipient = config.get("recipient", "")

        try:
            provider = self.manager.resolve_provider(provider=channel.provider, config=config)
            result = await self.manager.send(
                provider=provider,
                message=NotificationMessage(
                    recipient=recipient,
                    subject=rendered["subject"],
                    text=rendered["text"],
                    html=rendered["html"],
                ),
            )
        except NotificationError as exc:
            error_message = self._sanitize(str(exc), config=config)
            failed_status = await self._mark_failed(
                delivery_id,
                retryable=self.retry.is_retryable(exc),
                error_message=error_message,
                attempt_count=1,
            )
            return {"status": failed_status, "delivery_id": delivery_id}

        await self._mark_sent(delivery_id, result.external_message_id)
        return {"status": STATUS_SENT, "delivery_id": delivery_id}

    async def _mark_sent(self, delivery_id: int, external_message_id: str | None) -> None:
        delivery = await self.session.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return
        delivery.status = STATUS_SENT
        delivery.external_message_id = external_message_id
        delivery.sent_at = datetime.now().astimezone()
        await self.session.commit()

    async def _mark_failed(
        self,
        delivery_id: int,
        *,
        retryable: bool,
        error_message: str,
        attempt_count: int,
    ) -> str:
        delivery = await self.session.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return STATUS_FAILED
        delivery.attempt_count = attempt_count
        delivery.error_message = error_message
        if retryable and self.retry.should_retry(attempt_count=attempt_count + 1):
            delivery.status = STATUS_RETRY_SCHEDULED
            delivery.next_retry_at = self.retry.next_retry_at(retry_count=attempt_count)
        else:
            delivery.status = STATUS_FAILED
            delivery.next_retry_at = None
        await self.session.commit()
        return delivery.status

    # ------------------------------------------------------------ batch / retry

    async def deliver_pending(self, batch_size: int | None = None) -> int:
        """Attempt pending / due retry deliveries. Returns count dispatched."""
        batch = batch_size or settings.notification_batch_size
        due: list[NotificationDelivery] = await self.delivery_repo.list_due(batch_size=batch)
        dispatched = 0
        for delivery in due:
            await self._resend_delivery(delivery)
            dispatched += 1
        return dispatched

    async def _resend_delivery(self, delivery: NotificationDelivery) -> None:
        if delivery.status == STATUS_SENT:
            return
        if not self.retry.should_retry(attempt_count=delivery.attempt_count):
            delivery.status = STATUS_FAILED
            await self.session.commit()
            return
        channel = delivery.channel
        config = channel.config_json or {}
        recipient = config.get("recipient", "")
        delivery.status = STATUS_SENDING
        delivery.attempt_count += 1
        delivery.error_message = None
        delivery.next_retry_at = None
        await self.session.commit()

        try:
            provider = self.manager.resolve_provider(provider=channel.provider, config=config)
            result = await self.manager.send(
                provider=provider,
                message=NotificationMessage(
                    recipient=recipient,
                    subject=delivery.subject,
                    text=delivery.rendered_body,
                ),
            )
        except NotificationError as exc:
            await self._mark_failed(
                delivery.id,
                retryable=self.retry.is_retryable(exc),
                error_message=self._sanitize(str(exc), config=config),
                attempt_count=delivery.attempt_count,
            )
            return
        await self._mark_sent(delivery.id, result.external_message_id)

    async def retry_delivery(
        self, delivery_id: int, *, reject_completed: bool = False
    ) -> NotificationDelivery | None:
        delivery = await self.delivery_repo.get_with_channel(delivery_id)
        if delivery is None:
            return None
        if delivery.status in {STATUS_SENT, STATUS_READ}:
            if reject_completed:
                raise NotificationConflictError(
                    "Completed notification deliveries cannot be retried"
                )
            return delivery  # never resend a successfully-sent delivery
        await self._resend_delivery(delivery)
        return await self.session.get(NotificationDelivery, delivery_id)

    async def recover_stale_deliveries(self) -> int:
        """Reset stale ``sending`` rows to allow a retry attempt."""
        cutoff = datetime.now().astimezone()
        from datetime import timedelta

        cutoff = cutoff - timedelta(minutes=self.retry.stale_minutes)
        stale = await self.delivery_repo.list_stale_sending(cutoff=cutoff)
        for delivery in stale:
            delivery.status = STATUS_RETRY_SCHEDULED
            delivery.next_retry_at = self.retry.next_retry_at(retry_count=delivery.attempt_count)
        await self.session.commit()
        return len(stale)

    async def mark_read(self, delivery_id: int) -> NotificationDelivery | None:
        delivery = await self.session.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return None
        if delivery.status == STATUS_SENT:
            delivery.status = STATUS_READ
            await self.session.commit()
        return delivery

    async def test_provider(
        self,
        *,
        provider: str,
        config: dict | None = None,
        recipient: str = "",
    ) -> NotificationResult:
        """Send a safe, non-persistent provider test message."""
        provider_instance = self.manager.resolve_provider(provider=provider, config=config or {})
        destination = recipient or (config or {}).get("recipient", "")
        if not destination and provider in {"mock", "in_app"}:
            destination = "ui-test"
        return await self.manager.send(
            provider=provider_instance,
            message=NotificationMessage(
                recipient=destination,
                subject="AI Stock Scanner notification test",
                text="This is a safe test notification from AI Stock Scanner.",
                html="<p>This is a safe test notification from AI Stock Scanner.</p>",
            ),
        )

    # ----------------------------------------------------------------- sanity

    @staticmethod
    def _sanitize(message: str, *, config: dict | None = None) -> str:
        """Shorten and strip credentials from provider error messages."""
        safe = (message.strip() or "unknown provider error")[:500]
        config = config or {}
        for key in ("api_key", "bot_token", "chat_id", "password", "recipient", "token"):
            value = config.get(key)
            if value:
                safe = safe.replace(str(value), "********")
        return re.sub(
            r"(?i)(api[_-]?key|bot[_-]?token|chat[_-]?id|password|recipient|token)"
            r"\s*[=:]\s*[^\s,;]+",
            lambda match: f"{match.group(1)}=********",
            safe,
        )
