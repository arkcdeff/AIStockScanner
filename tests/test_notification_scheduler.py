from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.db.session import get_session_factory
from app.models.notification import NotificationChannel, NotificationDelivery
from app.scheduler.eod_scheduler import (
    DAILY_SUMMARY_JOB_ID,
    NOTIFICATION_DELIVERY_JOB_ID,
    EODScheduler,
)
from app.services.eod_pipeline import EODPipelineService
from app.services.notification_jobs import (
    DeliveryJobResult,
    run_daily_summary_job,
    run_pending_delivery_job,
)
from tests.auth_helpers import auth_client


def _bhavcopy_bytes(trade_date: date) -> bytes:
    stamp = trade_date.strftime("%d-%b-%Y").upper()
    return (
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,"
        "TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        f"SCHEDX,EQ,100,110,95,108,108,99,100000,10800000,{stamp},500,INESCHEDX1\n"
    ).encode()


async def _seed_delivery_rows() -> tuple[int, int]:
    factory = get_session_factory()
    async with factory() as session:
        channel = NotificationChannel(
            name="Scheduler Mock",
            provider="mock",
            enabled=True,
            config_json={"recipient": "scheduler-user"},
        )
        session.add(channel)
        await session.flush()
        pending = NotificationDelivery(
            channel_id=channel.id,
            notification_type="alert_triggered",
            subject="Pending scheduler delivery",
            rendered_body="Pending body",
            status="pending",
            attempt_count=0,
        )
        stale = NotificationDelivery(
            channel_id=channel.id,
            notification_type="alert_triggered",
            subject="Stale scheduler delivery",
            rendered_body="Stale body",
            status="sending",
            attempt_count=1,
            updated_at=datetime.now(UTC) - timedelta(hours=2),
        )
        session.add_all([pending, stale])
        await session.commit()
        return pending.id, stale.id


def test_notification_scheduler_jobs_are_disabled_by_default() -> None:
    assert settings.notification_delivery_job_enabled is False
    assert settings.daily_summary_enabled is False
    scheduler = EODScheduler()
    status = scheduler.status()
    assert status["running"] is False
    assert status["alert_delivery_job_enabled"] is False
    assert status["daily_summary_job_enabled"] is False
    assert status["registered_job_ids"] == []


@pytest.mark.asyncio
async def test_alert_delivery_job_registers_once_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "notification_delivery_job_enabled", True)
    scheduler = EODScheduler()
    try:
        scheduler.start()
        scheduler.start()
        assert scheduler._scheduler is not None
        ids = [job.id for job in scheduler._scheduler.get_jobs()]
        assert ids == [NOTIFICATION_DELIVERY_JOB_ID]
        status = scheduler.status()
        assert status["jobs"]["alert_delivery"]["next_run_time"] is not None
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_daily_summary_job_registers_once_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "daily_summary_enabled", True)
    scheduler = EODScheduler()
    try:
        scheduler.start()
        scheduler.start()
        assert scheduler._scheduler is not None
        ids = [job.id for job in scheduler._scheduler.get_jobs()]
        assert ids == [DAILY_SUMMARY_JOB_ID]
        status = scheduler.status()
        assert status["jobs"]["daily_summary"]["next_run_time"] is not None
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_delivery_job_uses_fresh_session_recovers_stale_and_processes_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_id, stale_id = await _seed_delivery_rows()
    monkeypatch.setattr(settings, "notifications_enabled", True)

    import app.services.notification_jobs as jobs_module

    real_factory = get_session_factory
    calls = 0

    def _tracked_factory():
        nonlocal calls
        calls += 1
        return real_factory()

    monkeypatch.setattr(jobs_module, "get_session_factory", _tracked_factory)
    result = await run_pending_delivery_job()

    factory = get_session_factory()
    async with factory() as session:
        pending = await session.get(NotificationDelivery, pending_id)
        stale = await session.get(NotificationDelivery, stale_id)

    assert calls == 1
    assert result.status == "completed"
    assert result.recovered == 1
    assert result.dispatched == 1
    assert pending.status == "sent"
    assert stale.status == "retry_scheduled"


