from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
from app.models.notification import (
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.notifications.base import NotificationMessage
from app.notifications.exceptions import NotificationError
from app.notifications.manager import NotificationManager
from app.notifications.retry import RetryEngine
from app.repositories.notification import (
    NotificationChannelRepository,
    NotificationDeliveryRepository,
    NotificationPreferenceRepository,
)
from app.services.composite_market_intelligence import CompositeMarketIntelligenceResult

EVENT_TYPE_MARKET_REGIME_CHANGE = "market_regime_change"
EVENT_TYPE_MARKET_SCORE_TRANSITION = "market_score_transition"
EVENT_TYPE_MARKET_VIX_EXTREME = "market_vix_extreme"
EVENT_TYPE_MARKET_MACRO_RISK = "market_macro_risk"

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_RETRY_SCHEDULED = "retry_scheduled"
STATUS_SKIPPED = "skipped"
STATUS_READ = "read"

# Cooldown periods (in hours) for each event type
COOLDOWN_HOURS = {
    EVENT_TYPE_MARKET_REGIME_CHANGE: 24,
    EVENT_TYPE_MARKET_SCORE_TRANSITION: 12,
    EVENT_TYPE_MARKET_VIX_EXTREME: 6,
    EVENT_TYPE_MARKET_MACRO_RISK: 24,
}

# Score transition threshold (absolute change)
SCORE_TRANSITION_THRESHOLD = Decimal("15.0")

# VIX extreme threshold (directional score <= -60)
VIX_EXTREME_THRESHOLD = Decimal("-60.0")

# Top tier of the existing Task 18.8 importance enum/contract (high/medium/low).
HIGH_MACRO_EVENT_IMPORTANCE = "high"


def _high_importance_upcoming_events(
    upcoming_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic subset: upcoming macro events with the top-tier importance.

    Consumes the Task 18.8 ``upcoming_events`` contract (lifecycle ``upcoming``
    within the evaluation lookahead). No direction is predicted: upcoming events
    carry no signal by design. The list is sorted by scheduled reference time so
    the dedup identity is stable across scheduler cycles.
    """
    events = [
        event
        for event in upcoming_events
        if event.get("importance") == HIGH_MACRO_EVENT_IMPORTANCE
    ]
    return sorted(
        events,
        key=lambda event: (event.get("reference_time") or "", event.get("event_code") or ""),
    )


@dataclass
class MarketIntelligenceNotificationResult:
    event_type: str
    market_date: str
    triggered: bool
    from_state: str | None = None
    to_state: str | None = None
    score_change: float | None = None
    snapshot_id: int | None = None
    deliveries_created: int = 0
    deliveries_skipped: int = 0
    error: str | None = None


class MarketIntelligenceNotificationService:
    """Evaluates deterministic market intelligence triggers and sends notifications.

    All trigger decisions are deterministic. AI explanation is optional and only
    adds explanatory text AFTER a deterministic trigger fires.
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

    async def evaluate_and_notify(
        self,
        *,
        current_result: CompositeMarketIntelligenceResult,
        previous_result: CompositeMarketIntelligenceResult | None = None,
        market_date: date,
    ) -> list[MarketIntelligenceNotificationResult]:
        """Evaluate all deterministic triggers and send notifications if needed.

        Args:
            current_result: Current composite intelligence evaluation
            previous_result: Previous evaluation for comparison (regime/score changes)
            market_date: The market date being evaluated

        Returns:
            List of notification results for each trigger type
        """
        results: list[MarketIntelligenceNotificationResult] = []

        # 1. Regime change notification
        regime_result = await self._check_regime_change(
            current=current_result,
            previous=previous_result,
            market_date=market_date,
        )
        results.append(regime_result)

        # 2. Composite score transition notification
        score_result = await self._check_score_transition(
            current=current_result,
            previous=previous_result,
            market_date=market_date,
        )
        results.append(score_result)

        # 3. Extreme VIX notification
        vix_result = await self._check_vix_extreme(
            current=current_result,
            market_date=market_date,
        )
        results.append(vix_result)

        # 4. High-risk macro event notification
        macro_result = await self._check_macro_risk(
            current=current_result,
            market_date=market_date,
        )
        results.append(macro_result)

        return results

    async def _check_regime_change(
        self,
        *,
        current: CompositeMarketIntelligenceResult,
        previous: CompositeMarketIntelligenceResult | None,
        market_date: date,
    ) -> MarketIntelligenceNotificationResult:
        """Check for regime change and notify if detected."""
        current_regime = current.regime
        previous_regime = previous.regime if previous else None

        triggered = (
            current_regime is not None
            and previous_regime is not None
            and current_regime != previous_regime
        )

        result = MarketIntelligenceNotificationResult(
            event_type=EVENT_TYPE_MARKET_REGIME_CHANGE,
            market_date=market_date.isoformat(),
            triggered=triggered,
            from_state=previous_regime,
            to_state=current_regime,
            snapshot_id=current.snapshot_id,
        )

        if not triggered:
            return result

        # Check cooldown
        cooldown_check = await self._is_in_cooldown(
            EVENT_TYPE_MARKET_REGIME_CHANGE, market_date,
            from_state=previous_regime, to_state=current_regime,
        )
        if cooldown_check:
            msg = f"{previous_regime} -> {current_regime}"
            logger.info("Regime change notification suppressed by cooldown: {}", msg)
            result.deliveries_skipped = 1
            return result

        # Send notifications
        deliveries = await self._send_market_intelligence_notification(
            event_type=EVENT_TYPE_MARKET_REGIME_CHANGE,
            market_date=market_date,
            context={
                "event_type": "regime_change",
                "from_regime": previous_regime,
                "to_regime": current_regime,
                "composite_score": current.composite_score,
                "confidence_score": current.confidence_score,
                "market_date": market_date.isoformat(),
            },
            from_state=previous_regime,
            to_state=current_regime,
        )
        result.deliveries_created = deliveries
        return result

    async def _check_score_transition(
        self,
        *,
        current: CompositeMarketIntelligenceResult,
        previous: CompositeMarketIntelligenceResult | None,
        market_date: date,
    ) -> MarketIntelligenceNotificationResult:
        """Check for large composite score transition and notify if threshold exceeded."""
        current_score = current.composite_score
        previous_score = previous.composite_score if previous else None

        triggered = False
        score_change = None
        if current_score is not None and previous_score is not None:
            change = abs(Decimal(str(current_score)) - Decimal(str(previous_score)))
            if change >= SCORE_TRANSITION_THRESHOLD:
                triggered = True
                score_change = float(change)

        result = MarketIntelligenceNotificationResult(
            event_type=EVENT_TYPE_MARKET_SCORE_TRANSITION,
            market_date=market_date.isoformat(),
            triggered=triggered,
            score_change=score_change,
            snapshot_id=current.snapshot_id,
        )

        if not triggered:
            return result

        # Check cooldown
        cooldown_check = await self._is_in_cooldown(
            EVENT_TYPE_MARKET_SCORE_TRANSITION, market_date
        )
        if cooldown_check:
            logger.info(
                "Score transition notification suppressed by cooldown: change={}", score_change
            )
            result.deliveries_skipped = 1
            return result

        deliveries = await self._send_market_intelligence_notification(
            event_type=EVENT_TYPE_MARKET_SCORE_TRANSITION,
            market_date=market_date,
            context={
                "event_type": "score_transition",
                "previous_score": previous_score,
                "current_score": current_score,
                "score_change": score_change,
                "regime": current.regime,
                "confidence_score": current.confidence_score,
                "market_date": market_date.isoformat(),
            },
        )
        result.deliveries_created = deliveries
        return result

    async def _check_vix_extreme(
        self,
        *,
        current: CompositeMarketIntelligenceResult,
        market_date: date,
    ) -> MarketIntelligenceNotificationResult:
        """Check for extreme India VIX condition and notify."""
        vix_factor = next((f for f in current.factors if f.name == "volatility"), None)
        triggered = False
        if vix_factor and vix_factor.score is not None:
            if Decimal(str(vix_factor.score)) <= VIX_EXTREME_THRESHOLD:
                triggered = True

        result = MarketIntelligenceNotificationResult(
            event_type=EVENT_TYPE_MARKET_VIX_EXTREME,
            market_date=market_date.isoformat(),
            triggered=triggered,
            from_state=vix_factor.score if vix_factor else None,
            snapshot_id=current.snapshot_id,
        )

        if not triggered:
            return result

        cooldown_check = await self._is_in_cooldown(
            EVENT_TYPE_MARKET_VIX_EXTREME, market_date
        )
        if cooldown_check:
            logger.info(
                "VIX extreme notification suppressed by cooldown: score={}", vix_factor.score
            )
            result.deliveries_skipped = 1
            return result

        deliveries = await self._send_market_intelligence_notification(
            event_type=EVENT_TYPE_MARKET_VIX_EXTREME,
            market_date=market_date,
            context={
                "event_type": "vix_extreme",
                "vix_score": vix_factor.score,
                "vix_direction": vix_factor.direction,
                "regime": current.regime,
                "composite_score": current.composite_score,
                "confidence_score": current.confidence_score,
                "market_date": market_date.isoformat(),
            },
        )
        result.deliveries_created = deliveries
        return result

    async def _check_macro_risk(
        self,
        *,
        current: CompositeMarketIntelligenceResult,
        market_date: date,
    ) -> MarketIntelligenceNotificationResult:
        """Check for high-risk upcoming Task 18.8 macro events and notify.

        Trigger contract (Task 18.12): ``market_macro_risk`` fires when the
        Task 18.8 deterministic macro/event engine reports an *upcoming*
        scheduled macro event (lifecycle ``upcoming`` within the evaluation
        lookahead) whose importance is the top tier of the existing contract
        (``"high"``). The existing importance enum is ``high/medium/low`` and
        has no ``very_high`` tier; ``"high"`` is the top tier.

        Generic market-risk signals (HIGH_VIX, EXTREME_NEGATIVE_BREADTH,
        EXTREME_NEGATIVE_GLOBAL, STRONG_CONTRADICTION) remain deterministic
        composite risk information but are NOT a macro-event notification.

        The trigger is fully deterministic: it never predicts event direction
        (upcoming events contribute no directional signal) and never consults
        AI. Deduplication uses the real event identity (``event_code`` +
        scheduled ``reference_time``) plus the market date so the same upcoming
        event is not re-sent every scheduler cycle.
        """
        upcoming = getattr(current, "upcoming_macro_events", None) or []
        high_risk_events = _high_importance_upcoming_events(upcoming)

        triggered = bool(high_risk_events)
        event = high_risk_events[0] if high_risk_events else None
        event_code = event.get("event_code") if event else None
        event_reference_time = event.get("reference_time") if event else None
        risk_detail = None
        if high_risk_events:
            risk_detail = "; ".join(
                e.get("name") or e.get("event_code") or "Unknown"
                for e in high_risk_events
            )

        result = MarketIntelligenceNotificationResult(
            event_type=EVENT_TYPE_MARKET_MACRO_RISK,
            market_date=market_date.isoformat(),
            triggered=triggered,
            from_state=risk_detail,
            snapshot_id=current.snapshot_id,
        )

        if not triggered:
            return result

        if await self._is_in_cooldown(EVENT_TYPE_MARKET_MACRO_RISK, market_date):
            logger.info("Macro risk notification suppressed by cooldown: {}", risk_detail)
            result.deliveries_skipped = 1
            return result

        macro_factor = next((f for f in current.factors if f.name == "macro"), None)
        deliveries = await self._send_market_intelligence_notification(
            event_type=EVENT_TYPE_MARKET_MACRO_RISK,
            market_date=market_date,
            context={
                "event_type": "macro_risk",
                "macro_events": high_risk_events,
                "event_code": event_code,
                "event_reference_time": event_reference_time,
                "event_name": event.get("name") if event else None,
                "event_importance": event.get("importance") if event else None,
                "risk_detail": risk_detail,
                "macro_score": macro_factor.score if macro_factor else None,
                "macro_direction": macro_factor.direction if macro_factor else None,
                "regime": current.regime,
                "composite_score": current.composite_score,
                "confidence_score": current.confidence_score,
                "market_date": market_date.isoformat(),
            },
            from_state=event_code,
            to_state=event_reference_time,
        )
        result.deliveries_created = deliveries
        return result

    async def _is_in_cooldown(
        self,
        event_type: str,
        market_date: date,
        *,
        from_state: str | None = None,
        to_state: str | None = None,
    ) -> bool:
        """Check if a notification of this type is in cooldown period.

        Deduplication is based on event_type + market_date + from_state + to_state.
        """
        cooldown_hours = COOLDOWN_HOURS.get(event_type, 24)
        cutoff = datetime.now(UTC) - timedelta(hours=cooldown_hours)

        # Check for recent delivery with same event_type and market_date
        recent = await self.delivery_repo.list_recent_by_type_and_date(
            notification_type=event_type,
            market_date=market_date,
            since=cutoff,
        )
        if not recent:
            return False

        # If states are provided, check for exact state match
        if from_state is not None and to_state is not None:
            for delivery in recent:
                subject = delivery.subject or ""
                if from_state in subject and to_state in subject:
                    return True
            return False

        # Otherwise, any recent delivery of this type for this date counts
        return len(recent) > 0

    async def _send_market_intelligence_notification(
        self,
        event_type: str,
        market_date: date,
        context: dict[str, Any],
        from_state: str | None = None,
        to_state: str | None = None,
    ) -> int:
        """Send market intelligence notification through eligible channels."""
        # Get eligible channels for market intelligence notifications
        channels = await self.channel_repo.list_with_preferences()
        now = datetime.now(UTC).astimezone()
        eligible: list[NotificationChannel] = []

        for channel in channels:
            if channel.enabled is False:
                continue
            pref = next(
                (p for p in channel.preferences if p.event_type == event_type),
                None,
            )
            if pref is None:
                continue
            if pref.enabled is False:
                continue
            if pref.minimum_priority is not None and 0 < pref.minimum_priority:
                continue
            if self._in_quiet_hours(pref, now):
                continue
            eligible.append(channel)

        sent_count = 0
        for channel in eligible:
            # Check for existing delivery (idempotency)
            existing = await self.delivery_repo.existing_by_type_date_states(
                channel_id=channel.id,
                notification_type=event_type,
                market_date=market_date,
                from_state=from_state,
                to_state=to_state,
            )
            if existing is not None:
                continue

            # Render message
            rendered = self.manager.render(
                notification_type=event_type,
                provider=channel.provider,
                context=context,
            )

            # Create delivery record
            delivery = NotificationDelivery(
                channel_id=channel.id,
                notification_type=event_type,
                subject=rendered["subject"],
                rendered_body=rendered["text"],
                status=STATUS_SENDING,
                attempt_count=1,
            )
            self.session.add(delivery)
            await self.session.commit()
            delivery_id = delivery.id

            # Send via provider
            config = channel.config_json or {}
            recipient = config.get("recipient", "") or config.get("chat_id", "")
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
                await self._mark_failed(
                    delivery_id,
                    retryable=self.retry.is_retryable(exc),
                    error_message=self._sanitize(str(exc), config=config),
                    attempt_count=1,
                )
                continue

            await self._mark_sent(delivery_id, result.external_message_id)
            sent_count += 1

        return sent_count

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
        return current >= start or current < end

    async def _mark_sent(self, delivery_id: int, external_message_id: str | None) -> None:
        delivery = await self.session.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return
        delivery.status = STATUS_SENT
        delivery.external_message_id = external_message_id
        delivery.sent_at = datetime.now(UTC)
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

    @staticmethod
    def _sanitize(message: str, *, config: dict | None = None) -> str:
        safe = (message.strip() or "unknown provider error")[:500]
        config = config or {}
        for key in ("api_key", "bot_token", "chat_id", "password", "recipient", "token"):
            value = config.get(key)
            if value:
                safe = safe.replace(str(value), "********")
        import re
        return re.sub(
            r"(?i)(api[_-]?key|bot[_-]?token|chat[_-]?id|password|recipient|token)"
            r"\s*[=:]\s*[^\s,;]+",
            lambda match: f"{match.group(1)}=********",
            safe,
        )


async def get_previous_snapshot(
    session: AsyncSession, market_date: date
) -> MarketIntelligenceSnapshot | None:
    """Get the most recent market intelligence snapshot before the given date."""
    result = await session.execute(
        select(MarketIntelligenceSnapshot)
        .where(MarketIntelligenceSnapshot.market_date < market_date)
        .order_by(MarketIntelligenceSnapshot.market_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def snapshot_to_composite_result(
    snapshot: MarketIntelligenceSnapshot,
) -> CompositeMarketIntelligenceResult:
    """Convert a persisted snapshot to a CompositeMarketIntelligenceResult for comparison."""

    from app.services.composite_market_intelligence import (
        CompositeMarketIntelligenceResult,
        FactorOut,
    )

    scores = {
        "price_action": snapshot.price_action_score,
        "breadth": snapshot.breadth_score,
        "sector": snapshot.sector_score,
        "institutional": snapshot.institutional_score,
        "volatility": snapshot.volatility_score,
        "global": snapshot.global_score,
        "gift_nifty": snapshot.gift_nifty_score,
        "currency": snapshot.currency_score,
        "crude": snapshot.crude_score,
        "macro": snapshot.macro_score,
    }

    factors = []
    for name, score in scores.items():
        factors.append(
            FactorOut(
                name=name,
                score=float(score) if score is not None else None,
                configured_weight=0.0,
                effective_weight=0.0,
                confidence=0.0,
                completeness=0.0,
                freshness="stale",
                direction=None,
                provenance="historical_snapshot",
            )
        )

    comp_score = (
        float(snapshot.composite_score) if snapshot.composite_score is not None else None
    )
    conf_score = (
        float(snapshot.confidence_score) if snapshot.confidence_score is not None else None
    )
    data_comp = (
        float(snapshot.data_completeness_percent)
        if snapshot.data_completeness_percent is not None
        else None
    )

    return CompositeMarketIntelligenceResult(
        evaluation_time=snapshot.generated_at,
        market_date=snapshot.market_date,
        factors=factors,
        composite_score=comp_score,
        regime=snapshot.regime,
        confidence_score=conf_score,
        confidence_state="unknown",
        data_completeness_percent=data_comp,
        snapshot_id=snapshot.id,
    )