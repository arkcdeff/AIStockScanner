from __future__ import annotations

from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from app.core.config import settings
from app.db.session import get_session_factory
from app.services.eod_pipeline import EODPipelineService, PipelineOverlapError

_JOB_ID = "eod_pipeline_daily"


class EODScheduler:
    """APScheduler wrapper for the daily EOD pipeline.

    Uses an AsyncIOScheduler so the async pipeline service can be awaited
    directly. The job always opens a fresh database session per execution.
    """

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None
        self._registered = False

    @property
    def enabled(self) -> bool:
        return bool(settings.eod_scheduler_enabled)

    @property
    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def start(self) -> None:
        if not self.enabled:
            logger.info("EOD scheduler is disabled; skipping start")
            return
        if self._scheduler is not None and self._scheduler.running:
            logger.warning("EOD scheduler already running; ignoring duplicate start")
            return
        if self._registered:
            logger.info("EOD scheduler already registered; skipping re-registration")
            return

        scheduler = AsyncIOScheduler(timezone=settings.eod_scheduler_timezone)
        trigger = CronTrigger(
            day_of_week="mon-fri",
            hour=settings.eod_scheduler_hour,
            minute=settings.eod_scheduler_minute,
            timezone=settings.eod_scheduler_timezone,
        )
        scheduler.add_job(
            self._run_pipeline,
            trigger=trigger,
            id=_JOB_ID,
            name="EOD Pipeline Daily Run",
            max_instances=settings.eod_scheduler_max_instances,
            coalesce=settings.eod_scheduler_coalesce,
            replace_existing=True,
        )
        self._scheduler = scheduler
        self._registered = True
        scheduler.start()
        logger.info(
            "EOD scheduler started ({} {:02d}:{:02d}, weekdays, tz={})",
            _JOB_ID,
            settings.eod_scheduler_hour,
            settings.eod_scheduler_minute,
            settings.eod_scheduler_timezone,
        )

    def stop(self) -> None:
        if self._scheduler is not None:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._registered = False
        logger.info("EOD scheduler stopped")

    def status(self) -> dict[str, object]:
        job = self._scheduler.get_job(_JOB_ID) if self._scheduler is not None else None
        return {
            "enabled": self.enabled,
            "running": self.is_running,
            "job_id": _JOB_ID,
            "registered": job is not None,
            "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
            "timezone": settings.eod_scheduler_timezone,
        }

    async def _run_pipeline(self) -> None:
        started = datetime.now(UTC)
        logger.info("Scheduled EOD pipeline execution started")
        async with get_session_factory()() as session:
            service = EODPipelineService(session)
            try:
                run = await service.run_latest()
            except PipelineOverlapError as exc:
                logger.warning("Scheduled EOD pipeline skipped (overlap): {}", exc)
                return
            except Exception as exc:
                logger.error("Scheduled EOD pipeline failed: {}", exc)
                return
        duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        logger.info(
            "Scheduled EOD pipeline completed: run_id={} status={} duration_ms={:.1f}",
            run.id,
            run.status,
            duration_ms,
        )


_scheduler = EODScheduler()


def get_scheduler() -> EODScheduler:
    return _scheduler
