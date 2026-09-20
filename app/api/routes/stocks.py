from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session, get_session_factory
from app.repositories.stock import StockRepository
from app.schemas.stock import StockCreate, StockRead
from app.schemas.stock_decision_context import StockDecisionContextRead
from app.schemas.what_changed import WhatChangedResult
from app.schemas.what_changed_interpretation import StockIntelligenceInterpretation
from app.schemas.what_changed_screening import ScreeningRequest, ScreeningResponse
from app.schemas.what_changed_synthesis import CrossDomainSynthesisResult
from app.services.stock import StockService
from app.services.what_changed_interpretation_service import (
    WhatChangedInterpretationService,
)
from app.services.what_changed_screening_service import WhatChangedScreeningService
from app.services.what_changed_service import WhatChangedService
from app.services.what_changed_synthesis_service import WhatChangedSynthesisService

router = APIRouter(
    prefix="/stocks",
    tags=["Stocks"],
    dependencies=[Depends(require_permission("stocks.read"))],
)


@router.get("", response_model=list[StockRead])
async def list_stocks(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[StockRead]:
    stocks = await StockRepository(session).list(offset=offset, limit=limit)
    return [StockRead.model_validate(stock) for stock in stocks]


@router.post(
    "",
    response_model=StockRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("stocks.write"))],
)
async def create_stock(
    payload: StockCreate,
    session: AsyncSession = Depends(get_db_session),
) -> StockRead:
    try:
        stock = await StockService(session).create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return StockRead.model_validate(stock)


# ---------------------------------------------------------------------------
# G.11-D-4 — What-Changed Multi-Stock Universe Screener
# NOTE: Must be defined before /{symbol}/what-changed to prevent FastAPI from
# matching "screening" as the {symbol} path parameter.
# ---------------------------------------------------------------------------


