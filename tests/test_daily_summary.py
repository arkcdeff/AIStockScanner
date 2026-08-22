from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.ai.cache import clear_memory_cache
from app.ai.explanation_service import AIExplanationService
from app.core.config import settings
from app.db.session import get_session_factory
from app.models.eod_pipeline_run import EODPipelineRun
from app.models.notification import (
    DailySummaryRun,
    NotificationChannel,
    NotificationDelivery,
    NotificationPreference,
)
from app.notifications.renderer import NotificationRenderer
from app.services.daily_summary import DailySummaryService
from tests.auth_helpers import admin_client
from tests.seed_helpers import seed_stock


@pytest.fixture(autouse=True)
def _clear_summary_ai_cache() -> None:
    clear_memory_cache()
    yield
    clear_memory_cache()


@asynccontextmanager
async def _client():
    async with admin_client(browser=True) as client:
        yield client


async def _seed_pipeline(summary_date: date) -> None:
    factory = get_session_factory()
    async with factory() as session:
        session.add(
            EODPipelineRun(
                source="file",
                trade_date=summary_date,
                status="completed",
                current_stage="completed",
                imported_rows=100,
                updated_rows=7,
                rejected_rows=2,
                scanner_matches=11,
                alerts_triggered=3,
                started_at=datetime.now(UTC) - timedelta(minutes=5),
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _seed_mock_summary_channel() -> int:
    factory = get_session_factory()
    async with factory() as session:
        channel = NotificationChannel(
            name="Daily Summary Mock",
            provider="mock",
            enabled=True,
            config_json={"recipient": "summary-user"},
        )
        session.add(channel)
        await session.flush()
        session.add(
            NotificationPreference(
                channel_id=channel.id,
                event_type="daily_summary",
                enabled=True,
            )
        )
        await session.commit()
        return channel.id


@pytest.mark.asyncio
async def test_daily_summary_generation_is_deterministic_and_read_only() -> None:
    summary_date = date(2026, 7, 30)
    await _seed_pipeline(summary_date)
    factory = get_session_factory()
    async with factory() as session:
        service = DailySummaryService(session)
        first = await service.generate(summary_date)
        second = await service.generate(summary_date)

    assert first == second
    assert first["pipeline_status"] == "completed"
    assert first["imported_rows"] == 100
    assert first["updated_rows"] == 7
    assert first["rejected_rows"] == 2
    assert first["scanner_matches"] == 11


@pytest.mark.asyncio
async def test_daily_summary_fallback_handles_missing_optional_data() -> None:
    summary_date = date(2026, 7, 29)
    factory = get_session_factory()
    async with factory() as session:
        context = await DailySummaryService(session).generate(summary_date)

    assert context["ai_summary_cached"] is False
    assert "pipeline not_run" in context["market_summary_text"]
    assert context["portfolio_value"] == 0
    assert context["portfolio_daily_pl"] == 0
    assert context["watchlist_changes"] == 0
    assert context["top_rated"] == []
    assert context["breakouts"] == []


@pytest.mark.asyncio
async def test_daily_summary_uses_cached_ai_market_summary_without_new_call() -> None:
    stock_id = await seed_stock(symbol="SUMAI", calculate_analytics=True)
    del stock_id
    summary_date = date.today() - timedelta(days=1)
    await _seed_pipeline(summary_date)
    factory = get_session_factory()
    async with factory() as session:
        cached = await AIExplanationService(session).explain_market_summary()
        context = await DailySummaryService(session).generate(summary_date)

    assert context["ai_summary_cached"] is True
    assert context["market_summary_text"] == cached.summary


@pytest.mark.asyncio
async def test_daily_summary_is_idempotent_and_creates_delivery_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "notifications_enabled", True)
    summary_date = date(2026, 7, 28)
    channel_id = await _seed_mock_summary_channel()
    factory = get_session_factory()
    async with factory() as session:
        first = await DailySummaryService(session).run(summary_date)
    async with factory() as session:
        second = await DailySummaryService(session).run(summary_date)
        run_count = await session.scalar(select(func.count()).select_from(DailySummaryRun))
        deliveries = list(
            await session.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.channel_id == channel_id,
                    NotificationDelivery.notification_type == "daily_summary",
                )
            )
        )

    assert first.run.status == "completed"
    assert first.idempotent is False
    assert second.run.id == first.run.id
    assert second.idempotent is True
    assert run_count == 1
    assert len(deliveries) == 1
    assert deliveries[0].status == "sent"
    assert deliveries[0].subject == f"Daily Summary - {summary_date}"


