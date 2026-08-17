from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_session_factory
from app.services.ai_market_explanation import create_market_explanation
from app.services.composite_market_intelligence import (
    CompositeMarketIntelligence,
)
from app.services.market_intelligence_notifications import (
    MarketIntelligenceNotificationService,
    get_previous_snapshot,
    snapshot_to_composite_result,
)
from app.services.notification_jobs import sanitize_job_error


@dataclass
class MarketIntelligenceJobResult:
    status: str
    market_date: str | None = None
    snapshot_id: int | None = None
    composite_score: float | None = None
    regime: str | None = None
    confidence_score: float | None = None
    factors_refreshed: int = 0
    factors_failed: int = 0
    explanation_generated: bool = False
    duration_ms: float = 0
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


async def run_market_intelligence_refresh_job(
    *,
    market_date: date | None = None,
    generate_explanation: bool = False,
    persistence: bool = True,
) -> MarketIntelligenceJobResult:
    """Run a market intelligence refresh cycle using a fresh session.

    This job evaluates all factor engines at the same boundary and optionally
    persists a MarketIntelligenceSnapshot. An AI explanation can be generated
    on top of the deterministic composite result.
    """
    started = datetime.now(UTC)
    mi_snap = settings.market_intelligence_snapshot_enabled
    mi_pre = settings.market_intelligence_premarket_enabled
    mi_post = settings.market_intelligence_postmarket_enabled
    if not mi_snap and not mi_pre and not mi_post:
        logger.info("Market intelligence scheduler jobs are disabled; skipping")
        return MarketIntelligenceJobResult(status="disabled")

    logger.info("Market intelligence refresh job started")
    eval_date = market_date or datetime.now(UTC).astimezone().date()
    evaluation_at = datetime.combine(eval_date, datetime.min.time(), tzinfo=UTC)

    try:
        async with get_session_factory()() as session:
            result = await _evaluate_and_persist(
                session,
                evaluation_at=evaluation_at,
                market_date=eval_date,
                persistence=persistence,
                generate_explanation=generate_explanation,
            )
    except Exception as exc:
        duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        error = sanitize_job_error(exc)
        logger.error(
            "Market intelligence refresh job failed date={} duration_ms={:.1f}: {}",
            eval_date,
            duration_ms,
            error,
        )
        return MarketIntelligenceJobResult(
            status="failed",
            market_date=eval_date.isoformat(),
            duration_ms=duration_ms,
            error=error,
        )

    duration_ms = (datetime.now(UTC) - started).total_seconds() * 1000
    comp = result.composite_score or 0.0
    reg = result.regime or "unknown"
    logger.info(
        "Market intelligence refresh completed date={} status={} "
        "composite={:.2f} regime={} duration_ms={:.1f}",
        eval_date,
        result.status,
        comp,
        reg,
        duration_ms,
    )
    result.duration_ms = duration_ms
    return result


async def run_premarket_intelligence_job() -> MarketIntelligenceJobResult:
    """Pre-market intelligence refresh (before Indian market open).

    Evaluates overnight global/Asian context, GIFT Nifty, and other pre-market
    factors. Does NOT persist a snapshot by default (informational only).
    """
    return await run_market_intelligence_refresh_job(
        persistence=False,
        generate_explanation=False,
    )


async def run_postmarket_intelligence_job() -> MarketIntelligenceJobResult:
    """Post-market intelligence refresh (after Indian market close).

    Evaluates all factors with completed session data, persists a snapshot,
    and generates an AI explanation for the daily summary.
    """
    return await run_market_intelligence_refresh_job(
        persistence=True,
        generate_explanation=True,
    )


async def run_daily_snapshot_intelligence_job() -> MarketIntelligenceJobResult:
    """Daily market intelligence snapshot (canonical end-of-day evaluation).

    Runs after post-market close, persists snapshot with full factor evaluation
    and AI explanation. This is the canonical daily intelligence record.
    """
    return await run_market_intelligence_refresh_job(
        persistence=True,
        generate_explanation=True,
    )


