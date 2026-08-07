from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(
    dependencies=[Depends(require_browser_permission("stocks.read"))]
)
WRITE_DEPENDENCIES = [Depends(require_browser_permission("market_data.write"))]


async def _status(session: AsyncSession) -> dict:
    total = (await session.execute(select(func.count(IndicatorResult.id)))).scalar_one()
    distinct_stocks = (
        await session.execute(
            select(func.count(func.distinct(IndicatorResult.stock_id)))
        )
    ).scalar_one()
    latest = (
        await session.execute(
            select(func.max(IndicatorResult.trade_date))
        )
    ).scalar_one_or_none()
    stock_count = (await session.execute(select(func.count(Stock.id)))).scalar_one()
    return {
        "total_indicator_rows": int(total),
        "stocks_with_indicators": int(distinct_stocks),
        "latest_trade_date": latest,
        "total_stocks": int(stock_count),
    }


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def indicators_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    status = await _status(session)
    html = _render(
        "indicators.html",
        {
            "request": request,
            "status": status,
        },
    )
    return HTMLResponse(content=html)


@router.post(
    "/calculate",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def calculate_indicators(
    request: Request,
    symbol: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.indicator_engine import IndicatorEngine, IndicatorEngineError

    engine = IndicatorEngine(session)
    message = ""
    level = "info"
    try:
        if symbol:
            results = await engine.calculate_for_stock(symbol)
            message = f"Calculated {len(results)} indicator rows for {symbol.upper()}"
        else:
            summary = await engine.calculate_all()
            message = (
                f"Batch calculation completed: {summary['calculated']} calculated, "
                f"{summary['skipped']} skipped"
            )
        level = "success"
    except IndicatorEngineError as exc:
        message = str(exc)
        level = "danger"
    return RedirectResponse(
        url=f"/indicators?flash={level}&message={message}", status_code=303
    )


@router.get("/status", response_class=HTMLResponse)
async def indicators_status_partial(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    html = _render(
        "components/indicator_status_cards.html",
        {"request": request, "status": await _status(session)},
    )
    return HTMLResponse(content=html)