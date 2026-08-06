from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.config import settings
from app.db.session import get_session_factory
from app.models.eod_pipeline_run import EODPipelineRun
from app.scheduler.eod_scheduler import EODScheduler
from app.services.eod_pipeline import EODPipelineService, PipelineOverlapError
from tests.auth_helpers import admin_client


def _bhavcopy_bytes(trade_date: date, rows: int = 1) -> bytes:
    lines = [
        "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN"
    ]
    day = trade_date
    for i in range(rows):
        stamp = day.strftime("%d-%b-%Y").upper()
        base = 1500 + i * 5
        lines.append(
            f"RELIANCE,EQ,{base},{base + 30},{base - 10},{base + 20},{base + 19},{base},"
            f"100000,152000000,{stamp},5000,INE002A01018"
        )
        day -= timedelta(days=1)
    return ("\n".join(lines) + "\n").encode()


def _invalid_bhavcopy_bytes() -> bytes:
    columns = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL"
    header = f"{columns},TIMESTAMP,TOTALTRADES,ISIN"
    return (header + "\nBAD\n").encode()


async def _insert_running_run() -> EODPipelineRun:
    factory = get_session_factory()
    async with factory() as session:
        run = EODPipelineRun(
            source="date",
            status="running",
            current_stage="import",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


@asynccontextmanager
async def _client():
    """Async context manager yielding an authenticated admin browser client.

    The pipeline tests previously used ``async with await _client() as client``
    against a fresh ``AsyncClient``; that pattern is incompatible with an
    already-authenticated (and therefore already-open) httpx client, so we
    delegate to the shared ``admin_client`` context manager instead.
    """
    async with admin_client(browser=True) as client:
        yield client


# ---------------------------------------------------------------- service


@pytest.mark.asyncio
async def test_successful_full_pipeline() -> None:
    service = EODPipelineService()
    run = await service.run_from_file(
        bhavcopy=_bhavcopy_bytes(date.today(), rows=5), bhavcopy_name="eod.csv"
    )
    assert run.status == "completed"
    assert run.failed_stage is None
    assert run.import_job_id is not None
    assert run.imported_rows > 0
    assert run.indicators_calculated >= 0
    assert run.scanners_executed >= 0
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.duration_ms is not None
    assert run.duration_ms >= 0


@pytest.mark.asyncio
async def test_import_failure_stops_later_stages() -> None:
    service = EODPipelineService()
    run = await service.run_from_file(bhavcopy=_invalid_bhavcopy_bytes(), bhavcopy_name="bad.csv")
    assert run.status == "failed"
    assert run.failed_stage == "import"
    assert run.error_message
    assert run.indicators_calculated == 0
    assert run.scanners_executed == 0


@pytest.mark.asyncio
async def test_indicator_failure_stops_scanner_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(self: object) -> dict[str, int]:
        raise RuntimeError("indicator engine down")

    from app.services.indicator_engine import IndicatorEngine

    monkeypatch.setattr(IndicatorEngine, "calculate_all", _boom)

    service = EODPipelineService()
    run = await service.run_from_file(
        bhavcopy=_bhavcopy_bytes(date.today(), rows=5), bhavcopy_name="eod.csv"
    )
    assert run.status == "failed"
    assert run.failed_stage == "indicators"
    assert "indicator engine down" in (run.error_message or "")
    assert run.scanners_executed == 0


@pytest.mark.asyncio
async def test_scanner_failure_records_failed_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(self: object) -> dict[str, list[object]]:
        raise RuntimeError("scanner engine down")

    from app.services.scanner_engine import ScannerService

    monkeypatch.setattr(ScannerService, "run_all", _boom)

    service = EODPipelineService()
    run = await service.run_from_file(
        bhavcopy=_bhavcopy_bytes(date.today(), rows=5), bhavcopy_name="eod.csv"
    )
    assert run.status == "failed"
    assert run.failed_stage == "scanners"
    assert "scanner engine down" in (run.error_message or "")


@pytest.mark.asyncio
async def test_duplicate_pipeline_execution_prevention() -> None:
    await _insert_running_run()
    service = EODPipelineService()
    with pytest.raises(PipelineOverlapError):
        await service.run_from_file(
            bhavcopy=_bhavcopy_bytes(date.today(), rows=1), bhavcopy_name="eod.csv"
        )


@pytest.mark.asyncio
async def test_duplicate_date_import_remains_idempotent() -> None:
    service = EODPipelineService()
    payload = _bhavcopy_bytes(date.today(), rows=1)

    first = await service.run_from_file(bhavcopy=payload, bhavcopy_name="eod.csv")
    assert first.status == "completed"
    assert first.inserted_rows == 1
    assert first.updated_rows == 0

    second = await service.run_from_file(bhavcopy=payload, bhavcopy_name="eod.csv")
    assert second.status == "completed"
    assert second.inserted_rows == 0
    assert second.updated_rows == 1


@pytest.mark.asyncio
async def test_recover_abandoned_runs() -> None:
    run = await _insert_running_run()
    service = EODPipelineService()
    recovered = await service.recover_abandoned_runs()
    assert recovered == 1

    refreshed = await service.get_run(run.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.failed_stage == "import"
    assert "abandoned" in (refreshed.error_message or "").lower()


@pytest.mark.asyncio
async def test_error_message_does_not_expose_credentials() -> None:
    async def _boom(self: object) -> dict[str, list[object]]:
        raise RuntimeError("connect failed postgresql://admin:super-secret@host/db")

    from app.services.scanner_engine import ScannerService

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ScannerService, "run_all", _boom)
    try:
        run = await EODPipelineService().run_from_file(
            bhavcopy=_bhavcopy_bytes(date.today(), rows=5), bhavcopy_name="eod.csv"
        )
    finally:
        monkeypatch.undo()
    assert run.status == "failed"
    assert "super-secret" not in (run.error_message or "")
    assert "admin" not in (run.error_message or "")


# ---------------------------------------------------------------- API


@pytest.mark.asyncio
async def test_pipeline_run_status_api() -> None:
    async with _client() as client:
        response = await client.get("/api/v1/pipeline/status")
        assert response.status_code == 200
        body = response.json()
        assert "scheduler_enabled" in body
        assert "scheduler_running" in body
        assert "latest_run" in body
        assert "total_runs" in body
        assert body["scheduler_enabled"] is False


@pytest.mark.asyncio
async def test_pipeline_history_api() -> None:
    service = EODPipelineService()
    run = await service.run_from_file(
        bhavcopy=_bhavcopy_bytes(date.today(), rows=1), bhavcopy_name="eod.csv"
    )
    assert run.status == "completed"

    async with _client() as client:
        response = await client.get("/api/v1/pipeline/runs")
        assert response.status_code == 200
        body = response.json()
        assert len(body) >= 1
        assert body[0]["id"] == run.id
        assert body[0]["status"] == "completed"

        detail = await client.get(f"/api/v1/pipeline/runs/{run.id}")
        assert detail.status_code == 200
        assert detail.json()["id"] == run.id

        missing = await client.get("/api/v1/pipeline/runs/999999")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_pipeline_run_file_api() -> None:
    async with _client() as client:
        files = {"file": ("eod.csv", _bhavcopy_bytes(date.today(), rows=1), "text/csv")}
        response = await client.post("/api/v1/pipeline/run-file", files=files)
        assert response.status_code == 200
        body = response.json()
        assert body["run"]["status"] == "completed"
        assert body["run"]["inserted_rows"] == 1


@pytest.mark.asyncio
async def test_pipeline_run_file_empty_rejected() -> None:
    async with _client() as client:
        files = {"file": ("empty.csv", b"", "text/csv")}
        response = await client.post("/api/v1/pipeline/run-file", files=files)
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_pipeline_run_api_overlap_returns_409() -> None:
    await _insert_running_run()
    async with _client() as client:
        files = {"file": ("eod.csv", _bhavcopy_bytes(date.today(), rows=1), "text/csv")}
        response = await client.post("/api/v1/pipeline/run-file", files=files)
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_pipeline_run_date_weekend_rejected() -> None:
    saturday = date(2026, 8, 1)
    async with _client() as client:
        response = await client.post(
            "/api/v1/pipeline/run-date", json={"trade_date": str(saturday)}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["run"]["status"] == "failed"
        assert body["run"]["failed_stage"] == "import"


# ---------------------------------------------------------------- scheduler


def test_scheduler_disabled_by_default() -> None:
    assert settings.eod_scheduler_enabled is False
    scheduler = EODScheduler()
    assert scheduler.enabled is False
    status = scheduler.status()
    assert status["enabled"] is False
    assert status["registered"] is False


@pytest.mark.asyncio
async def test_scheduler_registers_one_job_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "eod_scheduler_enabled", True)
    scheduler = EODScheduler()
    assert scheduler.enabled is True
    try:
        scheduler.start()
        status = scheduler.status()
        assert status["registered"] is True
        assert status["job_id"] == "eod_pipeline_daily"
        assert status["next_run_time"] is not None
    finally:
        scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_does_not_register_duplicate_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "eod_scheduler_enabled", True)
    scheduler = EODScheduler()
    try:
        scheduler.start()
        scheduler.start()
        assert scheduler._registered is True
        assert scheduler._scheduler is not None
        jobs = scheduler._scheduler.get_jobs()
        assert len(jobs) == 1
    finally:
        scheduler.stop()


# ---------------------------------------------------------------- dashboard


@pytest.mark.asyncio
async def test_dashboard_pipeline_status_rendering() -> None:
    service = EODPipelineService()
    run = await service.run_from_file(
        bhavcopy=_bhavcopy_bytes(date.today(), rows=1), bhavcopy_name="eod.csv"
    )
    assert run.status == "completed"

    async with _client() as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "Pipeline Status" in response.text
        assert "completed" in response.text


@pytest.mark.asyncio
async def test_dashboard_pipeline_status_never() -> None:
    async with _client() as client:
        response = await client.get("/dashboard")
        assert response.status_code == 200
        assert "Pipeline Status" in response.text
        assert "No pipeline runs recorded yet" in response.text