async def _evaluate_and_persist(
    session: AsyncSession,
    *,
    evaluation_at: datetime,
    market_date: date,
    persistence: bool,
    generate_explanation: bool,
) -> MarketIntelligenceJobResult:
    """Internal: evaluate composite intelligence and optionally persist + explain."""
    from app.repositories.market_index_price import MarketIndexPriceRepository
    from app.repositories.market_intelligence_snapshot import (
        MarketIntelligenceSnapshotRepository,
    )
    from app.services.external_market_factor_intelligence import (
        ExternalMarketFactorIntelligence,
    )
    from app.services.gift_nifty_intelligence import GiftNiftyIntelligence
    from app.services.global_market_intelligence import GlobalMarketIntelligence
    from app.services.institutional_intelligence import InstitutionalIntelligence
    from app.services.macro_intelligence import MacroIntelligence
    from app.services.market_breadth import MarketBreadth
    from app.services.sector_intelligence import SectorIntelligence

    price_repo = MarketIndexPriceRepository(session)
    snapshot_repo = MarketIntelligenceSnapshotRepository(session)
    breadth_engine = MarketBreadth(session)
    sector_engine = SectorIntelligence(session)
    institutional_engine = InstitutionalIntelligence(session)
    external_engine = ExternalMarketFactorIntelligence(session)
    global_engine = GlobalMarketIntelligence(session)
    gift_nifty_engine = GiftNiftyIntelligence(session)
    macro_engine = MacroIntelligence(session)

    engine = CompositeMarketIntelligence(
        session,
        price_repo=price_repo,
        snapshot_repo=snapshot_repo,
        breadth_engine=breadth_engine,
        sector_engine=sector_engine,
        institutional_engine=institutional_engine,
        external_factor_engine=external_engine,
        global_engine=global_engine,
        gift_nifty_engine=gift_nifty_engine,
        macro_engine=macro_engine,
    )

    result = await engine.evaluate(
        evaluation_at,
        market_date=market_date,
        persistence=persistence,
    )

    explanation_generated = False
    if generate_explanation:
        try:
            explanation = create_market_explanation(result)
            explanation_generated = True
            logger.debug("Market explanation generated provider={} is_ai={}",
                         explanation.provider, explanation.is_ai_generated)
        except Exception as exc:
            logger.warning("Market explanation generation failed: {}", sanitize_job_error(exc))

    # Trigger market intelligence notifications if persistence was enabled
    notifications_triggered = 0
    if persistence and result.snapshot_id is not None:
        try:
            # Get previous snapshot for comparison
            previous_snapshot = await get_previous_snapshot(session, market_date)
            previous_result = None
            if previous_snapshot:
                previous_result = snapshot_to_composite_result(previous_snapshot)

            # Evaluate and send notifications
            notification_service = MarketIntelligenceNotificationService(session)
            notification_results = await notification_service.evaluate_and_notify(
                current_result=result,
                previous_result=previous_result,
                market_date=market_date,
            )
            notifications_triggered = sum(1 for r in notification_results if r.triggered)
            logger.info("Market intelligence notifications triggered: {}", notifications_triggered)
        except Exception as exc:
            msg = sanitize_job_error(exc)
            logger.warning("Market intelligence notification evaluation failed: {}", msg)

    factors_refreshed = sum(1 for f in result.factors if f.score is not None)
    factors_failed = sum(1 for f in result.factors if f.score is None)

    return MarketIntelligenceJobResult(
        status="completed",
        market_date=market_date.isoformat(),
        snapshot_id=result.snapshot_id,
        composite_score=result.composite_score,
        regime=result.regime,
        confidence_score=result.confidence_score,
        factors_refreshed=factors_refreshed,
        factors_failed=factors_failed,
        explanation_generated=explanation_generated,
    )