from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.ai.explanation_service import AIExplanationService
from app.core.config import settings
from app.models.alert import AlertEvent
from app.models.eod_pipeline_run import EODPipelineRun
from app.models.notification import DailySummaryRun
from app.models.scan_result import ScanResult
from app.models.technical_analysis import TechnicalAnalysis
from app.models.watchlist import WatchlistStock
from app.repositories.notification import (
    DailySummaryRunRepository,
    NotificationDeliveryRepository,
)
from app.schemas.notification import sanitize_notification_error
from app.services.notification_service import (
    EVENT_TYPE_DAILY_SUMMARY,
    NotificationService,
)
from app.services.portfolio import PortfolioService

SUMMARY_STATUS_RUNNING = "running"
SUMMARY_STATUS_COMPLETED = "completed"
SUMMARY_STATUS_FAILED = "failed"

_SUMMARY_STATUSES = frozenset(
    {SUMMARY_STATUS_RUNNING, SUMMARY_STATUS_COMPLETED, SUMMARY_STATUS_FAILED}
)
_CREDENTIAL_PATTERN = re.compile(r"(://)([^:/\s@]+):([^@/\s]+)@")


def current_summary_date(now: datetime | None = None) -> date:
    timezone = ZoneInfo(settings.daily_summary_timezone)
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(timezone).date()


def _safe_error(error: object) -> str:
    without_url_credentials = _CREDENTIAL_PATTERN.sub(r"\1***:***@", str(error))
    return sanitize_notification_error(without_url_credentials) or "Daily summary failed"


@dataclass
class DailySummaryExecution:
    run: DailySummaryRun
    context: dict[str, Any] | None = None
    delivery_ids: list[int] = field(default_factory=list)
    idempotent: bool = False


