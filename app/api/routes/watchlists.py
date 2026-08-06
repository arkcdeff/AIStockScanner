from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistDetailRead,
    WatchlistRead,
    WatchlistStockCreate,
    WatchlistStockRead,
    WatchlistStockUpdate,
    WatchlistStockWithPriceRead,
    WatchlistUpdate,
)
from app.services.watchlist import WatchlistError, WatchlistService

router = APIRouter(
    prefix="/watchlists",
    tags=["Watchlists"],
    dependencies=[Depends(require_permission("watchlists.read"))],
)
WRITE_DEPENDENCIES = [Depends(require_permission("watchlists.write"))]


@router.get("/", response_model=list[WatchlistRead])
async def list_watchlists(
    session: AsyncSession = Depends(get_db_session),
) -> list[WatchlistRead]:
    service = WatchlistService(session)
    items: list[WatchlistRead] = []
    for watchlist in await service.list():
        item = WatchlistRead.model_validate(watchlist)
        item.stock_count = len(watchlist.stocks)
        items.append(item)
    return items


@router.post(
    "/", response_model=WatchlistRead, dependencies=WRITE_DEPENDENCIES
)
async def create_watchlist(
    payload: WatchlistCreate,
    session: AsyncSession = Depends(get_db_session),
) -> WatchlistRead:
    service = WatchlistService(session)
    try:
        watchlist = await service.create(payload.model_dump())
    except WatchlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WatchlistRead.model_validate(watchlist)


@router.get("/search", response_model=list[dict[str, object]])
async def search_stocks(
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, object]]:
    service = WatchlistService(session)
    stocks = await service.search_stocks(q, limit=limit)
    return [
        {"id": stock.id, "symbol": stock.symbol, "company_name": stock.company_name}
        for stock in stocks
    ]


@router.get("/{watchlist_id}", response_model=WatchlistDetailRead)
async def get_watchlist(
    watchlist_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> WatchlistDetailRead:
    service = WatchlistService(session)
    watchlist = await service.get(watchlist_id)
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    valued = await service.list_stocks(watchlist_id)
    base = WatchlistRead.model_validate(watchlist)
    item = WatchlistDetailRead(
        id=base.id,
        name=base.name,
        description=base.description,
        color=base.color,
        icon=base.icon,
        sort_order=base.sort_order,
        is_favorite=base.is_favorite,
        is_default=base.is_default,
        created_at=base.created_at,
        updated_at=base.updated_at,
        stock_count=len(watchlist.stocks),
        stocks=[
            WatchlistStockWithPriceRead(
                id=row["entry"].id,
                watchlist_id=watchlist_id,
                stock_id=row["entry"].stock_id,
                notes=row["entry"].notes,
                priority=row["entry"].priority,
                added_at=row["entry"].added_at,
                symbol=row["stock"].symbol,
                company_name=row["stock"].company_name,
                latest_close=(
                    float(row["latest_price"].close) if row["latest_price"] is not None else None
                ),
                trade_date=(
                    row["latest_price"].trade_date if row["latest_price"] is not None else None
                ),
            )
            for row in valued
        ],
    )
    return item


@router.put(
    "/{watchlist_id}", response_model=WatchlistRead, dependencies=WRITE_DEPENDENCIES
)
async def update_watchlist(
    watchlist_id: int,
    payload: WatchlistUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> WatchlistRead:
    service = WatchlistService(session)
    try:
        watchlist = await service.update(watchlist_id, payload.model_dump(exclude_unset=True))
    except WatchlistError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WatchlistRead.model_validate(watchlist)


@router.delete("/{watchlist_id}", status_code=204, dependencies=WRITE_DEPENDENCIES)
async def delete_watchlist(
    watchlist_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    service = WatchlistService(session)
    try:
        await service.delete(watchlist_id)
    except WatchlistError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{watchlist_id}/stocks", response_model=list[WatchlistStockWithPriceRead])
async def list_watchlist_stocks(
    watchlist_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> list[WatchlistStockWithPriceRead]:
    service = WatchlistService(session)
    if await service.get(watchlist_id) is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    valued = await service.list_stocks(watchlist_id)
    return [
        WatchlistStockWithPriceRead(
            id=row["entry"].id,
            watchlist_id=watchlist_id,
            stock_id=row["entry"].stock_id,
            notes=row["entry"].notes,
            priority=row["entry"].priority,
            added_at=row["entry"].added_at,
            symbol=row["stock"].symbol,
            company_name=row["stock"].company_name,
            latest_close=(
                float(row["latest_price"].close) if row["latest_price"] is not None else None
            ),
            trade_date=row["latest_price"].trade_date if row["latest_price"] is not None else None,
        )
        for row in valued
    ]


@router.post(
    "/{watchlist_id}/stocks", response_model=WatchlistStockRead, dependencies=WRITE_DEPENDENCIES
)
async def add_watchlist_stock(
    watchlist_id: int,
    payload: WatchlistStockCreate,
    session: AsyncSession = Depends(get_db_session),
) -> WatchlistStockRead:
    service = WatchlistService(session)
    try:
        entry = await service.add_stock(
            watchlist_id, payload.stock_id, notes=payload.notes, priority=payload.priority
        )
    except WatchlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WatchlistStockRead.model_validate(entry)


@router.patch(
    "/{watchlist_id}/stocks/{stock_id}",
    response_model=WatchlistStockRead,
    dependencies=WRITE_DEPENDENCIES,
)
async def update_watchlist_stock(
    watchlist_id: int,
    stock_id: int,
    payload: WatchlistStockUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> WatchlistStockRead:
    service = WatchlistService(session)
    try:
        entry = await service.update_stock(
            watchlist_id, stock_id, notes=payload.notes, priority=payload.priority
        )
    except WatchlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return WatchlistStockRead.model_validate(entry)


@router.delete(
    "/{watchlist_id}/stocks/{stock_id}",
    status_code=204,
    dependencies=WRITE_DEPENDENCIES,
)
async def remove_watchlist_stock(
    watchlist_id: int,
    stock_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    service = WatchlistService(session)
    try:
        await service.remove_stock(watchlist_id, stock_id)
    except WatchlistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
