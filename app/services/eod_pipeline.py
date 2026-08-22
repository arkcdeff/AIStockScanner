from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session_factory
from app.models.daily_price import DailyPrice
from app.models.eod_pipeline_run import EODPipelineRun
from app.schemas.notification import sanitize_notification_error
from app.services.alert import AlertService
from app.services.analytics_engine import AnalyticsEngine
from app.services.indicator_engine import IndicatorEngine
from app.services.nse_downloader import NSEDownloader
from app.services.nse_import import NSEImportService
from app.services.scanner_engine import ScannerService

PIPELINE_SOURCE_FILE = "file"
PIPELINE_SOURCE_DATE = "date"
PIPELINE_SOURCE_LATEST = "latest"


class PipelineError(Exception):
    pass


class PipelineOverlapError(PipelineError):
    pass


_CREDENTIAL_PATTERN = re.compile(r"(://)([^:/\s@]+):([^@/\s]+)@")


def _sanitize_error(message: str) -> str:
    safe = _CREDENTIAL_PATTERN.sub(r"\1***:***@", message)
    return sanitize_notification_error(safe) or "Pipeline failure"


class EODPipelineService:
    """Orchestrates the daily EOD processing pipeline.

    Each stage runs in its own fresh database session to preserve the
    existing transaction boundaries of the import, indicator, and scanner
    services. Never shares a request session across stages.
    """

    async def run_from_file(
        self,
        *,
        bhavcopy: bytes,
        bhavcopy_name: str,
        delivery: bytes | None = None,
        delivery_name: str | None = None,
        run_scanners: bool | None = None,
    ) -> EODPipelineRun:
        async def _import(session: AsyncSession) -> Any:
            return await NSEImportService(session).import_bytes(
                bhavcopy=bhavcopy,
                bhavcopy_name=bhavcopy_name,
                delivery=delivery,
                delivery_name=delivery_name,
                mode="UPLOAD",
            )

        return await self._run_pipeline(
            PIPELINE_SOURCE_FILE, _import, run_scanners=run_scanners
        )

    async def run_from_date(
        self, trade_date: date, *, run_scanners: bool | None = None
    ) -> EODPipelineRun:
        async def _import(session: AsyncSession) -> Any:
            path: Path = await asyncio.to_thread(self._download, trade_date)
            return await NSEImportService(session).import_files(
                path, mode="DATE_DOWNLOAD"
            )

        return await self._run_pipeline(
            PIPELINE_SOURCE_DATE, _import, run_scanners=run_scanners
        )

    async def run_latest(self, *, run_scanners: bool | None = None) -> EODPipelineRun:
        async def _import(session: AsyncSession) -> Any:
            _, path = await asyncio.to_thread(self._download_latest)
            return await NSEImportService(session).import_files(
                path, mode="AUTO_EOD"
            )

        return await self._run_pipeline(
            PIPELINE_SOURCE_LATEST, _import, run_scanners=run_scanners
        )

    @staticmethod
    def _download(trade_date: date) -> Path:
        with NSEDownloader() as downloader:
            path, _ = downloader.download(trade_date)
        return path

    @staticmethod
    def _download_latest() -> tuple[date, Path]:
        with NSEDownloader() as downloader:
            trade_date, path, _ = downloader.download_latest()
        return trade_date, path

    async def _run_pipeline(
        self,
        source: str,
        import_fn: Callable[[AsyncSession], Awaitable[Any]],
        run_scanners: bool | None = None,
    ) -> EODPipelineRun:
        should_run_scanners = (
            settings.eod_scheduler_run_scanners if run_scanners is None else run_scanners
        )
        async with get_session_factory()() as session:
            active = await session.scalar(
                select(EODPipelineRun).where(EODPipelineRun.status == "running").limit(1)
            )
            if active is not None:
                raise PipelineOverlapError(
                    f"Pipeline run {active.id} is already in progress "
                    f"(started {active.started_at.isoformat() if active.started_at else 'unknown'})"
                )
            run = EODPipelineRun(
                source=source,
                status="running",
                current_stage="import",
                started_at=datetime.now(UTC),
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)
            run_id = run.id

        metrics: dict[str, int] = {
            "imported_rows": 0,
            "inserted_rows": 0,
            "updated_rows": 0,
            "rejected_rows": 0,
            "stocks_processed": 0,
            "indicators_calculated": 0,
            "scanners_executed": 0,
            "scanner_matches": 0,
            "analytics_calculated": 0,
            "alerts_triggered": 0,
        }
        current_stage = "import"
        trade_date: date | None = None

        try:
            # Stage 1: validate + import market data
            async with get_session_factory()() as session:
                job = await import_fn(session)
            if job.status != "completed":
                await self._fail(run_id, "import", job.error_message or "Import stage failed")
                return await self.get_run(run_id)
            trade_date = job.trade_date
            await self._update(
                run_id,
                import_job_id=job.id,
                trade_date=job.trade_date,
                current_stage="indicators",
            )
            metrics["imported_rows"] = job.rows_read
            metrics["inserted_rows"] = job.rows_inserted
            metrics["updated_rows"] = job.rows_updated
            metrics["rejected_rows"] = job.rows_rejected

            # Stage 2: calculate indicators for affected stocks
            current_stage = "indicators"
            await self._update(run_id, current_stage="indicators")
            async with get_session_factory()() as session:
                engine = IndicatorEngine(session)
                summary = await engine.calculate_all()
                await session.commit()
                stocks_processed = (
                    await session.scalar(
                        select(func.count(func.distinct(DailyPrice.stock_id))).where(
                            DailyPrice.trade_date == trade_date
                        )
                    )
                    or 0
                )
            metrics["indicators_calculated"] = summary["calculated"]
            metrics["stocks_processed"] = int(stocks_processed)

            # Stage 3: run all active scanners and persist results (toggleable)
            current_stage = "scanners"
            if should_run_scanners:
                await self._update(run_id, current_stage="scanners")
                async with get_session_factory()() as session:
                    service = ScannerService(session)
                    results = await service.run_all()
                    await service.persist_results(results)
                metrics["scanners_executed"] = len(results)
                metrics["scanner_matches"] = sum(len(entries) for entries in results.values())
            else:
                await self._update(run_id, current_stage="scanners")
                metrics["scanners_executed"] = 0
                metrics["scanner_matches"] = 0

            # Stage 4: compute technical analytics for affected stocks
            current_stage = "analytics"
            await self._update(run_id, current_stage="analytics")
            async with get_session_factory()() as session:
                engine = AnalyticsEngine(session)
                summary = await engine.calculate_all()
                await session.commit()
            metrics["analytics_calculated"] = summary["calculated"]

            # Stage 5: evaluate alert rules against the freshly computed analytics
            current_stage = "alerts"
            await self._update(run_id, current_stage="alerts")
            async with get_session_factory()() as session:
                alert_summary = await AlertService(session).run_for_trade_date(trade_date)
            metrics["alerts_triggered"] = alert_summary["triggered"]

            completed = await self._complete(run_id, trade_date, metrics)
            if trade_date is not None:
                try:
                    from app.services.notification_jobs import (
                        deliver_alert_events_for_date,
                    )

                    notification_result = await deliver_alert_events_for_date(trade_date)
                    if notification_result.status == "failed":
                        logger.error(
                            "Pipeline {} completed but alert notification orchestration failed: {}",
                            run_id,
                            notification_result.error or "unknown notification failure",
                        )
                except Exception as exc:
                    logger.error(
                        "Pipeline {} completed but alert notification orchestration failed: {}",
                        run_id,
                        _sanitize_error(str(exc)),
                    )
            return completed
        except PipelineError:
            raise
        except Exception as exc:
            await self._fail(run_id, current_stage, exc)
            return await self.get_run(run_id)

    async def get_run(self, run_id: int) -> EODPipelineRun | None:
        async with get_session_factory()() as session:
            return await session.get(EODPipelineRun, run_id)

    async def list_runs(self, limit: int = 20, offset: int = 0) -> list[EODPipelineRun]:
        async with get_session_factory()() as session:
            rows = (
                await session.scalars(
                    select(EODPipelineRun)
                    .order_by(EODPipelineRun.started_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return list(rows)

    async def status(self) -> dict[str, Any]:
        async with get_session_factory()() as session:
            total = await session.scalar(select(func.count()).select_from(EODPipelineRun)) or 0
            running = (
                await session.scalar(
                    select(func.count())
                    .select_from(EODPipelineRun)
                    .where(EODPipelineRun.status == "running")
                )
                or 0
            )
            latest = await session.scalar(
                select(EODPipelineRun).order_by(EODPipelineRun.started_at.desc()).limit(1)
            )
            return {
                "total_runs": int(total),
                "running_runs": int(running),
                "latest_run": latest,
            }

    async def recover_abandoned_runs(self) -> int:
        now = datetime.now(UTC)
        recovered = 0
        async with get_session_factory()() as session:
            stuck = (
                await session.scalars(
                    select(EODPipelineRun).where(EODPipelineRun.status == "running")
                )
            ).all()
            for run in stuck:
                run.status = "failed"
                run.failed_stage = run.current_stage or "unknown"
                run.error_message = "Abandoned pipeline run recovered on startup"
                run.finished_at = now
                if run.started_at:
                    run.duration_ms = round((now - run.started_at).total_seconds() * 1000, 2)
                recovered += 1
            await session.commit()
        return recovered

    async def _update(self, run_id: int, **fields: Any) -> None:
        async with get_session_factory()() as session:
            run = await session.get(EODPipelineRun, run_id)
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)
            await session.commit()

    async def _complete(
        self, run_id: int, trade_date: date | None, metrics: dict[str, int]
    ) -> EODPipelineRun:
        finished = datetime.now(UTC)
        async with get_session_factory()() as session:
            run = await session.get(EODPipelineRun, run_id)
            if run is None:
                raise PipelineError(f"Pipeline run {run_id} not found")
            run.trade_date = trade_date
            for key, value in metrics.items():
                setattr(run, key, value)
            run.status = "completed"
            run.current_stage = "completed"
            run.finished_at = finished
            if run.started_at:
                run.duration_ms = round((finished - run.started_at).total_seconds() * 1000, 2)
            await session.commit()
            await session.refresh(run)
            return run

    async def _fail(self, run_id: int, stage: str, error: Any) -> None:
        finished = datetime.now(UTC)
        message = _sanitize_error(str(error))
        logger.error("EOD pipeline run {} failed at stage {}: {}", run_id, stage, message)
        async with get_session_factory()() as session:
            run = await session.get(EODPipelineRun, run_id)
            if run is None:
                return
            run.status = "failed"
            run.current_stage = stage
            run.failed_stage = stage
            run.error_message = message
            run.finished_at = finished
            if run.started_at:
                run.duration_ms = round((finished - run.started_at).total_seconds() * 1000, 2)
            await session.commit()
