from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.repositories.stock import StockRepository
from app.schemas.stock import StockCreate, StockRead
from app.schemas.what_changed import WhatChangedResult
from app.schemas.what_changed_synthesis import CrossDomainSynthesisResult
from app.services.stock import StockService
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

