from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db_session
from app.services.ai_insights import AIInsightsService
from app.services.dashboard import DashboardService

router = APIRouter()
env = Environment(
    loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
    autoescape=select_autoescape(("html", "htm", "xml")),
)


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
            "ai_status": {
                "enabled": settings.ai_enabled,
                "provider": None,
                "model": None,
                "explanations_total": 0,
                "explanations_latest_date": None,
                "explanations_for_latest_date": 0,
                "pipeline": {"status": "never"},
            },
            "ai_opportunities": [],
            "ai_highest_confidence": [],
            "ai_recent": [],
            "portfolio_summary": {
                "holdings_count": 0,
                "total_invested": 0.0,
                "total_value": 0.0,
                "total_pl": 0.0,
                "total_roi_pct": 0.0,
                "today_pl": 0.0,
            },
            "watchlist_summary": {
                "total_watchlists": 0,
                "total_watchlist_stocks": 0,
                "top_watchlist": None,
                "top_watchlist_count": 0,
            },
            "alert_summary": {
                "total_rules": 0,
                "enabled_rules": 0,
                "pending_events": 0,
                "recent_events": [],
            },
            "notification_summary": {
                "unread_count": 0,
                "failed_count": 0,
                "pending_retry_count": 0,
                "sent_today": 0,
                "latest": None,
                "last_delivery_at": None,
                "recent": [],
                "overall_status": "healthy",
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
    portfolio_summary = await service.get_portfolio_summary()
    watchlist_summary = await service.get_watchlist_summary()
    alert_summary = await service.get_alert_summary()
    notification_summary = await service.get_notification_summary()

    ai_service = AIInsightsService(session)
    ai_status = await ai_service.get_ai_status()
    ai_opportunities = await ai_service.get_top_opportunities()
    ai_highest_confidence = await ai_service.get_highest_confidence()
    ai_recent = await ai_service.get_recent_explanations()

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
            "portfolio_summary": portfolio_summary,
            "watchlist_summary": watchlist_summary,
            "alert_summary": alert_summary,
            "notification_summary": notification_summary,
            "ai_status": ai_status,
            "ai_opportunities": ai_opportunities,
            "ai_highest_confidence": ai_highest_confidence,
            "ai_recent": ai_recent,
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
