from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.core.config import settings
from app.db.session import get_session_factory
from app.services.daily_summary import current_summary_date
from app.services.eod_pipeline import EODPipelineService, PipelineOverlapError
from app.services.market_data_service import MarketDataService
from app.services.market_intelligence_jobs import (
    run_daily_snapshot_intelligence_job,
    run_postmarket_intelligence_job,
    run_premarket_intelligence_job,
    sanitize_job_error,
)
from app.services.notification_jobs import (
    run_daily_summary_job,
    run_pending_delivery_job,
)

EOD_JOB_ID = "eod_pipeline_daily"
MARKET_DATA_CATCHUP_ID = "market_data_catchup"
NOTIFICATION_DELIVERY_JOB_ID = "notification_delivery_interval"
DAILY_SUMMARY_JOB_ID = "notification_daily_summary"
MARKET_INTELLIGENCE_PREMARKET_JOB_ID = "market_intelligence_premarket"
MARKET_INTELLIGENCE_POSTMARKET_JOB_ID = "market_intelligence_postmarket"
MARKET_INTELLIGENCE_SNAPSHOT_JOB_ID = "market_intelligence_snapshot"


class EODScheduler:
    """Single APScheduler wrapper for EOD and notification jobs."""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None
        self._registered = False
        self._last_execution: dict[str, dict[str, object]] = {}

    @property
    def enabled(self) -> bool:
        return bool(
            settings.eod_scheduler_enabled
            or settings.notification_delivery_job_enabled
            or settings.daily_summary_enabled
            or settings.market_intelligence_premarket_enabled
            or settings.market_intelligence_postmarket_enabled
            or settings.market_intelligence_snapshot_enabled
        )

    @property
    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def start(self) -> None:
        if not self.enabled:
            logger.info("Application scheduler is disabled; skipping start")
            return
        if self._scheduler is not None and self._scheduler.running:
            logger.warning("Application scheduler already running; ignoring duplicate start")
            return
        if self._registered:
            logger.info("Application scheduler already registered; skipping re-registration")
            return

        scheduler = AsyncIOScheduler(timezone=settings.eod_scheduler_timezone)
        if settings.eod_scheduler_enabled:
            scheduler.add_job(
                self._run_pipeline,
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=settings.eod_scheduler_hour,
                    minute=settings.eod_scheduler_minute,
                    timezone=settings.eod_scheduler_timezone,
                ),
                id=EOD_JOB_ID,
                name="EOD Pipeline Daily Run",
                max_instances=settings.eod_scheduler_max_instances,
                coalesce=settings.eod_scheduler_coalesce,
                replace_existing=True,
            )
        if settings.notification_delivery_job_enabled:
            scheduler.add_job(
                self._run_notification_delivery,
                trigger=IntervalTrigger(minutes=settings.notification_delivery_interval_minutes),
                id=NOTIFICATION_DELIVERY_JOB_ID,
                name="Notification Pending Delivery",
                max_instances=settings.notification_delivery_max_instances,
                coalesce=settings.notification_delivery_coalesce,
                replace_existing=True,
            )
        if settings.daily_summary_enabled:
            scheduler.add_job(
                self._run_daily_summary,
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=settings.daily_summary_hour,
                    minute=settings.daily_summary_minute,
                    timezone=settings.daily_summary_timezone,
                ),
                id=DAILY_SUMMARY_JOB_ID,
                name="Notification Daily Summary",
                max_instances=settings.daily_summary_max_instances,
                coalesce=settings.daily_summary_coalesce,
                replace_existing=True,
            )
        if settings.market_intelligence_premarket_enabled:
            scheduler.add_job(
                self._run_premarket_intelligence,
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=settings.market_intelligence_premarket_hour,
                    minute=settings.market_intelligence_premarket_minute,
                    timezone=settings.market_intelligence_premarket_timezone,
                ),
                id=MARKET_INTELLIGENCE_PREMARKET_JOB_ID,
                name="Market Intelligence Pre-Market Refresh",
                max_instances=settings.market_intelligence_premarket_max_instances,
                coalesce=settings.market_intelligence_premarket_coalesce,
                replace_existing=True,
            )
        if settings.market_intelligence_postmarket_enabled:
            scheduler.add_job(
                self._run_postmarket_intelligence,
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=settings.market_intelligence_postmarket_hour,
                    minute=settings.market_intelligence_postmarket_minute,
                    timezone=settings.market_intelligence_postmarket_timezone,
                ),
                id=MARKET_INTELLIGENCE_POSTMARKET_JOB_ID,
                name="Market Intelligence Post-Market Refresh",
                max_instances=settings.market_intelligence_postmarket_max_instances,
                coalesce=settings.market_intelligence_postmarket_coalesce,
                replace_existing=True,
            )
        if settings.market_intelligence_snapshot_enabled:
            scheduler.add_job(
                self._run_daily_snapshot_intelligence,
                trigger=CronTrigger(
                    day_of_week="mon-fri",
                    hour=settings.market_intelligence_snapshot_hour,
                    minute=settings.market_intelligence_snapshot_minute,
                    timezone=settings.market_intelligence_snapshot_timezone,
                ),
                id=MARKET_INTELLIGENCE_SNAPSHOT_JOB_ID,
                name="Market Intelligence Daily Snapshot",
                max_instances=settings.market_intelligence_snapshot_max_instances,
                coalesce=settings.market_intelligence_snapshot_coalesce,
                replace_existing=True,
            )

        self._scheduler = scheduler
        self._registered = True
        scheduler.start()
        logger.info(
            "Application scheduler started jobs={}",
            [job.id for job in scheduler.get_jobs()],
        )

    def stop(self) -> None:
        if self._scheduler is not None:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._registered = False
        logger.info("Application scheduler stopped")

    def status(self) -> dict[str, object]:
        jobs = self._scheduler.get_jobs() if self._scheduler is not None else []
        registered = {job.id: job for job in jobs}

        def job_status(job_id: str, *, enabled: bool) -> dict[str, object]:
            job = registered.get(job_id)
            return {
                "job_id": job_id,
                "enabled": enabled,
                "registered": job is not None,
                "next_run_time": (
                    job.next_run_time.isoformat() if job and job.next_run_time else None
                ),
                "last_execution": self._last_execution.get(job_id),
            }

        eod = job_status(EOD_JOB_ID, enabled=settings.eod_scheduler_enabled)
        delivery = job_status(
            NOTIFICATION_DELIVERY_JOB_ID,
            enabled=settings.notification_delivery_job_enabled,
        )
        daily_summary = job_status(
            DAILY_SUMMARY_JOB_ID,
            enabled=settings.daily_summary_enabled,
        )
        premarket = job_status(
            MARKET_INTELLIGENCE_PREMARKET_JOB_ID,
            enabled=settings.market_intelligence_premarket_enabled,
        )
        postmarket = job_status(
            MARKET_INTELLIGENCE_POSTMARKET_JOB_ID,
            enabled=settings.market_intelligence_postmarket_enabled,
        )
        snapshot = job_status(
            MARKET_INTELLIGENCE_SNAPSHOT_JOB_ID,
            enabled=settings.market_intelligence_snapshot_enabled,
        )
        mi_pre = settings.market_intelligence_premarket_enabled
        mi_post = settings.market_intelligence_postmarket_enabled
        mi_snap = settings.market_intelligence_snapshot_enabled
        return {
            "enabled": self.enabled,
            "running": self.is_running,
            "job_id": EOD_JOB_ID,
            "registered": eod["registered"],
            "next_run_time": eod["next_run_time"],
            "timezone": settings.eod_scheduler_timezone,
            "alert_delivery_job_enabled": settings.notification_delivery_job_enabled,
            "daily_summary_job_enabled": settings.daily_summary_enabled,
            "market_intelligence_premarket_enabled": mi_pre,
            "market_intelligence_postmarket_enabled": mi_post,
            "market_intelligence_snapshot_enabled": mi_snap,
            "eod_retry_enabled": settings.eod_scheduler_retry_enabled,
            "eod_max_retries": settings.eod_scheduler_max_retries,
            "eod_run_scanners": settings.eod_scheduler_run_scanners,
            "eod_auto_catchup": settings.eod_scheduler_auto_catchup,
            "registered_job_ids": sorted(registered),
            "last_catchup": self._last_execution.get(MARKET_DATA_CATCHUP_ID),
            "jobs": {
                "eod_pipeline": eod,
                "alert_delivery": delivery,
                "daily_summary": daily_summary,
                "market_intelligence_premarket": premarket,
                "market_intelligence_postmarket": postmarket,
                "market_intelligence_snapshot": snapshot,
            },
        }

    async def run_delivery_now(self) -> dict[str, object]:
        return await self._run_notification_delivery()

    async def run_daily_summary_now(self, summary_date: date | None = None) -> dict[str, object]:
        return await self._run_daily_summary(summary_date=summary_date)

    async def run_premarket_intelligence_now(self) -> dict[str, object]:
        return await self._run_premarket_intelligence()

    async def run_postmarket_intelligence_now(self) -> dict[str, object]:
        return await self._run_postmarket_intelligence()

    async def run_daily_snapshot_intelligence_now(self) -> dict[str, object]:
        return await self._run_daily_snapshot_intelligence()

    async def _run_pipeline(self) -> None:
        started = datetime.now(UTC)
        logger.info("Scheduled EOD pipeline execution started")
        service = EODPipelineService()
        max_retries = (
            settings.eod_scheduler_max_retries if settings.eod_scheduler_retry_enabled else 0
        )
        attempt = 0
        run = None
        while True:
            try:
                run = await service.run_latest(
                    run_scanners=settings.eod_scheduler_run_scanners
                )
            except PipelineOverlapError as exc:
                logger.warning("Scheduled EOD pipeline skipped (overlap): {}", exc)
                self._last_execution[EOD_JOB_ID] = {
                    "status": "skipped",
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                return
            except Exception as exc:
                logger.error("Scheduled EOD pipeline failed: {}", sanitize_job_error(exc))
                self._last_execution[EOD_JOB_ID] = {
                    "status": "failed",
                    "finished_at": datetime.now(UTC).isoformat(),
                }
                return

            # Retry only when the import stage failed (e.g. the bhavcopy was not
            # yet published by NSE at the scheduled time). Bound the total effort.
            retryable = run is not None and run.status == "failed" and run.failed_stage == "import"
            if not retryable or attempt >= max_retries:
                break
            attempt += 1
            delay_seconds = settings.eod_scheduler_retry_minutes * 60
            logger.warning(
                "Scheduled EOD pipeline import attempt {} failed; retrying in {}s",
                attempt,
                delay_seconds,
            )
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

        if run is None:
            return
        duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        self._last_execution[EOD_JOB_ID] = {
            "status": run.status,
            "run_id": run.id,
            "retry_attempts": attempt,
            "duration_ms": round(duration_ms, 1),
            "finished_at": datetime.now(UTC).isoformat(),
        }
        logger.info(
            "Scheduled EOD pipeline completed: run_id={} status={} retries={} duration_ms={:.1f}",
            run.id,
            run.status,
            attempt,
            duration_ms,
        )

    async def run_market_data_catchup(self) -> dict[str, object]:
        """Backfill trailing missing trading days on startup (config gated)."""
        if not settings.eod_scheduler_auto_catchup:
            return {"status": "disabled", "message": "Auto catch-up is disabled"}
        started = datetime.now(UTC)
        end_date = started.date()
        start_date = end_date - timedelta(days=settings.eod_scheduler_catchup_max_days)
        logger.info(
            "Market data catch-up started window={}..{}",
            start_date.isoformat(),
            end_date.isoformat(),
        )
        try:
            async with get_session_factory()() as session:
                summary = await MarketDataService(session, request_delay=1.0).backfill(
                    start_date, end_date
                )
            payload: dict[str, object] = {
                "status": "completed",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                **summary,
                "finished_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.error("Market data catch-up failed: {}", sanitize_job_error(exc))
            payload = {
                "status": "failed",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        self._last_execution[MARKET_DATA_CATCHUP_ID] = payload
        logger.info(
            "Market data catch-up completed status={} finished_at={}",
            payload["status"],
            payload["finished_at"],
        )
        return payload

    async def _run_notification_delivery(self) -> dict[str, object]:
        result = await run_pending_delivery_job()
        payload = {
            **result.as_dict(),
            "finished_at": datetime.now(UTC).isoformat(),
        }
        self._last_execution[NOTIFICATION_DELIVERY_JOB_ID] = payload
        return payload

    async def _run_daily_summary(self, *, summary_date: date | None = None) -> dict[str, object]:
        requested_date = summary_date or current_summary_date()
        started = datetime.now(UTC)
        logger.info("Scheduled daily summary started date={}", requested_date)
        try:
            execution = await run_daily_summary_job(requested_date)
            status = execution.run.status
            payload = {
                "status": status,
                "run_id": execution.run.id,
                "summary_date": requested_date.isoformat(),
                "idempotent": execution.idempotent,
                "delivery_count": len(execution.delivery_ids),
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.error(
                "Scheduled daily summary failed date={}: {}",
                requested_date,
                sanitize_job_error(exc),
            )
            payload = {
                "status": "failed",
                "summary_date": requested_date.isoformat(),
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        self._last_execution[DAILY_SUMMARY_JOB_ID] = payload
        logger.info(
            "Scheduled daily summary completed date={} status={} duration_ms={}",
            requested_date,
            payload["status"],
            payload["duration_ms"],
        )
        return payload

    async def _run_premarket_intelligence(self) -> dict[str, object]:
        started = datetime.now(UTC)
        logger.info("Scheduled market intelligence pre-market refresh started")
        try:
            result = await run_premarket_intelligence_job()
            payload = {
                **result.as_dict(),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            msg = sanitize_job_error(exc)
            logger.error("Scheduled market intelligence pre-market failed: {}", msg)
            payload = {
                "status": "failed",
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        self._last_execution[MARKET_INTELLIGENCE_PREMARKET_JOB_ID] = payload
        logger.info(
            "Scheduled market intelligence pre-market completed status={} duration_ms={}",
            payload["status"],
            payload["duration_ms"],
        )
        return payload

    async def _run_postmarket_intelligence(self) -> dict[str, object]:
        started = datetime.now(UTC)
        logger.info("Scheduled market intelligence post-market refresh started")
        try:
            result = await run_postmarket_intelligence_job()
            payload = {
                **result.as_dict(),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            msg = sanitize_job_error(exc)
            logger.error("Scheduled market intelligence post-market failed: {}", msg)
            payload = {
                "status": "failed",
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        self._last_execution[MARKET_INTELLIGENCE_POSTMARKET_JOB_ID] = payload
        logger.info(
            "Scheduled market intelligence post-market completed status={} duration_ms={}",
            payload["status"],
            payload["duration_ms"],
        )
        return payload

    async def _run_daily_snapshot_intelligence(self) -> dict[str, object]:
        started = datetime.now(UTC)
        logger.info("Scheduled market intelligence daily snapshot started")
        try:
            result = await run_daily_snapshot_intelligence_job()
            payload = {
                **result.as_dict(),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            msg = sanitize_job_error(exc)
            logger.error("Scheduled market intelligence daily snapshot failed: {}", msg)
            payload = {
                "status": "failed",
                "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 1),
                "finished_at": datetime.now(UTC).isoformat(),
            }
        self._last_execution[MARKET_INTELLIGENCE_SNAPSHOT_JOB_ID] = payload
        logger.info(
            "Scheduled market intelligence daily snapshot completed status={} duration_ms={}",
            payload["status"],
            payload["duration_ms"],
        )
        return payload


_scheduler = EODScheduler()


def get_scheduler() -> EODScheduler:
    return _scheduler
