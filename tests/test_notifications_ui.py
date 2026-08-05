from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.session import get_session_factory
from app.main import app
from app.models.alert import AlertEvent, AlertRule
from app.models.notification import (
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.services.notification_service import EVENT_TYPE_ALERT
from tests.seed_helpers import seed_stock


@asynccontextmanager
async def _client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _create_channel(
    *,
    name: str = "UI Mock",
    provider: str = "mock",
    enabled: bool = True,
    config: dict | None = None,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        channel = NotificationChannel(
            name=name,
            provider=provider,
            enabled=enabled,
            config_json=config or {"recipient": "ui-user"},
        )
        session.add(channel)
        await session.commit()
        return channel.id


async def _create_delivery(
    channel_id: int,
    *,
    status: str = "sent",
    subject: str = "UI alert",
    body: str = "UI body",
    alert_event_id: int | None = None,
    created_at: datetime | None = None,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        delivery = NotificationDelivery(
            channel_id=channel_id,
            alert_event_id=alert_event_id,
            notification_type=EVENT_TYPE_ALERT,
            subject=subject,
            rendered_body=body,
            status=status,
            attempt_count=1 if status not in {"pending", "retry_scheduled"} else 0,
            error_message="temporary failure" if status == "failed" else None,
            sent_at=datetime.now(UTC) if status in {"sent", "read"} else None,
        )
        if created_at is not None:
            delivery.created_at = created_at
        session.add(delivery)
        await session.commit()
        return delivery.id


async def _create_stock_alert(symbol: str) -> int:
    stock_id = await seed_stock(symbol=symbol, calculate_analytics=False)
    factory = get_session_factory()
    async with factory() as session:
        rule = AlertRule(
            name=f"{symbol} breakout",
            stock_id=stock_id,
            rule_type="breakout",
            operator="true",
        )
        session.add(rule)
        await session.flush()
        event = AlertEvent(
            alert_rule_id=rule.id,
            stock_id=stock_id,
            trade_date=date.today(),
            trigger_value=101.5,
            message=f"{symbol} crossed its breakout level",
        )
        session.add(event)
        await session.commit()
        return event.id


@pytest.mark.asyncio
async def test_notification_pages_and_empty_history_render_html() -> None:
    async with _client() as client:
        responses = [
            await client.get("/notifications"),
            await client.get("/notifications/settings"),
            await client.get("/notifications/history"),
            await client.get("/notifications/history/table"),
        ]

    assert all(response.status_code == 200 for response in responses)
    assert all("text/html" in response.headers["content-type"] for response in responses)
    assert "Notification history" in responses[2].text
    assert "No deliveries match these filters" in responses[3].text


@pytest.mark.asyncio
async def test_notification_status_cards_render_aggregate_counts() -> None:
    channel_id = await _create_channel()
    for status in ("pending", "sending", "retry_scheduled", "failed", "sent"):
        await _create_delivery(channel_id, status=status, subject=f"{status} item")

    async with _client() as client:
        response = await client.get("/notifications/status")

    assert response.status_code == 200
    assert "Notifications enabled" in response.text
    assert 'data-notification-count="pending">1<' in response.text
    assert 'data-notification-count="retry-scheduled">1<' in response.text
    assert 'data-notification-count="failed">1<' in response.text
    assert 'data-notification-count="unread">1<' in response.text


@pytest.mark.asyncio
async def test_channel_create_update_test_and_delete_actions() -> None:
    async with _client() as client:
        created = await client.post(
            "/notifications/channels/create",
            data={
                "name": "Browser Mock",
                "provider": "mock",
                "enabled": "on",
                "recipient": "browser-user",
            },
        )
        assert created.status_code == 200
        assert "Notification channel created" in created.text

        factory = get_session_factory()
        async with factory() as session:
            channel_id = await session.scalar(
                select(NotificationChannel.id).where(NotificationChannel.name == "Browser Mock")
            )

        updated = await client.post(
            f"/notifications/channels/{channel_id}/update",
            data={
                "name": "Browser Mock Updated",
                "provider": "mock",
                "enabled": "on",
                "recipient": "browser-user",
            },
        )
        tested = await client.post(f"/notifications/channels/{channel_id}/test")
        deleted = await client.post(f"/notifications/channels/{channel_id}/delete")

    assert "Notification channel updated" in updated.text
    assert "Browser Mock Updated test succeeded" in tested.text
    assert "Notification channel deleted" in deleted.text
    assert "Browser Mock Updated" not in deleted.text


@pytest.mark.asyncio
async def test_channel_duplicate_conflict_is_friendly() -> None:
    await _create_channel(name="Existing Channel")
    async with _client() as client:
        response = await client.post(
            "/notifications/channels/create",
            data={"name": "Existing Channel", "provider": "mock", "enabled": "on"},
        )

    assert response.status_code == 200
    assert "already exists" in response.text
    assert "alert-danger" in response.text


@pytest.mark.asyncio
async def test_channel_secrets_are_never_rendered() -> None:
    await _create_channel(
        name="Telegram Secret",
        provider="telegram",
        config={
            "bot_token": "123456:full-secret-token",
            "chat_id": "99887766",
            "timeout": 15,
        },
    )
    await _create_channel(
        name="Email Secret",
        provider="email",
        config={
            "host": "smtp.example.test",
            "username": "mailer",
            "password": "full-smtp-password",
            "from": "sender@example.test",
            "recipient": "private@example.test",
        },
    )

    async with _client() as client:
        response = await client.get("/notifications/settings")

    assert response.status_code == 200
    for secret in (
        "123456:full-secret-token",
        "99887766",
        "full-smtp-password",
        "private@example.test",
    ):
        assert secret not in response.text
    assert "********" in response.text
    assert 'value="full-smtp-password"' not in response.text


@pytest.mark.asyncio
async def test_preference_create_update_validation_and_delete_actions() -> None:
    channel_id = await _create_channel()
    async with _client() as client:
        created = await client.post(
            "/notifications/preferences/create",
            data={
                "channel_id": str(channel_id),
                "event_type": EVENT_TYPE_ALERT,
                "enabled": "on",
                "minimum_priority": "20",
                "quiet_hours_start": "22:00",
                "quiet_hours_end": "06:00",
            },
        )
        assert "Notification preference created" in created.text

        factory = get_session_factory()
        async with factory() as session:
            preference_id = await session.scalar(select(NotificationPreference.id))

        updated = await client.post(
            f"/notifications/preferences/{preference_id}/update",
            data={
                "channel_id": str(channel_id),
                "event_type": EVENT_TYPE_ALERT,
                "enabled": "on",
                "minimum_priority": "50",
                "quiet_hours_start": "23:00",
                "quiet_hours_end": "05:00",
            },
        )
        invalid = await client.post(
            f"/notifications/preferences/{preference_id}/update",
            data={
                "channel_id": str(channel_id),
                "event_type": EVENT_TYPE_ALERT,
                "enabled": "on",
                "quiet_hours_start": "08:00",
                "quiet_hours_end": "08:00",
            },
        )
        deleted = await client.post(f"/notifications/preferences/{preference_id}/delete")

    assert "Notification preference updated" in updated.text
    assert ">50<" in updated.text
    assert "quiet hours start and end must be different" in invalid.text
    assert "Notification preference deleted" in deleted.text


@pytest.mark.asyncio
async def test_history_filters_and_server_side_pagination() -> None:
    mock_id = await _create_channel(name="Mock History")
    email_id = await _create_channel(
        name="Email History",
        provider="email",
        config={"host": "smtp.example.test", "from": "sender@example.test"},
    )
    now = datetime.now(UTC)
    for index in range(12):
        await _create_delivery(
            mock_id,
            status="failed",
            subject=f"Failure {index:02d}",
            created_at=now + timedelta(seconds=index),
        )
    await _create_delivery(email_id, status="sent", subject="Email sent")

    async with _client() as client:
        first_page = await client.get(
            "/notifications/history/table?status=failed&provider=mock&page_size=5&page=1"
        )
        second_page = await client.get(
            "/notifications/history/table?status=failed&provider=mock&page_size=5&page=2"
        )

    assert "12 result(s)" in first_page.text
    assert "Page 1 of 3" in first_page.text
    assert "Failure 11" in first_page.text
    assert "Email sent" not in first_page.text
    assert "Page 2 of 3" in second_page.text
    assert "Failure 06" in second_page.text


@pytest.mark.asyncio
async def test_retry_and_mark_read_refresh_history_rows() -> None:
    channel_id = await _create_channel(config={"recipient": "retry-user"})
    failed_id = await _create_delivery(channel_id, status="failed", subject="Retry me")
    sent_id = await _create_delivery(channel_id, status="sent", subject="Read me")

    async with _client() as client:
        retried = await client.post(
            f"/notifications/history/{failed_id}/retry",
            data={"view": "history", "page": "1", "page_size": "25"},
        )
        marked = await client.post(
            f"/notifications/history/{sent_id}/mark-read",
            data={"view": "history", "page": "1", "page_size": "25"},
        )

    assert "Retry completed with status: sent" in retried.text
    assert "Notification marked as read" in marked.text
    factory = get_session_factory()
    async with factory() as session:
        assert (await session.get(NotificationDelivery, failed_id)).status == "sent"
        assert (await session.get(NotificationDelivery, sent_id)).status == "read"


@pytest.mark.asyncio
async def test_delivery_detail_escapes_stored_message_body() -> None:
    channel_id = await _create_channel()
    delivery_id = await _create_delivery(
        channel_id,
        body='<script>alert("unsafe")</script><b>not trusted html</b>',
    )

    async with _client() as client:
        response = await client.get(f"/notifications/history/{delivery_id}/detail")

    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert "&lt;b&gt;not trusted html&lt;/b&gt;" in response.text
    assert '<script>alert("unsafe")</script>' not in response.text


@pytest.mark.asyncio
async def test_dashboard_notification_widget_limits_recent_rows_to_five() -> None:
    await _create_stock_alert("DASHUI")
    channel_id = await _create_channel()
    now = datetime.now(UTC)
    for index in range(6):
        await _create_delivery(
            channel_id,
            subject=f"Dashboard item {index}",
            created_at=now + timedelta(seconds=index),
        )

    async with _client() as client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert "Open notifications" in response.text
    assert "Dashboard item 5" in response.text
    assert "Dashboard item 0" not in response.text
    assert response.text.count("Dashboard item") == 6  # latest label plus five table rows


@pytest.mark.asyncio
async def test_stock_detail_recent_notifications_and_empty_state() -> None:
    alert_event_id = await _create_stock_alert("UISTOCK")
    channel_id = await _create_channel()
    await _create_delivery(
        channel_id,
        alert_event_id=alert_event_id,
        subject="UISTOCK delivery",
    )
    await seed_stock(symbol="UIEMPTY", calculate_analytics=False)

    async with _client() as client:
        populated = await client.get("/stocks/UISTOCK")
        empty = await client.get("/stocks/UIEMPTY")
        unknown = await client.get("/stocks/UIMISSING")

    assert populated.status_code == 200
    assert "Recent notifications" in populated.text
    assert "UISTOCK delivery" in populated.text
    assert "UISTOCK breakout" in populated.text
    assert empty.status_code == 200
    assert "No notifications are related to this stock yet" in empty.text
    assert unknown.status_code == 404
