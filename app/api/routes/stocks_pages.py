from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.models.stock import Stock
from app.repositories.stock import StockRepository


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


async def _search(session: AsyncSession, q: str, limit: int) -> list[Stock]:
    if not q:
        return await StockRepository(session).list(offset=0, limit=limit)
    pattern = f"%{q.strip().upper()}%"
    statement = (
        select(Stock)
        .where(
            Stock.symbol.ilike(pattern) | Stock.company_name.ilike(f"%{q.strip()}%")
        )
        .order_by(Stock.symbol)
        .limit(limit)
    )
    return list(await session.scalars(statement))


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def stocks_page(
    request: Request,
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    stocks = await _search(session, q, limit=200)
    html = _render(
        "stocks.html",
        {
            "request": request,
            "stocks": stocks,
            "query": q,
        },
    )
    return HTMLResponse(content=html)


@router.get("/table", response_class=HTMLResponse)
async def stocks_table_partial(
    request: Request,
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    stocks = await _search(session, q, limit=200)
    html = _render(
        "components/stocks_table.html",
        {"request": request, "stocks": stocks, "query": q},
    )
    return HTMLResponse(content=html)