@pytest.mark.asyncio
async def test_failed_daily_summary_is_persisted_and_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_date = date(2026, 7, 27)
    factory = get_session_factory()
    async with factory() as session:
        service = DailySummaryService(session)

        async def _fail(_: date) -> dict:
            raise RuntimeError("token=do-not-store summary source failed")

        monkeypatch.setattr(service, "generate", _fail)
        failed = await service.run(summary_date)

    assert failed.run.status == "failed"
    assert failed.run.finished_at is not None
    assert "do-not-store" not in (failed.run.error_message or "")

    async with factory() as session:
        retried = await DailySummaryService(session).run(summary_date)

    assert retried.run.id == failed.run.id
    assert retried.run.status == "completed"
    assert retried.idempotent is False


@pytest.mark.asyncio
async def test_abandoned_daily_summary_runs_are_recovered() -> None:
    factory = get_session_factory()
    async with factory() as session:
        run = DailySummaryRun(
            summary_date=date(2026, 7, 26),
            status="running",
            started_at=datetime.now(UTC) - timedelta(hours=2),
        )
        session.add(run)
        await session.commit()
        recovered = await DailySummaryService(session).recover_abandoned_runs()
        await session.refresh(run)

    assert recovered == 1
    assert run.status == "failed"
    assert "abandoned" in (run.error_message or "").lower()


def test_daily_summary_templates_render_telegram_email_and_plain_text() -> None:
    context = {
        "summary_date": "2026-07-25",
        "pipeline_status": "completed",
        "failed_pipeline_stage": None,
        "imported_rows": 100,
        "updated_rows": 5,
        "rejected_rows": 1,
        "scanner_matches": 12,
        "alerts_count": 3,
        "top_rated_count": 1,
        "top_rated": [{"symbol": "<b>SUM</b>", "score": 88}],
        "breakouts": [{"symbol": "BREAK", "score": 91}],
        "alerts": [{"symbol": "SUM", "alert_name": '<script>alert("x")</script>'}],
        "watchlist_changes": 2,
        "portfolio_value": 100000,
        "portfolio_daily_pl": 1250,
        "market_summary_text": '<img src=x onerror="alert(1)">',
    }
    renderer = NotificationRenderer()
    telegram = renderer.render(
        notification_type="daily_summary", provider="telegram", context=context
    )
    email = renderer.render(notification_type="daily_summary", provider="email", context=context)

    assert "DAILY SUMMARY - 2026-07-25" in telegram["text"]
    assert "Top rated:" in telegram["text"]
    assert "DAILY SUMMARY - 2026-07-25" in email["text"]
    assert "Top rated stocks" in email["text"]
    assert "<table" in email["html"]
    assert "&lt;script&gt;" in email["html"]
    assert "<script>" not in email["html"]
    assert "<img" not in email["html"]


@pytest.mark.asyncio
async def test_daily_summary_api_history_detail_and_idempotency() -> None:
    summary_date = "2026-07-24"
    async with _client() as client:
        first = await client.post(
            "/api/v1/notifications/daily-summary/run",
            json={"summary_date": summary_date},
        )
        second = await client.post(
            "/api/v1/notifications/daily-summary/run",
            json={"summary_date": summary_date},
        )
        history = await client.get(
            "/api/v1/notifications/daily-summary/history",
            params={"status": "completed", "date_from": summary_date, "date_to": summary_date},
        )
        detail = await client.get(
            f"/api/v1/notifications/daily-summary/{first.json()['run']['id']}"
        )

    assert first.status_code == 200
    assert first.json()["run"]["status"] == "completed"
    assert first.json()["idempotent"] is False
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert second.json()["run"]["id"] == first.json()["run"]["id"]
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert detail.status_code == 200
    assert detail.json()["summary_date"] == summary_date


@pytest.mark.asyncio
async def test_daily_summary_ui_and_dashboard_widgets_render() -> None:
    summary_date = "2026-07-23"
    async with _client() as client:
        notification_page = await client.get("/notifications")
        settings_page = await client.get("/notifications/settings")
        summary_panel = await client.get(
            "/notifications/summary-scheduler", params={"show_run_action": "true"}
        )
        manual = await client.post(
            "/notifications/daily-summary/run",
            data={"summary_date": summary_date},
        )
        dashboard = await client.get("/dashboard")

    assert notification_page.status_code == 200
    assert settings_page.status_code == 200
    assert "Daily summary and scheduler" in settings_page.text
    assert "Alert delivery scheduler" in settings_page.text
    assert "environment-driven and read-only" in settings_page.text
    assert summary_panel.status_code == 200
    assert "Run summary" in summary_panel.text
    assert manual.status_code == 200
    assert "Daily summary completed" in manual.text
    assert dashboard.status_code == 200
    assert "Daily summary: Completed" in dashboard.text
    assert summary_date in dashboard.text
