from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.services.stock import StockService


def _render(template_name: str, context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    return env.get_template(template_name).render(**context)


router = APIRouter(dependencies=[Depends(require_browser_permission("stocks.read"))])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def stocks_page(
    request: Request,
    q: str = Query(default=""),
    readiness: str = Query(default="all", pattern="^(all|ready|limited|missing)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    explorer = await StockService(session).list_explorer(
        query=q, readiness=readiness, page=page, page_size=page_size
    )
    html = _render(
        "stocks.html",
        {
            "request": request,
            "stocks": explorer["rows"],
            "explorer": explorer,
            "query": q,
            "readiness_filter": readiness,
        },
    )
    return HTMLResponse(content=html)


@router.get("/table", response_class=HTMLResponse)
async def stocks_table_partial(
    request: Request,
    q: str = Query(default=""),
    readiness: str = Query(default="all", pattern="^(all|ready|limited|missing)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    explorer = await StockService(session).list_explorer(
        query=q, readiness=readiness, page=page, page_size=page_size
    )
    html = _render(
        "components/stocks_table.html",
        {
            "request": request,
            "stocks": explorer["rows"],
            "explorer": explorer,
            "query": q,
            "readiness_filter": readiness,
        },
    )
    return HTMLResponse(content=html)
