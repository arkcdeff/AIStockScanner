from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.repositories.stock import StockRepository
from app.repositories.stock_detail import StockDetailRepository
from app.schemas.stock import StockCreate, StockRead
from app.schemas.stock_detail import ChartDataResponse
from app.services.stock import StockService

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


@router.get("/{symbol}/chart-data", response_model=ChartDataResponse)
async def stock_chart_data(
    symbol: str,
    period: str = Query(default="1y", pattern="^(1m|3m|6m|1y|3y|5y|all)$"),
    include_indicators: bool = Query(default=True),
    session: AsyncSession = Depends(get_db_session),
) -> ChartDataResponse:
    repo = StockDetailRepository(session)
    stock = await repo.get_stock(symbol)
    if not stock:
        raise HTTPException(status_code=404, detail="Stock not found")

    actual_period = "all" if period == "all" else period
    data = await repo.get_chart_data(
        stock.id, period=actual_period, include_indicators=include_indicators
    )
    return ChartDataResponse(symbol=symbol.upper(), **data)
