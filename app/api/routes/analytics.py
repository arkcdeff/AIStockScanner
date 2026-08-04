from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.daily_price import DailyPrice
from app.models.technical_analysis import TechnicalAnalysis
from app.schemas.analytics import AnalyticsListItemRead, TechnicalAnalysisRead
from app.services.analytics_engine import AnalyticsEngine

router = APIRouter(prefix="/analytics", tags=["Analytics"])


async def _enrich(
    session: AsyncSession, rows: list[TechnicalAnalysis]
) -> list[AnalyticsListItemRead]:
    if not rows:
        return []
    stock_ids = [row.stock_id for row in rows]
    price_rows = (
        await session.execute(
            select(
                DailyPrice.stock_id,
                DailyPrice.close,
                DailyPrice.trade_date,
            ).where(
                DailyPrice.stock_id.in_(stock_ids),
                DailyPrice.trade_date.in_(
                    select(TechnicalAnalysis.trade_date).where(
                        TechnicalAnalysis.stock_id.in_(stock_ids)
                    )
                ),
            )
        )
    ).fetchall()
    price_map: dict[tuple[int, object], float] = {
        (stock_id, trade_date): float(close) for stock_id, close, trade_date in price_rows
    }

    items: list[AnalyticsListItemRead] = []
    for row in rows:
        stock = row.stock
        close = price_map.get((row.stock_id, row.trade_date))
        items.append(
            AnalyticsListItemRead(
                **TechnicalAnalysisRead.model_validate(row).model_dump(),
                symbol=stock.symbol,
                company_name=stock.company_name,
                latest_close=close,
            )
        )
    return items


@router.get("/top-rated", response_model=list[AnalyticsListItemRead])
async def top_rated(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
) -> list[AnalyticsListItemRead]:
    rows = await AnalyticsEngine(session).top_rated(limit=limit)
    return await _enrich(session, rows)


@router.get("/trending", response_model=list[AnalyticsListItemRead])
async def trending(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
) -> list[AnalyticsListItemRead]:
    rows = await AnalyticsEngine(session).trending(limit=limit)
    return await _enrich(session, rows)


@router.get("/breakouts", response_model=list[AnalyticsListItemRead])
async def breakouts(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
) -> list[AnalyticsListItemRead]:
    rows = await AnalyticsEngine(session).breakouts(limit=limit)
    return await _enrich(session, rows)


@router.get("/{symbol}", response_model=AnalyticsListItemRead)
async def get_symbol_analytics(
    symbol: str,
    session: AsyncSession = Depends(get_db_session),
) -> AnalyticsListItemRead:
    analysis = await AnalyticsEngine(session).get_analytics(symbol)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"No analytics found for {symbol}")
    items = await _enrich(session, [analysis])
    return items[0]