@pytest.mark.asyncio
async def test_manual_delivery_scheduler_api_processes_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_id, _stale_id = await _seed_delivery_rows()
    monkeypatch.setattr(settings, "notifications_enabled", True)
    async with auth_client(role="admin") as client:
        response = await client.post("/api/v1/notifications/scheduler/run-delivery")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["dispatched"] >= 1
    factory = get_session_factory()
    async with factory() as session:
        assert (await session.get(NotificationDelivery, pending_id)).status == "sent"


@pytest.mark.asyncio
async def test_scheduler_status_api_has_safe_job_state() -> None:
    async with auth_client(role="admin") as client:
        response = await client.get("/api/v1/notifications/scheduler/status")

    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    assert body["alert_delivery_job_enabled"] is False
    assert body["daily_summary_job_enabled"] is False
    assert body["registered_job_ids"] == []
    assert "alert_delivery" in body["jobs"]
    assert "daily_summary" in body["jobs"]


@pytest.mark.asyncio
async def test_daily_summary_scheduler_runs_requested_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = date(2026, 7, 22)
    captured: list[date] = []

    async def _fake_run(summary_date: date):
        captured.append(summary_date)
        return SimpleNamespace(
            run=SimpleNamespace(id=42, status="completed"),
            idempotent=False,
            delivery_ids=[],
        )

    monkeypatch.setattr("app.scheduler.eod_scheduler.run_daily_summary_job", _fake_run)
    result = await EODScheduler().run_daily_summary_now(requested)

    assert captured == [requested]
    assert result["summary_date"] == requested.isoformat()
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_daily_summary_job_helper_uses_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.notification_jobs as jobs_module

    real_factory = get_session_factory
    calls = 0

    def _tracked_factory():
        nonlocal calls
        calls += 1
        return real_factory()

    monkeypatch.setattr(jobs_module, "get_session_factory", _tracked_factory)
    execution = await run_daily_summary_job(date(2026, 7, 21))

    assert calls == 1
    assert execution.run.status == "completed"


@pytest.mark.asyncio
async def test_successful_pipeline_triggers_notification_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trade_date = date.today()
    called: list[date] = []

    async def _capture(value: date) -> DeliveryJobResult:
        called.append(value)
        return DeliveryJobResult(status="completed")

    monkeypatch.setattr("app.services.notification_jobs.deliver_alert_events_for_date", _capture)
    run = await EODPipelineService().run_from_file(
        bhavcopy=_bhavcopy_bytes(trade_date),
        bhavcopy_name="scheduler.csv",
    )

    assert run.status == "completed"
    assert called == [trade_date]


@pytest.mark.asyncio
async def test_notification_failure_does_not_fail_completed_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trade_date = date.today()

    async def _fail(_: date) -> DeliveryJobResult:
        raise RuntimeError("token=hidden notification failure")

    monkeypatch.setattr("app.services.notification_jobs.deliver_alert_events_for_date", _fail)
    run = await EODPipelineService().run_from_file(
        bhavcopy=_bhavcopy_bytes(trade_date),
        bhavcopy_name="scheduler.csv",
    )

    assert run.status == "completed"
    assert run.failed_stage is None


@pytest.mark.asyncio
async def test_failed_pipeline_does_not_trigger_notification_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _capture(_: date) -> DeliveryJobResult:
        nonlocal called
        called = True
        return DeliveryJobResult(status="completed")

    monkeypatch.setattr("app.services.notification_jobs.deliver_alert_events_for_date", _capture)
    run = await EODPipelineService().run_from_file(
        bhavcopy=b"invalid",
        bhavcopy_name="invalid.csv",
    )

    assert run.status == "failed"
    assert called is False
