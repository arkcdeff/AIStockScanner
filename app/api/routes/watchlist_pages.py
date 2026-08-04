from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.services.watchlist import WatchlistError, WatchlistService

router = APIRouter()
_env = None


def _get_env():
    global _env
    if _env is None:
        from jinja2 import Environment, FileSystemLoader

        _env = Environment(
            loader=FileSystemLoader(str(settings.base_dir / "app" / "templates"))
        )
    return _env


def _render(template_name: str, context: dict) -> str:
    env = _get_env()
    return env.get_template(template_name).render(**context)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def watchlists_page(
    request: Request,
    selected: int | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    watchlists = await service.list()
    detail = None
    target = selected or (watchlists[0].id if watchlists else None)
    if target is not None:
        detail = await service.get(target)
    valued = await service.list_stocks(target) if target is not None else []
    html = _render(
        "watchlists.html",
        {
            "request": request,
            "watchlists": watchlists,
            "selected_watchlist": detail,
            "stocks": valued,
        },
    )
    return HTMLResponse(content=html)


@router.post("/", response_class=HTMLResponse)
async def create_watchlist(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(default=None),
    color: str | None = Form(default=None),
    icon: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    try:
        await service.create(
            {
                "name": name,
                "description": description,
                "color": color,
                "icon": icon,
            }
        )
    except WatchlistError:
        pass
    return RedirectResponse(url="/watchlists", status_code=303)


@router.post("/{watchlist_id}/toggle-favorite", response_class=HTMLResponse)
async def toggle_favorite(
    watchlist_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    watchlist = await service.get(watchlist_id)
    if watchlist is not None:
        await service.update(watchlist_id, {"is_favorite": not watchlist.is_favorite})
    return RedirectResponse(url="/watchlists", status_code=303)


@router.post("/{watchlist_id}/toggle-default", response_class=HTMLResponse)
async def toggle_default(
    watchlist_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    watchlist = await service.get(watchlist_id)
    if watchlist is not None:
        await service.update(watchlist_id, {"is_default": not watchlist.is_default})
    return RedirectResponse(url="/watchlists", status_code=303)


@router.post("/{watchlist_id}/delete", response_class=HTMLResponse)
async def delete_watchlist(
    watchlist_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    try:
        await service.delete(watchlist_id)
    except WatchlistError:
        pass
    return RedirectResponse(url="/watchlists", status_code=303)


@router.post("/{watchlist_id}/stocks", response_class=HTMLResponse)
async def add_stock_to_watchlist(
    watchlist_id: int,
    request: Request,
    stock_id: int = Form(...),
    notes: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    try:
        await service.add_stock(watchlist_id, stock_id, notes=notes)
    except WatchlistError:
        pass
    return RedirectResponse(url=f"/watchlists?selected={watchlist_id}", status_code=303)


@router.post("/{watchlist_id}/stocks/{stock_id}/delete", response_class=HTMLResponse)
async def remove_stock_from_watchlist(
    watchlist_id: int,
    stock_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    try:
        await service.remove_stock(watchlist_id, stock_id)
    except WatchlistError:
        pass
    return RedirectResponse(url=f"/watchlists?selected={watchlist_id}", status_code=303)


@router.get("/search", response_class=HTMLResponse)
async def search_stocks_partial(
    request: Request,
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = WatchlistService(session)
    stocks = await service.search_stocks(q) if q else []
    html = _render(
        "components/watchlist_search_results.html",
        {"request": request, "stocks": stocks, "query": q},
    )
    return HTMLResponse(content=html)
