"""Task 18.11: REST API routes for Market Intelligence.

Exposes the deterministic intelligence services (Tasks 18.2–18.10) through
authenticated REST endpoints. No business logic — only service orchestration.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.repositories.market_intelligence_snapshot import (
    MarketIntelligenceSnapshotRepository,
)
from app.schemas.market_intelligence_api import (
    BreadthResponse,
    ExternalFactorItem,
    ExternalFactorsResponse,
    FactorOut,
    GiftNiftyResponse,
    GlobalMarketItem,
    GlobalMarketsResponse,
    HistoryEntryResponse,
    HistoryResponse,
    IndianIndexItem,
    IndianIndicesResponse,
    InstitutionalInvestorItem,
    InstitutionalResponse,
    MacroEventItem,
    MacroIntelligenceResponse,
    MarketExplanationResponse,
    OverallIntelligenceResponse,
    RefreshResultResponse,
    SectorIntelligenceResponse,
    SectorItem,
)
from app.services.ai_market_explanation import AIMarketExplanationService
from app.services.composite_market_intelligence import CompositeMarketIntelligence
from app.services.external_market_factor_intelligence import (
    ExternalMarketFactorIntelligence,
)
from app.services.gift_nifty_intelligence import GiftNiftyIntelligence
from app.services.global_market_intelligence import GlobalMarketIntelligence
from app.services.index_price_action import IndexPriceAction
from app.services.institutional_intelligence import InstitutionalIntelligence
from app.services.macro_intelligence import MacroIntelligence
from app.services.market_breadth import MarketBreadth
from app.services.sector_intelligence import SectorIntelligence

router = APIRouter(
    prefix="/market-intelligence",
    tags=["Market Intelligence"],
    dependencies=[Depends(require_permission("market_intelligence.read"))],
)

# Maximum history range in days
_MAX_HISTORY_DAYS = 366


# ---------------------------------------------------------------------------
# Service factory helpers
# ---------------------------------------------------------------------------


async def _build_composite(session: AsyncSession) -> CompositeMarketIntelligence:
    """Build the composite intelligence engine with all sub-services."""
    price_repo = MarketIndexPriceRepository(session)
    snapshot_repo = MarketIntelligenceSnapshotRepository(session)
    breadth_engine = MarketBreadth(session)
    sector_engine = SectorIntelligence(session)
    institutional_engine = InstitutionalIntelligence(session)
    external_engine = ExternalMarketFactorIntelligence(session)
    global_engine = GlobalMarketIntelligence(session)
    gift_nifty_engine = GiftNiftyIntelligence(session)
    macro_engine = MacroIntelligence(session)

    return CompositeMarketIntelligence(
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


async def _get_composite_result(
    session: AsyncSession, *, market_date: date | None = None
) -> Any:
    """Evaluate composite intelligence, preferring persisted snapshot."""
    engine = await _build_composite(session)
    if market_date:
        evaluation_at = datetime.combine(market_date, datetime.min.time(), tzinfo=UTC)
    else:
        evaluation_at = datetime.now(UTC)
    return await engine.evaluate(evaluation_at, market_date=market_date)


# ---------------------------------------------------------------------------
# Overall intelligence endpoints
# ---------------------------------------------------------------------------


@router.get("/latest", response_model=OverallIntelligenceResponse)
async def latest_intelligence(
    session: AsyncSession = Depends(get_db_session),
) -> OverallIntelligenceResponse:
    """GET the latest overall market intelligence composite."""
    snapshot_repo = MarketIntelligenceSnapshotRepository(session)
    snapshot = await snapshot_repo.get_latest()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No market intelligence snapshots available")

    result = await _get_composite_result(session, market_date=snapshot.market_date)
    return _composite_to_response(result)


@router.get("/history", response_model=HistoryResponse)
async def intelligence_history(
    start_date: date = Query(description="Start date (YYYY-MM-DD)"),
    end_date: date = Query(description="End date (YYYY-MM-DD)"),
    limit: int = Query(default=100, ge=1, le=_MAX_HISTORY_DAYS),
    session: AsyncSession = Depends(get_db_session),
) -> HistoryResponse:
    """GET bounded historical intelligence snapshots."""
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be on or before end_date")
    if (end_date - start_date).days > _MAX_HISTORY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Date range must not exceed {_MAX_HISTORY_DAYS} days",
        )

    snapshot_repo = MarketIntelligenceSnapshotRepository(session)
    snapshots = await snapshot_repo.list_history(start_date, end_date)
    entries = [
        HistoryEntryResponse(
            id=s.id,
            market_date=s.market_date,
            generated_at=s.generated_at,
            composite_score=float(s.composite_score) if s.composite_score is not None else None,
            confidence_score=float(s.confidence_score) if s.confidence_score is not None else None,
            regime=s.regime,
            price_action_score=(
                float(s.price_action_score)
                if s.price_action_score is not None
                else None
            ),
            breadth_score=float(s.breadth_score) if s.breadth_score is not None else None,
            sector_score=float(s.sector_score) if s.sector_score is not None else None,
            institutional_score=(
                float(s.institutional_score)
                if s.institutional_score is not None
                else None
            ),
            volatility_score=float(s.volatility_score) if s.volatility_score is not None else None,
            global_score=float(s.global_score) if s.global_score is not None else None,
            gift_nifty_score=float(s.gift_nifty_score) if s.gift_nifty_score is not None else None,
            currency_score=float(s.currency_score) if s.currency_score is not None else None,
            crude_score=float(s.crude_score) if s.crude_score is not None else None,
            macro_score=float(s.macro_score) if s.macro_score is not None else None,
            data_completeness_percent=(
                float(s.data_completeness_percent)
                if s.data_completeness_percent is not None
                else None
            ),
            stale_factor_count=s.stale_factor_count,
            missing_factor_count=s.missing_factor_count,
        )
        for s in snapshots[:limit]
    ]
    return HistoryResponse(
        entries=entries,
        start_date=start_date,
        end_date=end_date,
        total=len(entries),
    )


@router.get("/factors", response_model=OverallIntelligenceResponse)
async def factor_breakdown(
    session: AsyncSession = Depends(get_db_session),
) -> OverallIntelligenceResponse:
    """GET the factor breakdown of the latest intelligence."""
    return await latest_intelligence(session)


# ---------------------------------------------------------------------------
# Indian indices
# ---------------------------------------------------------------------------


@router.get("/indian-indices", response_model=IndianIndicesResponse)
async def indian_indices(
    session: AsyncSession = Depends(get_db_session),
) -> IndianIndicesResponse:
    """GET Indian index price-action summaries."""
    from app.market_intelligence.index_catalog import INDEX_CATALOG

    price_repo = MarketIndexPriceRepository(session)
    now = datetime.now(UTC)
    indices = []
    for entry in INDEX_CATALOG:
        if entry.country != "INDIA":
            continue
        code = entry.code
        engine = IndexPriceAction(code, date.today())
        engine.set_repo(price_repo)
        try:
            basic = await engine.basic_price()
            returns = await engine.returns()
            trend_result = await engine.trend()
            score_result = await engine.price_action_score()
            latest_close = basic.get("close")
            change_pct = basic.get("change_percent")
        except Exception:
            latest_close = None
            change_pct = None
            returns = {}
            trend_result = {}
            score_result = {}

        indices.append(
            IndianIndexItem(
                index_code=code,
                name=entry.name,
                latest_close=latest_close,
                daily_change_percent=change_pct,
                return_1d=returns.get(1),
                return_5d=returns.get(5),
                return_20d=returns.get(20),
                trend=trend_result.get("trend"),
                price_action_score=score_result.get("price_action_score"),
                freshness="recent" if latest_close is not None else "missing",
            )
        )
    return IndianIndicesResponse(
        market_date=date.today(),
        indices=indices,
        evaluation_at=now,
    )


# ---------------------------------------------------------------------------
# Global / Asian markets
# ---------------------------------------------------------------------------


@router.get("/global", response_model=GlobalMarketsResponse)
async def global_markets(
    session: AsyncSession = Depends(get_db_session),
) -> GlobalMarketsResponse:
    """GET global and Asian market intelligence."""
    engine = GlobalMarketIntelligence(session)
    result = await engine.evaluate(datetime.now(UTC))
    markets = {}
    for code, data in result.get("markets", {}).items():
        markets[code] = GlobalMarketItem(
            index_code=code,
            market_date=data.get("market_date"),
            close=data.get("close") if isinstance(data.get("close"), (int, float)) else None,
            returns=data.get("returns", {}),
            trend=data.get("trend"),
            price_action_score=data.get("price_action_score"),
            score=data.get("score"),
            direction=data.get("direction"),
            freshness=data.get("freshness", "missing"),
        )
    return GlobalMarketsResponse(
        evaluation_at=result.get("evaluation_at"),
        global_score=result.get("global_score"),
        global_direction=result.get("global_direction"),
        us_context=result.get("us_context", {}),
        asian_context=result.get("asian_context", {}),
        markets=markets,
        completeness=result.get("completeness", 0.0),
        confidence=result.get("confidence", 0.0),
    )


# ---------------------------------------------------------------------------
# GIFT Nifty
# ---------------------------------------------------------------------------


@router.get("/gift-nifty", response_model=GiftNiftyResponse)
async def gift_nifty(
    session: AsyncSession = Depends(get_db_session),
) -> GiftNiftyResponse:
    """GET GIFT Nifty intelligence."""
    engine = GiftNiftyIntelligence(session)
    result = await engine.evaluate(datetime.now(UTC))
    return GiftNiftyResponse(
        evaluation_at=result.get("evaluation_at"),
        gift_nifty_score=result.get("gift_nifty_score", 0.0),
        confidence_score=result.get("confidence_score", 0.0),
        data_completeness_percent=result.get("data_completeness_percent", 0.0),
        direction=result.get("direction"),
        indicated_gap_points=result.get("indicated_gap_points"),
        indicated_gap_percent=result.get("indicated_gap_percent"),
        gift_observation=result.get("gift_observation"),
        nifty_reference=result.get("nifty_reference"),
        fallback_level=result.get("fallback_level", "none"),
        freshness="recent" if result.get("gift_observation") else "missing",
    )


# ---------------------------------------------------------------------------
# Institutional (FII/DII)
# ---------------------------------------------------------------------------


@router.get("/institutional", response_model=InstitutionalResponse)
async def institutional(
    session: AsyncSession = Depends(get_db_session),
) -> InstitutionalResponse:
    """GET FII/DII institutional intelligence."""
    engine = InstitutionalIntelligence(session)
    result = await engine.evaluate(datetime.now(UTC))
    investors = {}
    for inv_type, inv_data in result.get("investors", {}).items():
        investors[inv_type] = InstitutionalInvestorItem(
            investor_type=inv_type,
            buy_value=inv_data.get("buy_value"),
            sell_value=inv_data.get("sell_value"),
            net_value=inv_data.get("net_value"),
            score=inv_data.get("score"),
            direction=inv_data.get("direction"),
            freshness=inv_data.get("freshness", "missing"),
            confidence=inv_data.get("confidence"),
        )
    return InstitutionalResponse(
        evaluation_at=result.get("evaluation_at"),
        institutional_score=result.get("institutional_score"),
        direction=result.get("direction"),
        freshness=result.get("freshness", "missing"),
        confidence=result.get("confidence"),
        completeness=result.get("completeness"),
        investors=investors,
        combined=result.get("combined", {}),
    )


# ---------------------------------------------------------------------------
# External factors (VIX, USD/INR, Brent)
# ---------------------------------------------------------------------------


@router.get("/external-factors", response_model=ExternalFactorsResponse)
async def external_factors(
    session: AsyncSession = Depends(get_db_session),
) -> ExternalFactorsResponse:
    """GET India VIX, USD/INR, and Brent crude intelligence."""
    engine = ExternalMarketFactorIntelligence(session)
    result = await engine.evaluate(datetime.now(UTC))
    factors = result.get("factors", {})

    def _to_item(data: dict[str, Any] | None) -> ExternalFactorItem | None:
        if data is None:
            return None
        return ExternalFactorItem(
            index_code=data.get("index_code", ""),
            latest=data.get("latest"),
            score=data.get("score"),
            direction=data.get("direction"),
            freshness=data.get("freshness", "missing"),
            confidence=data.get("confidence"),
            returns=data.get("returns", {}),
            regime=data.get("regime"),
            trend=data.get("trend"),
        )

    return ExternalFactorsResponse(
        evaluation_at=result.get("evaluation_at"),
        completeness=result.get("completeness", 0.0),
        confidence=result.get("confidence", 0.0),
        freshness=result.get("freshness", "missing"),
        india_vix=_to_item(factors.get("india_vix")),
        usd_inr=_to_item(factors.get("usd_inr")),
        brent_crude=_to_item(factors.get("brent_crude")),
    )


# ---------------------------------------------------------------------------
# Market breadth
# ---------------------------------------------------------------------------


@router.get("/breadth", response_model=BreadthResponse)
async def breadth(
    session: AsyncSession = Depends(get_db_session),
) -> BreadthResponse:
    """GET market breadth intelligence."""
    engine = MarketBreadth(session)
    result = await engine.evaluate(date.today(), evaluation_time=datetime.now(UTC))
    sma = result.get("sma_breadth", {})
    hl = result.get("high_low_breadth", {})
    return BreadthResponse(
        market_date=result.get("market_date"),
        evaluation_time=result.get("evaluation_time"),
        breadth_score=result.get("breadth_score"),
        breadth_state=result.get("breadth_state"),
        breadth_direction=result.get("breadth_direction"),
        advancing_count=result.get("advancing_count"),
        declining_count=result.get("declining_count"),
        unchanged_count=result.get("unchanged_count"),
        advance_decline_ratio=result.get("advance_decline_ratio"),
        net_advances=result.get("net_advances", 0),
        participation_score=result.get("participation_score"),
        breadth_momentum_score=result.get("breadth_momentum_score"),
        breadth_momentum_direction=result.get("breadth_momentum_direction"),
        sma_breadth=sma,
        high_low_breadth=hl,
        divergence=result.get("divergence", {}),
        completeness=result.get("completeness", 0.0),
        confidence=result.get("confidence", 0.0),
        freshness=result.get("freshness", "missing"),
    )


# ---------------------------------------------------------------------------
# Sectors
# ---------------------------------------------------------------------------


@router.get("/sectors", response_model=SectorIntelligenceResponse)
async def sectors(
    session: AsyncSession = Depends(get_db_session),
) -> SectorIntelligenceResponse:
    """GET sector intelligence."""

    engine = SectorIntelligence(session)
    result = await engine.evaluate(date.today())

    def _sector_item(s: dict[str, Any]) -> SectorItem:
        return SectorItem(
            code=s.get("code", ""),
            name=s.get("name"),
            group=s.get("group"),
            score=s.get("score"),
            direction=s.get("direction"),
            returns=s.get("returns", {}),
            momentum=s.get("momentum"),
            relative_strength=s.get("relative_strength"),
            freshness=s.get("freshness", "missing"),
        )

    return SectorIntelligenceResponse(
        market_date=result.get("market_date"),
        sector_intelligence_score=result.get("sector_intelligence_score"),
        direction=result.get("direction"),
        sector_count=result.get("sector_count", 0),
        available_sector_count=result.get("available_sector_count", 0),
        leaders=[_sector_item(s) for s in result.get("leaders", [])],
        laggards=[_sector_item(s) for s in result.get("laggards", [])],
        cyclical_score=result.get("cyclical_score"),
        defensive_score=result.get("defensive_score"),
        rotation=result.get("rotation", {}),
        completeness_percent=result.get("completeness_percent", 0.0),
        confidence_score=result.get("confidence_score", 0.0),
        freshness=result.get("freshness", "missing"),
    )


# ---------------------------------------------------------------------------
# Macro / Events
# ---------------------------------------------------------------------------


@router.get("/macro", response_model=MacroIntelligenceResponse)
async def macro(
    session: AsyncSession = Depends(get_db_session),
) -> MacroIntelligenceResponse:
    """GET macro and event intelligence."""
    engine = MacroIntelligence(session)
    result = await engine.evaluate(datetime.now(UTC))
    upcoming = []
    for ev in result.get("upcoming_events", []):
        upcoming.append(
            MacroEventItem(
                event_code=ev.get("event_code", ""),
                name=ev.get("name"),
                category=ev.get("category"),
                importance=ev.get("importance"),
                actual=ev.get("actual"),
                forecast=ev.get("forecast"),
                previous=ev.get("previous"),
                surprise=ev.get("surprise"),
                lifecycle=ev.get("lifecycle"),
                time_to_event=ev.get("time_to_event"),
                freshness_status=ev.get("freshness_status"),
            )
        )
    return MacroIntelligenceResponse(
        evaluation_at=result.get("evaluation_at"),
        macro_score=result.get("macro_score"),
        direction=result.get("direction"),
        freshness=result.get("freshness", "missing"),
        confidence=result.get("confidence", 0.0),
        completeness=result.get("completeness", 0.0),
        domains=result.get("domains", {}),
        upcoming_events=upcoming,
        upcoming_risk=result.get("upcoming_risk", 0.0),
    )


# ---------------------------------------------------------------------------
# AI Explanation
# ---------------------------------------------------------------------------


@router.get("/explanation", response_model=MarketExplanationResponse)
async def explanation(
    session: AsyncSession = Depends(get_db_session),
) -> MarketExplanationResponse:
    """GET the AI market explanation (deterministic fallback if no AI provider)."""
    result = await _get_composite_result(session)
    service = AIMarketExplanationService(result)
    explanation = service.generate_explanation()
    return MarketExplanationResponse(
        market_date=explanation.market_date,
        evaluation_at=explanation.evaluation_at,
        regime=explanation.regime,
        composite_score=explanation.composite_score,
        confidence_score=explanation.confidence_score,
        headline=explanation.headline,
        summary=explanation.summary,
        bullish_factors=explanation.bullish_factors,
        bearish_factors=explanation.bearish_factors,
        risk_factors=explanation.risk_factors,
        missing_data=explanation.missing_data,
        stale_data=explanation.stale_data,
        contradictions=explanation.contradictions,
        watch_items=explanation.watch_items,
        provider=explanation.provider,
        model=explanation.model,
        generated_at=explanation.generated_at,
        is_ai_generated=explanation.is_ai_generated,
        fallback_used=explanation.fallback_used,
    )


# ---------------------------------------------------------------------------
# Manual refresh
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=RefreshResultResponse,
    dependencies=[Depends(require_permission("market_intelligence.refresh"))],
)
async def refresh_intelligence(
    session: AsyncSession = Depends(get_db_session),
) -> RefreshResultResponse:
    """POST to manually refresh market intelligence (requires refresh permission)."""
    engine = await _build_composite(session)
    now = datetime.now(UTC)
    result = await engine.evaluate(now, persistence=True)
    return RefreshResultResponse(
        status="ok",
        message="Market intelligence refreshed successfully",
        evaluation_at=now,
        composite_score=result.composite_score,
        regime=result.regime,
        factors_refreshed=[f.name for f in result.factors if f.score is not None],
        factors_failed=[f.name for f in result.factors if f.score is None],
    )


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _composite_to_response(result: Any) -> OverallIntelligenceResponse:
    """Convert CompositeMarketIntelligenceResult to API response."""
    return OverallIntelligenceResponse(
        evaluation_at=result.evaluation_time,
        market_date=result.market_date,
        regime=result.regime,
        composite_score=result.composite_score,
        confidence_score=result.confidence_score,
        confidence_state=result.confidence_state,
        data_completeness_percent=result.data_completeness_percent,
        agreement_percent=result.agreement_percent,
        factors=[FactorOut.model_validate(f) for f in result.factors],
        bullish_factors=[FactorOut.model_validate(f) for f in result.bullish_factors],
        bearish_factors=[FactorOut.model_validate(f) for f in result.bearish_factors],
        neutral_factors=[FactorOut.model_validate(f) for f in result.neutral_factors],
        missing_factors=[FactorOut.model_validate(f) for f in result.missing_factors],
        stale_factors=[FactorOut.model_validate(f) for f in result.stale_factors],
        contradictions=result.contradictions,
        risk_factors=result.risk_factors,
        factor_contributions=result.factor_contributions,
        uncertain=result.uncertain,
        high_volatility=result.high_volatility,
        provenance_summary=result.provenance_summary,
        snapshot_id=result.snapshot_id,
    )
