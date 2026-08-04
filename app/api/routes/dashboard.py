from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.services.dashboard import DashboardService

router = APIRouter()
env = Environment(loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")))


def _render(template_name: str, context: dict) -> str:
    template = env.get_template(template_name)
    return template.render(**context)


@router.get("/", response_class=HTMLResponse)
async def root_redirect(request: Request) -> HTMLResponse:
    html = _render(
        "dashboard.html",
        {
            "request": request,
            "summary": {
                "total_stocks": 0,
                "total_indicators": 0,
                "total_scanner_matches": 0,
                "latest_trade_date": None,
                "market_status": "closed",
                "import_status": {},
                "daily_prices": 0,
                "first_trade_date": None,
            },
            "recent_imports": [],
            "scanner_summary": {},
            "top_categories": {},
            "top_rated": [],
            "health": {},
            "pipeline_status": {
                "has_run": False,
                "status": "never",
                "trade_date": None,
                "finished_at": None,
                "imported_rows": 0,
                "indicators_calculated": 0,
                "scanner_matches": 0,
                "analytics_calculated": 0,
                "failed_stage": None,
                "duration_ms": None,
            },
        },
    )
    return HTMLResponse(content=html)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = DashboardService(session)
    summary = await service.get_summary()
    recent_imports = await service.get_recent_imports()
    scanner_summary = await service.get_scanner_summary()
    top_categories = await service.get_top_categories()
    health = await service.get_health()
    pipeline_status = await service.get_pipeline_status()
    top_rated = await service.get_top_rated()

    html = _render(
        "dashboard.html",
        {
            "request": request,
            "summary": summary,
            "recent_imports": recent_imports,
            "scanner_summary": scanner_summary,
            "top_categories": top_categories,
            "health": health,
            "pipeline_status": pipeline_status,
            "top_rated": top_rated,
        },
    )
    return HTMLResponse(content=html)


@router.get("/dashboard/search", response_class=HTMLResponse)
async def search_stocks(
    request: Request,
    q: str = "",
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    service = DashboardService(session)
    stocks = await service.search_stocks(q) if q else []
    html = _render(
        "components/search_results.html",
        {
            "request": request,
            "stocks": stocks,
            "query": q,
        },
    )
    return HTMLResponse(content=html)
