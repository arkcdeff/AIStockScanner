from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.services.portfolio import PortfolioError, PortfolioService

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
async def portfolio_page(
    request: Request,
    selected: int | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = PortfolioService(session)
    portfolios = await service.list()
    summary = await service.overall_summary()
    selected_portfolio = None
    valued = []
    detail_summary = None
    target = selected or (portfolios[0].id if portfolios else None)
    if target is not None:
        selected_portfolio = await service.get(target)
        holdings = await service.list_holdings(target)
        valued = await service.value_holdings(holdings)
        detail_summary = service._aggregate(target, valued)
    html = _render(
        "portfolio.html",
        {
            "request": request,
            "portfolios": portfolios,
            "summary": summary,
            "selected_portfolio": selected_portfolio,
            "holdings": valued,
            "detail_summary": detail_summary,
        },
    )
    return HTMLResponse(content=html)


@router.post("/", response_class=HTMLResponse)
async def create_portfolio(
    request: Request,
    broker: str | None = Form(default=None),
    account_name: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = PortfolioService(session)
    try:
        portfolio = await service.create(
            {"broker": broker, "account_name": account_name, "notes": notes}
        )
    except PortfolioError:
        return RedirectResponse(url="/portfolio", status_code=303)
    return RedirectResponse(url=f"/portfolio?selected={portfolio.id}", status_code=303)


@router.post("/{portfolio_id}/delete", response_class=HTMLResponse)
async def delete_portfolio(
    portfolio_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = PortfolioService(session)
    try:
        await service.delete(portfolio_id)
    except PortfolioError:
        pass
    return RedirectResponse(url="/portfolio", status_code=303)


@router.post("/{portfolio_id}/holdings", response_class=HTMLResponse)
async def add_holding(
    portfolio_id: int,
    request: Request,
    stock_id: int = Form(...),
    quantity: str = Form(...),
    average_price: str = Form(...),
    purchase_date: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = PortfolioService(session)
    try:
        await service.add_holding(
            portfolio_id,
            {
                "stock_id": stock_id,
                "quantity": Decimal(quantity),
                "average_price": Decimal(average_price),
                "purchase_date": purchase_date or None,
                "notes": notes,
            },
        )
    except (PortfolioError, ValueError):
        pass
    return RedirectResponse(url=f"/portfolio?selected={portfolio_id}", status_code=303)


@router.post("/{portfolio_id}/holdings/{holding_id}/delete", response_class=HTMLResponse)
async def remove_holding(
    portfolio_id: int,
    holding_id: int,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = PortfolioService(session)
    try:
        await service.remove_holding(portfolio_id, holding_id)
    except PortfolioError:
        pass
    return RedirectResponse(url=f"/portfolio?selected={portfolio_id}", status_code=303)


@router.get("/search", response_class=HTMLResponse)
async def search_stocks_partial(
    request: Request,
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.watchlist import WatchlistService

    stocks = await WatchlistService(session).search_stocks(q) if q else []
    html = _render(
        "components/watchlist_search_results.html",
        {"request": request, "stocks": stocks, "query": q},
    )
    return HTMLResponse(content=html)
