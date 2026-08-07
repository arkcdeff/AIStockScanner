from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import require_browser_permission
from app.core.config import settings
from app.db.session import get_db_session
from app.repositories.market_data import MarketDataRepository


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


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def market_data_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    repo = MarketDataRepository(session)
    summary = await repo.summary()
    imports = await repo.list_imports(limit=15)
    prices = await repo.list_prices(limit=10)
    price_rows = [
        {
            "symbol": stock.symbol,
            "trade_date": price.trade_date,
            "close": price.close,
            "open": price.open,
            "high": price.high,
            "low": price.low,
            "volume": price.volume,
            "delivery_percentage": price.delivery_percentage,
        }
        for price, stock in prices
    ]
    html = _render(
        "market_data.html",
        {
            "request": request,
            "summary": summary,
            "imports": imports,
            "prices": price_rows,
            "nse_download_blocked": True,
        },
    )
    return HTMLResponse(content=html)


@router.post(
    "/import",
    response_class=HTMLResponse,
    dependencies=WRITE_DEPENDENCIES,
)
async def import_market_data(
    request: Request,
    bhavcopy: UploadFile = File(...),
    delivery: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    from app.services.nse_import import NSEImportService

    bhavcopy_bytes = await bhavcopy.read()
    delivery_bytes = await delivery.read() if delivery else None
    try:
        job = await NSEImportService(session).import_bytes(
            bhavcopy=bhavcopy_bytes,
            bhavcopy_name=bhavcopy.filename or "bhavcopy.csv",
            delivery=delivery_bytes,
            delivery_name=delivery.filename if delivery else None,
        )
    except Exception as exc:  # noqa: BLE001 - present friendly result to the user
        raise HTTPException(status_code=422, detail=f"Import failed: {exc}") from exc
    return RedirectResponse(
        url=f"/market-data?imported={job.status}", status_code=303
    )


@router.get("/prices", response_class=HTMLResponse)
async def market_prices_partial(
    request: Request,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    repo = MarketDataRepository(session)
    prices = await repo.list_prices(symbol=symbol, limit=limit)
    rows = [
        {
            "symbol": stock.symbol,
            "trade_date": price.trade_date,
            "open": price.open,
            "high": price.high,
            "low": price.low,
            "close": price.close,
            "volume": price.volume,
            "delivery_percentage": price.delivery_percentage,
        }
        for price, stock in prices
    ]
    html = _render(
        "components/market_prices_table.html",
        {"request": request, "prices": rows},
    )
    return HTMLResponse(content=html)


@router.get("/imports", response_class=HTMLResponse)
async def market_imports_partial(
    request: Request,
    limit: int = Query(default=15, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    imports = await MarketDataRepository(session).list_imports(limit=limit)
    html = _render(
        "components/market_imports_table.html",
        {"request": request, "imports": imports},
    )
    return HTMLResponse(content=html)