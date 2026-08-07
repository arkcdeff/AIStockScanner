from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.models.daily_price import DailyPrice
from app.models.technical_analysis import TechnicalAnalysis
from app.services.analytics_engine import AnalyticsEngine


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(
    dependencies=[Depends(require_browser_permission("analytics.read"))]
)


async def _list_items(
    session: AsyncSession,
    rows: list[TechnicalAnalysis],
) -> list[dict]:
    items: list[dict] = []
    for row in rows:
        close = (
            await session.execute(
                select(DailyPrice.close).where(
                    DailyPrice.stock_id == row.stock_id,
                    DailyPrice.trade_date == row.trade_date,
                )
            )
        ).scalar_one_or_none()
        items.append(
            {
                "symbol": row.stock.symbol,
                "company_name": row.stock.company_name,
                "trade_date": row.trade_date,
                "latest_close": float(close) if close is not None else None,
                "overall_score": row.overall_score,
                "trend_score": row.trend_score,
                "momentum_score": row.momentum_score,
                "volume_score": row.volume_score,
                "delivery_score": row.delivery_score,
                "trend_rating": row.trend_rating,
                "is_trending": row.is_trending,
                "is_breakout": row.is_breakout,
                "support": row.support,
                "resistance": row.resistance,
            }
        )
    return items


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def analytics_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    engine = AnalyticsEngine(session)
    top_rated = await _list_items(session, await engine.top_rated(limit=10))
    trending = await _list_items(session, await engine.trending(limit=10))
    breakouts = await _list_items(session, await engine.breakouts(limit=10))
    html = _render(
        "analytics.html",
        {
            "request": request,
            "top_rated": top_rated,
            "trending": trending,
            "breakouts": breakouts,
        },
    )
    return HTMLResponse(content=html)


@router.get("/table", response_class=HTMLResponse)
async def analytics_table_partial(
    request: Request,
    kind: str = Query(default="top-rated", pattern="^(top-rated|trending|breakouts)$"),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    engine = AnalyticsEngine(session)
    if kind == "trending":
        rows = await engine.trending(limit=10)
        title = "Trending Stocks"
    elif kind == "breakouts":
        rows = await engine.breakouts(limit=10)
        title = "Breakout Candidates"
    else:
        rows = await engine.top_rated(limit=10)
        title = "Top Rated Stocks"
    items = await _list_items(session, rows)
    html = _render(
        "components/analytics_table.html",
        {"request": request, "items": items, "title": title},
    )
    return HTMLResponse(content=html)