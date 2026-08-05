from datetime import date, time

import pytest
from sqlalchemy import text

from app.db.session import get_session_factory
from app.models.notification import (
    DailySummaryRun,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)


async def _session():
    factory = get_session_factory()
    session = factory()
    await session.__aenter__()
    return session


# --------------------------------------------------------------- inserts


@pytest.mark.asyncio
async def test_channel_insert_and_read() -> None:
    session = await _session()
    try:
        channel = NotificationChannel(
            name="Telegram Alerts",
            provider="telegram",
            enabled=True,
            config_json={"chat_id": "-100123"},
        )
        session.add(channel)
        await session.flush()

        assert channel.id is not None
        assert channel.created_at is not None
        assert channel.updated_at is not None

        fetched = await session.get(NotificationChannel, channel.id)
        assert fetched is not None
        assert fetched.name == "Telegram Alerts"
        assert fetched.config_json == {"chat_id": "-100123"}
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_preference_insert_with_channel() -> None:
    session = await _session()
    try:
        channel = NotificationChannel(name="Email", provider="email", enabled=True)
        session.add(channel)
        await session.flush()

        pref = NotificationPreference(
            channel_id=channel.id,
            event_type="alert_triggered",
            enabled=True,
            minimum_priority=5,
            quiet_hours_start=time(22, 0),
            quiet_hours_end=time(6, 0),
        )
        session.add(pref)
        await session.flush()
        assert pref.id is not None
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_delivery_insert() -> None:
    session = await _session()
    try:
        channel = NotificationChannel(name="In App", provider="in_app", enabled=True)
        session.add(channel)
        await session.flush()

        delivery = NotificationDelivery(
            channel_id=channel.id,
            notification_type="alert",
            subject="RELIANCE hit target",
            rendered_body="<p>RELIANCE exceeded.</p>",
            status="pending",
            attempt_count=0,
        )
        session.add(delivery)
        await session.flush()

        assert delivery.id is not None
        assert delivery.status == "pending"
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_daily_summary_run_insert() -> None:
    session = await _session()
    try:
        run = DailySummaryRun(
            summary_date=date.today(),
            status="completed",
            alerts_count=3,
            scanner_matches=12,
            top_rated_count=5,
            watchlist_changes=2,
            portfolio_value=123456.7890,
            started_at=None,
            finished_at=None,
        )
        session.add(run)
        await session.flush()
        assert run.id is not None
    finally:
        await session.rollback()
        await session.close()


# ------------------------------------------------------------ relationships


@pytest.mark.asyncio
async def test_channel_preferences_relationship() -> None:
    session = await _session()
    try:
        channel = NotificationChannel(name="All Channels", provider="email", enabled=True)
        channel.preferences = [
            NotificationPreference(event_type="alert_triggered", enabled=True),
            NotificationPreference(event_type="daily_summary", enabled=True),
        ]
        session.add(channel)
        await session.flush()

        assert len(channel.preferences) == 2
        for pref in channel.preferences:
            assert pref.channel is channel
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_channel_deliveries_relationship() -> None:
    session = await _session()
    try:
        channel = NotificationChannel(name="Delivery Channel", provider="telegram", enabled=True)
        delivery = NotificationDelivery(
            notification_type="alert", subject="subject", status="sent"
        )
        channel.deliveries.append(delivery)
        session.add(channel)
        await session.flush()

        assert len(channel.deliveries) == 1
        assert delivery.channel is channel
    finally:
        await session.rollback()
        await session.close()


# -------------------------------------------------------- unique constraints


@pytest.mark.asyncio
async def test_unique_channel_name() -> None:
    import sqlalchemy as sa

    session = await _session()
    try:
        session.add_all(
            [
                NotificationChannel(name="Ch", provider="email", enabled=True),
                NotificationChannel(name="Ch", provider="telegram", enabled=True),
            ]
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_unique_preference_channel_event() -> None:
    import sqlalchemy as sa

    session = await _session()
    try:
        channel = NotificationChannel(name="Pref Channel", provider="email", enabled=True)
        session.add(channel)
        await session.flush()
        session.add_all(
            [
                NotificationPreference(channel_id=channel.id, event_type="alert", enabled=True),
                NotificationPreference(channel_id=channel.id, event_type="alert", enabled=True),
            ]
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_unique_daily_summary_date() -> None:
    import sqlalchemy as sa

    session = await _session()
    try:
        session.add_all(
            [
                DailySummaryRun(summary_date=date(2026, 1, 1), status="completed"),
                DailySummaryRun(summary_date=date(2026, 1, 1), status="running"),
            ]
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()
    finally:
        await session.rollback()
        await session.close()


# -------------------------------------------------------------- cascade


@pytest.mark.asyncio
async def test_channel_delete_cascades_preferences() -> None:
    session = await _session()
    try:
        channel = NotificationChannel(name="Cascade Pref", provider="email", enabled=True)
        channel.preferences = [
            NotificationPreference(event_type="alert_triggered", enabled=True),
        ]
        session.add(channel)
        await session.flush()
        channel_id = channel.id
        pref_id = channel.preferences[0].id

        await session.delete(channel)
        await session.flush()

        assert await session.get(NotificationChannel, channel_id) is None
        assert await session.get(NotificationPreference, pref_id) is None
    finally:
        await session.rollback()
        await session.close()


@pytest.mark.asyncio
async def test_channel_delete_cascades_deliveries() -> None:
    session = await _session()
    try:
        channel = NotificationChannel(name="Cascade Delivery", provider="telegram", enabled=True)
        delivery = NotificationDelivery(
            notification_type="alert", subject="s", status="pending"
        )
        channel.deliveries.append(delivery)
        session.add(channel)
        await session.flush()
        channel_id = channel.id
        delivery_id = delivery.id

        await session.delete(channel)
        await session.flush()

        assert await session.get(NotificationChannel, channel_id) is None
        assert await session.get(NotificationDelivery, delivery_id) is None
    finally:
        await session.rollback()
        await session.close()


# ------------------------------------------------------------- migration


@pytest.mark.asyncio
async def test_notification_tables_exist() -> None:
    session = await _session()
    try:
        result = await session.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN "
                "('notification_channels', 'notification_preferences', "
                "'notification_deliveries', 'daily_summary_runs')"
            )
        )
        assert result == 4
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_foreign_key_cascade_at_db_level() -> None:
    session = await _session()
    try:
        total = await session.scalar(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE contype = 'f' AND conname IN "
                "('fk_notification_preferences_channel_id_notification_channels', "
                "'fk_notification_deliveries_channel_id_notification_channels')"
            )
        )
        assert total >= 2
    finally:
        await session.close()