class DailySummaryService:
    """Build and deliver one deterministic, read-only summary per date."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        notification_service: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.run_repo = DailySummaryRunRepository(session)
        self.delivery_repo = NotificationDeliveryRepository(session)
        self.notification_service = notification_service or NotificationService(session)

    async def run(self, summary_date: date | None = None) -> DailySummaryExecution:
        requested_date = summary_date or current_summary_date()
        run, idempotent = await self._start_or_reuse(requested_date)
        if idempotent:
            deliveries = await self.delivery_repo.list_daily_summary_deliveries(requested_date)
            return DailySummaryExecution(
                run=run,
                delivery_ids=[item.id for item in deliveries],
                idempotent=True,
            )

        run_id = run.id
        try:
            context = await self.generate(requested_date)
            self._apply_metrics(run, context)
            await self.session.commit()

            delivery_ids: list[int] = []
            if settings.notifications_enabled:
                channels = await self.notification_service.eligible_channels(
                    event_type=EVENT_TYPE_DAILY_SUMMARY,
                    priority=0,
                    require_preference=True,
                )
                for channel in channels:
                    delivery = await self.notification_service.deliver_daily_summary(
                        channel,
                        summary_date=requested_date,
                        context=context,
                    )
                    delivery_ids.append(delivery.id)

            run.status = SUMMARY_STATUS_COMPLETED
            run.error_message = None
            run.finished_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(run)
            return DailySummaryExecution(
                run=run,
                context=context,
                delivery_ids=delivery_ids,
            )
        except Exception as exc:
            await self.session.rollback()
            failed = await self.session.get(DailySummaryRun, run_id)
            if failed is None:
                raise
            failed.status = SUMMARY_STATUS_FAILED
            failed.error_message = _safe_error(exc)
            failed.finished_at = datetime.now(UTC)
            await self.session.commit()
            await self.session.refresh(failed)
            return DailySummaryExecution(run=failed)

    async def generate(self, summary_date: date) -> dict[str, Any]:
        """Read existing persisted results and produce a deterministic context."""
        pipeline = await self.session.scalar(
            select(EODPipelineRun)
            .where(EODPipelineRun.trade_date == summary_date)
            .order_by(EODPipelineRun.started_at.desc(), EODPipelineRun.id.desc())
            .limit(1)
        )

        scanner_matches = int(
            await self.session.scalar(
                select(func.count())
                .select_from(ScanResult)
                .where(ScanResult.scan_date == summary_date)
            )
            or 0
        )
        if scanner_matches == 0 and pipeline is not None:
            scanner_matches = int(pipeline.scanner_matches or 0)

        top_rows = list(
            await self.session.scalars(
                select(TechnicalAnalysis)
                .where(TechnicalAnalysis.trade_date == summary_date)
                .options(joinedload(TechnicalAnalysis.stock))
                .order_by(
                    TechnicalAnalysis.overall_score.desc(),
                    TechnicalAnalysis.stock_id,
                )
                .limit(5)
            )
        )
        breakout_rows = list(
            await self.session.scalars(
                select(TechnicalAnalysis)
                .where(
                    TechnicalAnalysis.trade_date == summary_date,
                    TechnicalAnalysis.is_breakout.is_(True),
                )
                .options(joinedload(TechnicalAnalysis.stock))
                .order_by(
                    TechnicalAnalysis.overall_score.desc(),
                    TechnicalAnalysis.stock_id,
                )
                .limit(5)
            )
        )

        alerts_count = int(
            await self.session.scalar(
                select(func.count())
                .select_from(AlertEvent)
                .where(AlertEvent.trade_date == summary_date)
            )
            or 0
        )
        alert_rows = list(
            await self.session.scalars(
                select(AlertEvent)
                .where(AlertEvent.trade_date == summary_date)
                .options(joinedload(AlertEvent.alert_rule), joinedload(AlertEvent.stock))
                .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc())
                .limit(5)
            )
        )

        timezone = ZoneInfo(settings.daily_summary_timezone)
        local_start = datetime.combine(summary_date, datetime.min.time(), tzinfo=timezone)
        local_end = local_start + timedelta(days=1)
        watchlist_changes = int(
            await self.session.scalar(
                select(func.count())
                .select_from(WatchlistStock)
                .where(
                    WatchlistStock.added_at >= local_start.astimezone(UTC),
                    WatchlistStock.added_at < local_end.astimezone(UTC),
                )
            )
            or 0
        )

        portfolio = await PortfolioService(self.session).overall_summary()
        cached_ai = await AIExplanationService(self.session).get_cached_market_summary()
        cached_ai_text = None
        if cached_ai is not None and cached_ai.trade_date in {None, summary_date}:
            cached_ai_text = cached_ai.summary or cached_ai.explanation or None

        pipeline_status = pipeline.status if pipeline is not None else "not_run"
        market_summary_text = cached_ai_text or self._fallback_market_text(
            summary_date=summary_date,
            pipeline_status=pipeline_status,
            scanner_matches=scanner_matches,
            alerts_count=alerts_count,
            top_rated_count=len(top_rows),
        )
        return {
            "summary_date": summary_date.isoformat(),
            "pipeline_status": pipeline_status,
            "imported_rows": int(pipeline.imported_rows or 0) if pipeline else 0,
            "updated_rows": int(pipeline.updated_rows or 0) if pipeline else 0,
            "rejected_rows": int(pipeline.rejected_rows or 0) if pipeline else 0,
            "scanner_matches": scanner_matches,
            "top_rated_count": len(top_rows),
            "top_rated": [
                {"symbol": item.stock.symbol, "score": item.overall_score} for item in top_rows
            ],
            "breakouts": [
                {"symbol": item.stock.symbol, "score": item.overall_score} for item in breakout_rows
            ],
            "alerts_count": alerts_count,
            "alerts": [
                {
                    "symbol": item.stock.symbol,
                    "alert_name": item.alert_rule.name,
                }
                for item in alert_rows
            ],
            "watchlist_changes": watchlist_changes,
            "portfolio_value": portfolio.get("total_value", 0),
            "portfolio_daily_pl": portfolio.get("today_pl", 0),
            "market_summary_text": market_summary_text,
            "ai_summary_cached": cached_ai_text is not None,
            "failed_pipeline_stage": pipeline.failed_stage if pipeline else None,
        }

    async def recover_abandoned_runs(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(minutes=settings.notification_stale_minutes)
        running = await self.run_repo.list_filtered(status=SUMMARY_STATUS_RUNNING, limit=500)
        recovered = 0
        for run in running:
            started_at = run.started_at
            if started_at is not None and started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            if started_at is not None and started_at >= cutoff:
                continue
            run.status = SUMMARY_STATUS_FAILED
            run.error_message = "Abandoned daily summary run recovered"
            run.finished_at = datetime.now(UTC)
            recovered += 1
        if recovered:
            await self.session.commit()
        return recovered

    async def _start_or_reuse(self, summary_date: date) -> tuple[DailySummaryRun, bool]:
        existing = await self.run_repo.get_for_date(summary_date)
        if existing is not None:
            if existing.status == SUMMARY_STATUS_COMPLETED:
                return existing, True
            if existing.status == SUMMARY_STATUS_RUNNING:
                started_at = existing.started_at
                if started_at is not None and started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                cutoff = datetime.now(UTC) - timedelta(minutes=settings.notification_stale_minutes)
                if started_at is not None and started_at >= cutoff:
                    return existing, True
            self._reset_run(existing)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing, False

        run = DailySummaryRun(
            summary_date=summary_date,
            status=SUMMARY_STATUS_RUNNING,
            started_at=datetime.now(UTC),
        )
        self.session.add(run)
        try:
            await self.session.commit()
            await self.session.refresh(run)
            return run, False
        except IntegrityError:
            await self.session.rollback()
            concurrent = await self.run_repo.get_for_date(summary_date)
            if concurrent is None:
                raise
            return concurrent, True

    @staticmethod
    def _reset_run(run: DailySummaryRun) -> None:
        run.status = SUMMARY_STATUS_RUNNING
        run.alerts_count = 0
        run.scanner_matches = 0
        run.top_rated_count = 0
        run.watchlist_changes = 0
        run.portfolio_value = None
        run.started_at = datetime.now(UTC)
        run.finished_at = None
        run.error_message = None

    @staticmethod
    def _apply_metrics(run: DailySummaryRun, context: dict[str, Any]) -> None:
        run.alerts_count = int(context["alerts_count"])
        run.scanner_matches = int(context["scanner_matches"])
        run.top_rated_count = int(context["top_rated_count"])
        run.watchlist_changes = int(context["watchlist_changes"])
        run.portfolio_value = Decimal(str(context["portfolio_value"]))

    @staticmethod
    def _fallback_market_text(
        *,
        summary_date: date,
        pipeline_status: str,
        scanner_matches: int,
        alerts_count: int,
        top_rated_count: int,
    ) -> str:
        return (
            f"{summary_date.isoformat()}: pipeline {pipeline_status}; "
            f"{scanner_matches} scanner matches, {alerts_count} alerts, "
            f"and {top_rated_count} top-rated stocks."
        )


def supported_summary_statuses() -> frozenset[str]:
    return _SUMMARY_STATUSES