@router.post(
    "/screening/what-changed",
    response_model=ScreeningResponse,
    summary="Screen a stock universe using What-Changed evidence interpretation",
    description=(
        "G.11-D-4: Stateless, on-demand, PIT-safe screener. "
        "Resolves the requested universe, fans out D-3 interpret() calls with "
        "bounded concurrency (max 5 workers), and returns filtered results. "
        "No AI narrative is included at this layer. "
        "Evidence descriptions — never trading recommendations."
    ),
)
async def screen_what_changed(
    request: ScreeningRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningResponse:
    """POST body: :class:`ScreeningRequest`.

    Returns :class:`ScreeningResponse` with per-symbol evidence summaries
    and aggregate run statistics.
    """
    try:
        svc = WhatChangedScreeningService(
            session=session,
            session_factory=get_session_factory(),
        )
        return await svc.screen(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/screening/what-changed",
    response_model=ScreeningResponse,
    summary="Screen a predefined universe using What-Changed evidence interpretation (GET convenience)",
    description=(
        "G.11-D-4: GET convenience endpoint for simple universe screenings. "
        "For advanced filter criteria use the POST endpoint. "
        "Evidence descriptions — never trading recommendations."
    ),
)
async def screen_what_changed_get(
    universe: str = Query(
        default="NIFTY_50",
        description="Predefined universe code (e.g. NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP_100, NIFTY_BANK, FNO, WATCHLIST)",
    ),
    watchlist_id: int | None = Query(
        default=None,
        description="Watchlist ID — required when universe=WATCHLIST",
    ),
    as_of_date: date | None = Query(
        default=None,
        description="Point-in-time evaluation date (defaults to latest available)",
    ),
    comparison_window: int = Query(
        default=1,
        ge=1,
        le=30,
        description="Trading-day look-back window (default 1)",
    ),
    include_non_matched: bool = Query(
        default=False,
        description="Include NO_MATCH / NO_DATA / ERROR results in response",
    ),
    max_results: int | None = Query(
        default=None,
        ge=1,
        le=500,
        description="Truncate matched results to this count",
    ),
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningResponse:
    """GET convenience screening with predefined universe and no filter criteria.

    For custom filter criteria (overall_states, directional_biases, convergence),
    use the POST endpoint with a :class:`ScreeningRequest` body.
    """
    from app.services.what_changed_screening_config import ScreeningUniverse

    try:
        universe_enum = ScreeningUniverse(universe.upper())
    except ValueError as exc:
        valid = ", ".join(e.value for e in ScreeningUniverse)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid universe '{universe}'. Valid values: {valid}",
        ) from exc

    req = ScreeningRequest(
        universe=universe_enum,
        watchlist_id=watchlist_id,
        as_of_date=as_of_date,
        comparison_window=comparison_window,
        include_non_matched=include_non_matched,
        max_results=max_results,
    )

    try:
        svc = WhatChangedScreeningService(
            session=session,
            session_factory=get_session_factory(),
        )
        return await svc.screen(req)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Per-symbol endpoints (G.11-D-1, G.11-D-2, G.11-D-3)
# ---------------------------------------------------------------------------


@router.get("/{symbol}/what-changed", response_model=WhatChangedResult)
async def get_what_changed(
    symbol: str,
    evaluation_date: date | None = Query(default=None, description="Evaluation date (defaults to latest available)"),
    comparison_window: int = Query(default=1, ge=1, le=20, description="Comparison window in trading sessions (default 1)"),
    session: AsyncSession = Depends(get_db_session),
) -> WhatChangedResult:
    service = WhatChangedService(session)
    try:
        return await service.detect_changes(
            symbol=symbol,
            evaluation_date=evaluation_date,
            comparison_window=comparison_window,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{symbol}/what-changed/synthesis", response_model=CrossDomainSynthesisResult)
async def get_what_changed_synthesis(
    symbol: str,
    evaluation_date: date | None = Query(default=None, description="Evaluation date (defaults to latest available)"),
    comparison_window: int = Query(default=1, ge=1, le=20, description="Comparison window in trading sessions (default 1)"),
    session: AsyncSession = Depends(get_db_session),
) -> CrossDomainSynthesisResult:
    service = WhatChangedSynthesisService(session)
    try:
        return await service.synthesize(
            symbol=symbol,
            evaluation_date=evaluation_date,
            comparison_window=comparison_window,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{symbol}/what-changed/interpretation",
    response_model=StockIntelligenceInterpretation,
)
async def get_what_changed_interpretation(
    symbol: str,
    evaluation_date: date | None = Query(default=None, description="Evaluation date (defaults to latest available)"),
    comparison_window: int = Query(default=1, ge=1, le=20, description="Comparison window in trading sessions (default 1)"),
    session: AsyncSession = Depends(get_db_session),
) -> StockIntelligenceInterpretation:
    """Return the deterministic G.11-D-3 stock intelligence interpretation.

    Evidence → Interpretation → Narrative. The result is deterministic
    evidence interpretation — never a trading recommendation.
    """
    service = WhatChangedInterpretationService(session)
    try:
        return await service.interpret(
            symbol=symbol,
            evaluation_date=evaluation_date,
            comparison_window=comparison_window,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{symbol}/decision-context",
    response_model=StockDecisionContextRead,
)
async def get_stock_decision_context(
    symbol: str,
    evaluation_date: date | None = Query(default=None, description="PIT evaluation date (defaults to latest available)"),
    as_of_date: date | None = Query(default=None, description="PIT observation cutoff date (defaults to evaluation date)"),
    comparison_window: int = Query(default=1, ge=1, le=30, description="Comparison window in trading sessions (default 1)"),
    include_ai: bool = Query(default=False, description="Whether to request optional AI narrative"),
    session: AsyncSession = Depends(get_db_session),
) -> StockDecisionContextRead:
    """Return the deterministic G.11-D-5 Stock 360 & Decision Context Fusion research dossier.

    Combines:
      - Certified G.11-D-3 What-Changed Interpretation (and D-2/D-1 evidence)
      - Stock 360° PIT-safe 10-dimensional research snapshot
      - Canonical Opportunity Profiles alignment & veto compilation
    """
    from app.services.stock_decision_context_service import StockDecisionContextFusionService

    service = StockDecisionContextFusionService(session)
    try:
        return await service.get_decision_context(
            symbol=symbol,
            evaluation_date=evaluation_date,
            as_of_date=as_of_date,
            comparison_window=comparison_window,
            include_ai=include_ai,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
