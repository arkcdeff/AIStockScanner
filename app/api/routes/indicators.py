from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_permission
from app.db.session import get_db_session
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.schemas.indicator import IndicatorCalculateResponse
from app.services.indicator_engine import IndicatorEngine, IndicatorEngineError

router = APIRouter(
    prefix="/indicators",
    tags=["Indicators"],
    dependencies=[Depends(require_permission("stocks.read"))],
)


@router.post(
    "/calculate",
    response_model=IndicatorCalculateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("market_data.write"))],
)
async def calculate_indicators(
    symbol: str | None = Query(default=None, min_length=1, max_length=32),
    session: AsyncSession = Depends(get_db_session),
) -> IndicatorCalculateResponse:
    engine = IndicatorEngine(session)
    if symbol:
        try:
            results = await engine.calculate_for_stock(symbol)
            return IndicatorCalculateResponse(
                symbol=symbol.upper(),
                calculated=len(results),
                skipped=0,
                message=f"Calculated {len(results)} indicator rows",
            )
        except IndicatorEngineError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    else:
        summary = await engine.calculate_all()
        return IndicatorCalculateResponse(
            symbol="ALL",
            calculated=summary["calculated"],
            skipped=summary["skipped"],
            message="Batch calculation completed",
        )


@router.get("/status", response_model=dict[str, Any])
async def indicator_status(
    symbol: str | None = Query(default=None, min_length=1, max_length=32),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if symbol:
        stock = (
            await session.execute(select(Stock).where(Stock.symbol == symbol.upper()))
        ).scalar_one_or_none()
        if not stock:
            raise HTTPException(status_code=404, detail="Stock not found")

        count = (
            await session.execute(
                select(func.count(IndicatorResult.id)).where(IndicatorResult.stock_id == stock.id)
            )
        ).scalar_one()
        latest = (
            await session.execute(
                select(IndicatorResult.trade_date)
                .where(IndicatorResult.stock_id == stock.id)
                .order_by(IndicatorResult.trade_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        return {
            "symbol": stock.symbol,
            "indicator_rows": int(count),
            "latest_trade_date": latest,
        }

    total = (await session.execute(select(func.count(IndicatorResult.id)))).scalar_one()
    distinct_stocks = (
        await session.execute(select(func.count(func.distinct(IndicatorResult.stock_id))))
    ).scalar_one()
    return {
        "total_indicator_rows": int(total),
        "stocks_with_indicators": int(distinct_stocks),
    }
