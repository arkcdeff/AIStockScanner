from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.explanation_service import AIExplanationService, AINotFoundError
from app.core.config import settings
from app.db.session import get_db_session
from app.schemas.ai import (
    AICollectionExplanationResponse,
    AIMarketSummaryResponse,
    AIStockExplanationResponse,
)

router = APIRouter(prefix="/ai", tags=["AI"])


def _require_enabled() -> None:
    if not settings.ai_enabled:
        raise HTTPException(status_code=503, detail="AI features are disabled")


@router.get("/stock/{symbol}", response_model=AIStockExplanationResponse)
async def explain_stock(
    symbol: str,
    session: AsyncSession = Depends(get_db_session),
) -> AIStockExplanationResponse:
    _require_enabled()
    service = AIExplanationService(session)
    try:
        return await service.explain_stock(symbol)
    except AINotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/top-rated", response_model=AICollectionExplanationResponse)
async def top_rated_ai(
    limit: int = Query(default=5, ge=1, le=20),
    session: AsyncSession = Depends(get_db_session),
) -> AICollectionExplanationResponse:
    _require_enabled()
    service = AIExplanationService(session)
    return await service.explain_top_rated(limit=limit)


@router.get("/market-summary", response_model=AIMarketSummaryResponse)
async def market_summary(
    limit: int = Query(default=8, ge=1, le=20),
    session: AsyncSession = Depends(get_db_session),
) -> AIMarketSummaryResponse:
    _require_enabled()
    service = AIExplanationService(session)
    return await service.explain_market_summary(limit=limit)
