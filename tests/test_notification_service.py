from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.alert import AlertEvent, AlertRule
from app.models.notification import (
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.notifications.renderer import NotificationRenderer
from app.notifications.retry import RetryEngine
from app.services.notification_service import (
    EVENT_TYPE_ALERT,
    STATUS_SENT,
    NotificationService,
)
from tests.seed_helpers import seed_stock

# ------------------------------------------------------------------ renderer


def _alert_context() -> dict:
    return {
        "symbol": "RELIANCE",
        "alert_name": "Breakout",
        "trigger_value": 3000.5,
        "latest_close": 3010.25,
        "technical_score": 82,
        "trend_rating": "Strong",
        "support": 2950.0,
        "resistance": 3050.0,
        "alert_time": datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
    }


def test_renderer_telegram_template():
    out = NotificationRenderer().render(
        notification_type="alert", provider="telegram", context=_alert_context()
    )
    assert "ALERT: RELIANCE" in out["text"]
    assert "Breakout" in out["text"]
    assert "82" in out["text"]
    assert "Strong" in out["text"]
    assert out["html"] is None


def test_renderer_email_html_and_plain():
    out = NotificationRenderer().render(
        notification_type="alert", provider="email", context=_alert_context()
    )
    assert "RELIANCE" in out["subject"]
    assert out["text"] is not None
    assert out["html"] is not None
    assert "<h2>ALERT: RELIANCE</h2>" in out["html"]
    assert "<table>" in out["html"]


def test_renderer_html_escapes_unsafe_notes():
    ctx = _alert_context()
    ctx["symbol"] = '<script>alert("x")</script>'
    ctx["note"] = '<img src=x onerror="alert(1)">'
    out = NotificationRenderer().render(
        notification_type="alert", provider="email", context=ctx
    )
    assert "<script>" not in out["html"]
    assert "&lt;script&gt;" in out["html"]
    assert "<img" not in out["html"]


def test_renderer_missing_analytics_does_not_fail():
    ctx = _alert_context()
    ctx["technical_score"] = None
    ctx["trend_rating"] = None
    ctx["support"] = None
    out = NotificationRenderer().render(
        notification_type="alert", provider="telegram", context=ctx
    )
    assert "N/A" in out["text"]


def test_renderer_missing_price_does_not_fail():
    ctx = _alert_context()
    ctx["latest_close"] = None
    out = NotificationRenderer().render(
        notification_type="alert", provider="telegram", context=ctx
    )
    assert "N/A" in out["text"]


def test_renderer_daily_summary():
    out = NotificationRenderer().render(
        notification_type="daily_summary",
        provider="email",
        context={
            "summary_date": "2026-01-01",
            "alerts_count": 3,
            "scanner_matches": 12,
            "top_rated_count": 5,
            "watchlist_changes": 1,
            "portfolio_value": 1000000.5,
        },
    )
    assert "Daily Summary" in out["subject"]
    assert "3" in out["html"]


# ---------------------------------------------------------------- retry engine


def test_retry_engine_retryable_vs_permanent():
    from app.notifications.exceptions import (
        NotificationPermanentError,
        NotificationTimeoutError,
    )

    engine = RetryEngine(max_attempts=3, retry_minutes=15, stale_minutes=30)
    assert engine.is_retryable(NotificationTimeoutError("x")) is True
    assert engine.is_retryable(NotificationPermanentError("x")) is False
    assert engine.is_permanent(NotificationPermanentError("x")) is True


def test_retry_engine_max_attempts():
    engine = RetryEngine(max_attempts=2, retry_minutes=15, stale_minutes=30)
    assert engine.should_retry(attempt_count=0) is True
    assert engine.should_retry(attempt_count=1) is True
    assert engine.should_retry(attempt_count=2) is False


def test_retry_engine_stale_sending():
    engine = RetryEngine(max_attempts=3, retry_minutes=15, stale_minutes=30)
    old = datetime.now(UTC) - timedelta(minutes=60)
    assert engine.is_stale_sending(old) is True
    assert engine.is_stale_sending(datetime.now(UTC)) is False


# ------------------------------------------------------------- seeding helpers


async def _make_channel(
    *, name: str = "Mock", provider: str = "mock", enabled: bool = True, config: dict | None = None
) -> NotificationChannel:
    factory = get_session_factory()
    async with factory() as session:
        channel = NotificationChannel(
            name=name,
            provider=provider,
            enabled=enabled,
            config_json=config or {"recipient": "test"},
        )
        session.add(channel)
        await session.commit()
        await session.refresh(channel)
        return channel


async def _make_preference(
    *, channel_id: int, enabled: bool = True, minimum_priority: int | None = None
) -> NotificationPreference:
    factory = get_session_factory()
    async with factory() as session:
        pref = NotificationPreference(
            channel_id=channel_id,
            event_type=EVENT_TYPE_ALERT,
            enabled=enabled,
            minimum_priority=minimum_priority,
        )
        session.add(pref)
        await session.commit()
        await session.refresh(pref)
        return pref


async def _seed_alert(symbol: str = "NTFYSYM", *, trigger_value: float = 3000.0) -> int:
    sid = await seed_stock(symbol=symbol)
    return await _create_event_and_rule(sid, trigger_value=trigger_value)


async def _service() -> NotificationService:
    factory = get_session_factory()
    session = factory()
    await session.__aenter__()
    return NotificationService(session)


async def _cleanup(service: NotificationService) -> None:
    await service.session.commit()
    await service.session.close()


async def _create_event_and_rule(stock_id: int, *, trigger_value: float = 3000.0) -> int:
    factory = get_session_factory()
    async with factory() as session:
        rule = AlertRule(
            name="Breakout",
            rule_type="breakout",
            operator="true",
            stock_id=stock_id,
        )
        session.add(rule)
        await session.flush()
        event = AlertEvent(
            alert_rule_id=rule.id,
            stock_id=stock_id,
            trigger_value=trigger_value,
            message="Breakout triggered",
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event.id


async def _delivery_count() -> int:
    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import func

        return await session.scalar(select(func.count()).select_from(NotificationDelivery)) or 0


async def _deliveries_for(event_id: int) -> list[NotificationDelivery]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.scalars(
            select(NotificationDelivery).where(NotificationDelivery.alert_event_id == event_id)
        )
        return list(result.all())

# ---------------------------------------------------------------- delivery


@pytest.mark.asyncio
async def test_alert_event_creates_pending_and_sends() -> None:
    sid = await seed_stock(symbol="NTFY001")
    await _make_channel(name="C1")
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        summary = await service.deliver_alert(event_id)
        assert summary[STATUS_SENT] == 1
        deliveries = await _deliveries_for(event_id)
        assert len(deliveries) == 1
        assert deliveries[0].status == STATUS_SENT
        assert deliveries[0].external_message_id == "mock-test"
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_success_stores_external_message_id() -> None:
    sid = await seed_stock(symbol="NTFY002")
    await _make_channel(name="C2")
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        assert deliveries[0].external_message_id == "mock-test"
        assert deliveries[0].sent_at is not None
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_failure_marks_failed() -> None:
    sid = await seed_stock(symbol="NTFY003")
    await _make_channel(name="C3", config={"recipient": "x", "fail_with": "permanent"})
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        summary = await service.deliver_alert(event_id)
        assert summary["failed"] == 1
        deliveries = await _deliveries_for(event_id)
        assert deliveries[0].status == "failed"
        assert deliveries[0].error_message
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_retryable_failure_schedules_retry() -> None:
    sid = await seed_stock(symbol="NTFY004")
    await _make_channel(name="C4", config={"recipient": "x", "fail_with": "transient"})
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        assert deliveries[0].status == "retry_scheduled"
        assert deliveries[0].next_retry_at is not None
        assert deliveries[0].attempt_count == 1
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_permanent_failure_does_not_retry() -> None:
    sid = await seed_stock(symbol="NTFY005")
    await _make_channel(name="C5", config={"recipient": "x", "fail_with": "permanent"})
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        assert deliveries[0].status == "failed"
        assert deliveries[0].next_retry_at is None
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_duplicate_alert_channel_prevented() -> None:
    sid = await seed_stock(symbol="NTFY006")
    await _make_channel(name="C6")
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        await service.deliver_alert(event_id)
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        assert len(deliveries) == 1
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_disabled_channel_skipped() -> None:
    sid = await seed_stock(symbol="NTFY007")
    await _make_channel(name="C7", enabled=False)
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        summary = await service.deliver_alert(event_id)
        assert summary["skipped"] == 1
        assert summary[STATUS_SENT] == 0
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_disabled_preference_skipped() -> None:
    sid = await seed_stock(symbol="NTFY008")
    ch = await _make_channel(name="C8")
    await _make_preference(channel_id=ch.id, enabled=False)
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        summary = await service.deliver_alert(event_id)
        assert summary["skipped"] == 1
        assert summary[STATUS_SENT] == 0
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_minimum_priority_filtering() -> None:
    sid = await seed_stock(symbol="NTFY009")
    ch = await _make_channel(name="C9")
    await _make_preference(channel_id=ch.id, minimum_priority=90)
    event_id = await _create_event_and_rule(sid, trigger_value=10.0)
    service = await _service()
    try:
        summary = await service.deliver_alert(event_id)
        # priority derived from overall_score which is below 90
        assert summary["skipped"] == 1
        assert summary[STATUS_SENT] == 0
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_quiet_hours_skip() -> None:
    sid = await seed_stock(symbol="NTFY010")
    ch = await _make_channel(name="C10")
    now = datetime.now()
    hour = now.hour
    start = time(hour, 0)
    end = time((hour + 1) % 24, 0)
    factory = get_session_factory()
    async with factory() as session:
        pref = NotificationPreference(
            channel_id=ch.id,
            event_type=EVENT_TYPE_ALERT,
            enabled=True,
            quiet_hours_start=start,
            quiet_hours_end=end,
        )
        session.add(pref)
        await session.commit()

    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        summary = await service.deliver_alert(event_id)
        assert summary["skipped"] == 1
        assert summary[STATUS_SENT] == 0
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_max_attempts_enforced() -> None:
    sid = await seed_stock(symbol="NTFY011")
    await _make_channel(name="C11", config={"recipient": "x", "fail_with": "transient"})
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    service.retry = RetryEngine(max_attempts=1, retry_minutes=15, stale_minutes=30)
    try:
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        assert deliveries[0].status == "failed"
        assert deliveries[0].next_retry_at is None
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_sent_delivery_not_resent() -> None:
    sid = await seed_stock(symbol="NTFY012")
    await _make_channel(name="C12")
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        assert deliveries[0].status == STATUS_SENT
        await service.retry_delivery(deliveries[0].id)
        refreshed = await _deliveries_for(event_id)
        assert refreshed[0].status == STATUS_SENT
        assert refreshed[0].attempt_count == 1
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_manual_retry_of_failed_delivery() -> None:
    sid = await seed_stock(symbol="NTFY013")
    ch = await _make_channel(name="C13")
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        # force a failed delivery first by a temporary config
        async with get_session_factory()() as session:
            from app.repositories.notification import NotificationChannelRepository

            repo = NotificationChannelRepository(session)
            channel = await repo.get(ch.id)
            channel.config_json = {"recipient": "x", "fail_with": "permanent"}
            await session.commit()
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        assert deliveries[0].status == "failed"

        # now clear failure and retry
        async with get_session_factory()() as session:
            channel = await session.get(NotificationChannel, ch.id)
            channel.config_json = {"recipient": "x"}
            await session.commit()
        await service.retry_delivery(deliveries[0].id)
        refreshed = await _deliveries_for(event_id)
        assert refreshed[0].status == STATUS_SENT
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_recover_stale_sending() -> None:
    factory = get_session_factory()
    ch = await _make_channel(name="C14")
    async with factory() as session:
        delivery = NotificationDelivery(
            alert_event_id=None,
            channel_id=ch.id,
            notification_type=EVENT_TYPE_ALERT,
            status="sending",
            attempt_count=1,
            updated_at=datetime.now(UTC) - timedelta(hours=2),
        )
        session.add(delivery)
        await session.commit()
        delivery_id = delivery.id

    service = await _service()
    try:
        count = await service.recover_stale_deliveries()
        assert count == 1
        async with factory() as session:
            recovered = await session.get(NotificationDelivery, delivery_id)
            assert recovered.status == "retry_scheduled"
            assert recovered.next_retry_at is not None
    finally:
        await _cleanup(service)


@pytest.mark.asyncio
async def test_mark_read() -> None:
    sid = await seed_stock(symbol="NTFY014")
    await _make_channel(name="C15")
    event_id = await _create_event_and_rule(sid)
    service = await _service()
    try:
        await service.deliver_alert(event_id)
        deliveries = await _deliveries_for(event_id)
        await service.mark_read(deliveries[0].id)
        refreshed = await _deliveries_for(event_id)
        assert refreshed[0].status == "read"
    finally:
        await _cleanup(service)