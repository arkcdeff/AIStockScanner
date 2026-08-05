from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.main import app
from app.models.alert import AlertEvent, AlertRule
from app.models.notification import (
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.notifications.base import NotificationMessage, NotificationProvider, NotificationResult
from app.notifications.exceptions import NotificationPermanentError
from app.services.notification_service import EVENT_TYPE_ALERT
from tests.seed_helpers import seed_stock

API = "/api/v1/notifications"


@asynccontextmanager
async def _client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _create_channel(
    *,
    name: str = "Mock API",
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
            config_json=config or {"recipient": "api-user"},
        )
        session.add(channel)
        await session.commit()
        return channel.id


async def _create_alert(*, symbol: str = "NTFAPI") -> int:
    stock_id = await seed_stock(symbol=symbol, calculate_analytics=False)
    factory = get_session_factory()
    async with factory() as session:
        rule = AlertRule(
            name="API Breakout",
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
            trigger_value=123.45,
            message="Breakout from API test",
        )
        session.add(event)
        await session.commit()
        return event.id


async def _create_delivery(
    channel_id: int,
    *,
    status: str,
    alert_event_id: int | None = None,
    attempt_count: int = 0,
    error_message: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        delivery = NotificationDelivery(
            channel_id=channel_id,
            alert_event_id=alert_event_id,
            notification_type=EVENT_TYPE_ALERT,
            subject="Test alert",
            rendered_body="Test body",
            status=status,
            attempt_count=attempt_count,
            error_message=error_message,
            sent_at=datetime.now(UTC) if status in {"sent", "read"} else None,
        )
        if created_at is not None:
            delivery.created_at = created_at
        if updated_at is not None:
            delivery.updated_at = updated_at
        session.add(delivery)
        await session.commit()
        return delivery.id


@pytest.mark.asyncio
async def test_status_returns_counts_without_secrets() -> None:
    channel_id = await _create_channel(
        name="Telegram Secret",
        provider="telegram",
        config={
            "recipient": "123456789",
            "chat_id": "123456789",
            "bot_token": "secret-bot-token",
        },
    )
    await _create_delivery(channel_id, status="pending")
    await _create_delivery(
        channel_id,
        status="sending",
        updated_at=datetime.now(UTC) - timedelta(hours=2),
    )
    await _create_delivery(channel_id, status="retry_scheduled")
    await _create_delivery(channel_id, status="failed")
    await _create_delivery(channel_id, status="sent")

    async with _client() as client:
        response = await client.get(f"{API}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["pending_count"] == 1
    assert body["sending_count"] == 1
    assert body["retry_scheduled_count"] == 1
    assert body["failed_count"] == 1
    assert body["sent_today"] == 1
    assert body["unread_count"] == 1
    assert body["stale_sending_count"] == 1
    assert body["telegram_enabled"] is True
    assert body["provider_availability"]["telegram"] is True
    assert "secret-bot-token" not in response.text
    assert "123456789" not in response.text


@pytest.mark.asyncio
async def test_channels_list_create_update_mask_and_delete() -> None:
    async with _client() as client:
        empty = await client.get(f"{API}/channels")
        assert empty.status_code == 200
        assert empty.json() == []

        created = await client.post(
            f"{API}/channels",
            json={
                "name": "Telegram Primary",
                "provider": "telegram",
                "config_json": {
                    "bot_token": "top-secret-token",
                    "chat_id": "987654",
                    "html_mode": True,
                },
            },
        )
        assert created.status_code == 201
        channel_id = created.json()["id"]
        assert created.json()["config_json"]["bot_token"] == "********"
        assert created.json()["config_json"]["chat_id"] == "********"
        assert "top-secret-token" not in created.text

        listed = await client.get(f"{API}/channels", params={"provider": "telegram"})
        assert [item["id"] for item in listed.json()] == [channel_id]

        updated = await client.put(
            f"{API}/channels/{channel_id}",
            json={
                "name": "Telegram Renamed",
                "enabled": False,
                "config_json": {"bot_token": "********", "html_mode": False},
            },
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Telegram Renamed"
        assert updated.json()["enabled"] is False
        assert updated.json()["config_json"]["bot_token"] == "********"

        preference = await client.post(
            f"{API}/preferences",
            json={"channel_id": channel_id, "event_type": "alert_triggered"},
        )
        assert preference.status_code == 201
        await _create_delivery(channel_id, status="pending")

        deleted = await client.delete(f"{API}/channels/{channel_id}")
        assert deleted.status_code == 204
        assert (await client.get(f"{API}/channels")).json() == []

    factory = get_session_factory()
    async with factory() as session:
        assert await session.get(NotificationChannel, channel_id) is None
        assert await session.scalar(select(func.count()).select_from(NotificationPreference)) == 0
        assert await session.scalar(select(func.count()).select_from(NotificationDelivery)) == 0


@pytest.mark.asyncio
async def test_channel_duplicate_and_unsupported_provider_validation() -> None:
    async with _client() as client:
        first = await client.post(
            f"{API}/channels",
            json={"name": "Duplicate", "provider": "mock"},
        )
        duplicate = await client.post(
            f"{API}/channels",
            json={"name": "Duplicate", "provider": "mock"},
        )
        unsupported = await client.post(
            f"{API}/channels",
            json={"name": "Bad", "provider": "carrier-pigeon"},
        )
        unsafe_config = await client.post(
            f"{API}/channels",
            json={
                "name": "Unsafe URL",
                "provider": "telegram",
                "config_json": {
                    "base_url": "https://example.invalid",
                    "bot_token": "must-not-leak",
                },
            },
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert unsupported.status_code == 422
    assert unsafe_config.status_code == 422
    assert "must-not-leak" not in unsafe_config.text


@pytest.mark.asyncio
async def test_preferences_create_filter_update_and_delete() -> None:
    channel_id = await _create_channel()
    async with _client() as client:
        created = await client.post(
            f"{API}/preferences",
            json={
                "channel_id": channel_id,
                "event_type": "ALERT_TRIGGERED",
                "minimum_priority": 25,
                "quiet_hours_start": "22:00:00",
                "quiet_hours_end": "06:00:00",
            },
        )
        assert created.status_code == 201
        preference_id = created.json()["id"]
        assert created.json()["event_type"] == "alert_triggered"

        filtered = await client.get(
            f"{API}/preferences",
            params={"channel_id": channel_id, "event_type": "alert_triggered"},
        )
        assert [item["id"] for item in filtered.json()] == [preference_id]

        updated = await client.put(
            f"{API}/preferences/{preference_id}",
            json={"minimum_priority": 80, "enabled": False},
        )
        assert updated.status_code == 200
        assert updated.json()["minimum_priority"] == 80
        assert updated.json()["enabled"] is False

        deleted = await client.delete(f"{API}/preferences/{preference_id}")
        assert deleted.status_code == 204
        assert (await client.get(f"{API}/preferences")).json() == []


@pytest.mark.asyncio
async def test_preference_duplicate_and_invalid_quiet_hours() -> None:
    channel_id = await _create_channel()
    async with _client() as client:
        first = await client.post(
            f"{API}/preferences",
            json={"channel_id": channel_id, "event_type": "alert_triggered"},
        )
        duplicate = await client.post(
            f"{API}/preferences",
            json={"channel_id": channel_id, "event_type": "alert_triggered"},
        )
        invalid = await client.post(
            f"{API}/preferences",
            json={
                "channel_id": channel_id,
                "event_type": "daily_summary",
                "quiet_hours_start": "22:00:00",
            },
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_deliver_existing_alert_and_history_detail() -> None:
    await _create_channel()
    event_id = await _create_alert()
    async with _client() as client:
        delivered = await client.post(f"{API}/deliver-alert/{event_id}")
        assert delivered.status_code == 200
        body = delivered.json()
        assert body["alert_event_id"] == event_id
        assert body["channels_evaluated"] == 1
        assert body["sent"] == 1
        assert body["failed"] == 0
        assert len(body["delivery_ids"]) == 1

        detail = await client.get(f"{API}/history/{body['delivery_ids'][0]}")
        assert detail.status_code == 200
        assert detail.json()["provider"] == "mock"
        assert detail.json()["alert"]["id"] == event_id
        assert detail.json()["stock"]["symbol"] == "NTFAPI"
        assert detail.json()["rendered_body"]


@pytest.mark.asyncio
async def test_deliver_alert_not_found() -> None:
    async with _client() as client:
        response = await client.post(f"{API}/deliver-alert/999999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_deliver_pending_and_batch_validation() -> None:
    channel_id = await _create_channel()
    delivery_id = await _create_delivery(channel_id, status="pending")
    async with _client() as client:
        delivered = await client.post(f"{API}/deliver-pending", json={"batch_size": 5})
        too_large = await client.post(f"{API}/deliver-pending", params={"batch_size": 101})

    assert delivered.status_code == 200
    assert delivered.json() == {"batch_size": 5, "dispatched": 1}
    assert too_large.status_code == 422
    factory = get_session_factory()
    async with factory() as session:
        assert (await session.get(NotificationDelivery, delivery_id)).status == "sent"


@pytest.mark.asyncio
async def test_retry_failed_delivery_and_reject_sent_retry() -> None:
    channel_id = await _create_channel()
    failed_id = await _create_delivery(channel_id, status="failed")
    sent_id = await _create_delivery(channel_id, status="sent", attempt_count=1)
    async with _client() as client:
        retried = await client.post(f"{API}/history/{failed_id}/retry")
        rejected = await client.post(f"{API}/history/{sent_id}/retry")
        missing = await client.post(f"{API}/history/999999/retry")

    assert retried.status_code == 200
    assert retried.json()["status"] == "sent"
    assert retried.json()["attempt_count"] == 1
    assert rejected.status_code == 409
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_mark_read() -> None:
    channel_id = await _create_channel()
    delivery_id = await _create_delivery(channel_id, status="sent")
    async with _client() as client:
        response = await client.post(f"{API}/history/{delivery_id}/mark-read")
        missing = await client.post(f"{API}/history/999999/mark-read")
    assert response.status_code == 200
    assert response.json() == {"delivery_id": delivery_id, "status": "read", "read": True}
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_history_filters_pagination_and_validation() -> None:
    channel_id = await _create_channel()
    event_id = await _create_alert(symbol="HISTORYX")
    first_id = await _create_delivery(channel_id, status="failed", alert_event_id=event_id)
    second_id = await _create_delivery(channel_id, status="sent", alert_event_id=event_id)
    async with _client() as client:
        filtered = await client.get(
            f"{API}/history",
            params={"provider": "mock", "status": "sent", "stock_symbol": "historyx"},
        )
        page_one = await client.get(f"{API}/history", params={"limit": 1, "offset": 0})
        page_two = await client.get(f"{API}/history", params={"limit": 1, "offset": 1})
        bad_status = await client.get(f"{API}/history", params={"status": "unknown"})
        bad_dates = await client.get(
            f"{API}/history",
            params={"date_from": "2026-08-05", "date_to": "2026-08-01"},
        )

    assert [item["id"] for item in filtered.json()] == [second_id]
    assert page_one.json()[0]["id"] == second_id
    assert page_two.json()[0]["id"] == first_id
    assert bad_status.status_code == 422
    assert bad_dates.status_code == 422


@pytest.mark.asyncio
async def test_history_detail_sanitizes_error_and_404() -> None:
    channel_id = await _create_channel(config={"recipient": "private-chat-123"})
    delivery_id = await _create_delivery(
        channel_id,
        status="failed",
        error_message="provider failed for chat_id=private-chat-123",
    )
    async with _client() as client:
        response = await client.get(f"{API}/history/{delivery_id}")
        missing = await client.get(f"{API}/history/999999")
    assert response.status_code == 200
    assert "private-chat-123" not in response.text
    assert "********" in response.json()["sanitized_error"]
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_provider_mock_success_does_not_create_alert() -> None:
    async with _client() as client:
        response = await client.post(f"{API}/test/mock")
    assert response.status_code == 200
    assert response.json()["provider"] == "mock"
    assert response.json()["success"] is True

    factory = get_session_factory()
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(AlertEvent))
    assert count == 0


@pytest.mark.asyncio
async def test_provider_unknown_and_unconfigured_fail_safely() -> None:
    async with _client() as client:
        unknown = await client.post(f"{API}/test/carrier-pigeon")
        telegram = await client.post(f"{API}/test/telegram")
        email = await client.post(f"{API}/test/email", json={"recipient": "test@example.com"})
    assert unknown.status_code == 422
    assert telegram.status_code == 422
    assert email.status_code == 422
    assert "traceback" not in (telegram.text + email.text).lower()


class _LeakyProvider(NotificationProvider):
    def provider_name(self) -> str:
        return "mock"

    async def send(self, message: NotificationMessage) -> NotificationResult:
        raise NotificationPermanentError(f"password=super-secret recipient={message.recipient}")

    async def health(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_provider_error_response_is_sanitized(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.notifications.get_provider",
        lambda _name, config=None: _LeakyProvider(),
    )
    async with _client() as client:
        response = await client.post(
            f"{API}/test/mock",
            json={"recipient": "private-recipient", "config_json": {"recipient": "stored"}},
        )
    assert response.status_code == 502
    assert "super-secret" not in response.text
    assert "private-recipient" not in response.text
    assert "stored" not in response.text
    assert "********" in response.text
