from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.repositories.stock import StockRepository
from app.schemas.stock import StockCreate, StockRead
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
