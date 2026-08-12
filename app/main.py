from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape
from loguru import logger
from starlette.staticfiles import StaticFiles

from app.api.browser_auth import ForbiddenError, LoginRequiredError
from app.api.router import (
    api_router,
    dashboard_router,
    scanner_workspace_router,
    stock_detail_router,
)
from app.api.routes.admin_pages import router as admin_pages_router
from app.api.routes.ai_pages import router as ai_pages_router
from app.api.routes.alert_pages import router as alert_pages_router
from app.api.routes.analytics_pages import router as analytics_pages_router
from app.api.routes.auth_pages import router as auth_pages_router
from app.api.routes.backtest_pages import router as backtest_pages_router
from app.api.routes.indicator_pages import router as indicator_pages_router
from app.api.routes.market_data_pages import router as market_data_pages_router
from app.api.routes.notification_pages import router as notification_pages_router
from app.api.routes.pipeline_pages import router as pipeline_pages_router
from app.api.routes.portfolio_pages import router as portfolio_pages_router
from app.api.routes.scanner_library_pages import router as scanner_library_pages_router
from app.api.routes.scanner_run_pages import router as scanner_run_pages_router
from app.api.routes.scheduler_pages import router as scheduler_pages_router
from app.api.routes.stocks_pages import router as stocks_pages_router
from app.api.routes.watchlist_pages import router as watchlist_pages_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import dispose_engine, get_session_factory
from app.scheduler.eod_scheduler import get_scheduler
from app.services.daily_summary import DailySummaryService
from app.services.eod_pipeline import EODPipelineService


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("Starting {} in {} mode", settings.app_name, settings.app_env)

    scheduler = get_scheduler()
    if scheduler.enabled:
        if settings.eod_scheduler_enabled:
            recovered = await EODPipelineService().recover_abandoned_runs()
            if recovered:
                logger.warning("Recovered {} abandoned pipeline run(s)", recovered)
        if settings.daily_summary_enabled:
            async with get_session_factory()() as session:
                recovered = await DailySummaryService(session).recover_abandoned_runs()
            if recovered:
                logger.warning("Recovered {} abandoned daily summary run(s)", recovered)
        scheduler.start()
    else:
        logger.info("Application scheduler disabled; not starting")

    yield

    scheduler.stop()
    await dispose_engine()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.13.0",
        debug=settings.app_debug,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.include_router(auth_pages_router)
    app.include_router(admin_pages_router)
    app.include_router(dashboard_router)
    app.include_router(scanner_workspace_router, prefix="/scanner-results")
    app.include_router(stocks_pages_router, prefix="/stocks")
    app.include_router(stock_detail_router, prefix="/stocks")
    app.include_router(watchlist_pages_router, prefix="/watchlists")
    app.include_router(portfolio_pages_router, prefix="/portfolio")
    app.include_router(alert_pages_router, prefix="/alerts")
    app.include_router(notification_pages_router, prefix="/notifications")
    app.include_router(market_data_pages_router, prefix="/market-data")
    app.include_router(indicator_pages_router, prefix="/indicators")
    app.include_router(scanner_library_pages_router, prefix="/scanner-library")
    app.include_router(scanner_run_pages_router, prefix="/scanner-run")
    app.include_router(pipeline_pages_router, prefix="/pipeline")
    app.include_router(scheduler_pages_router, prefix="/scheduler")
    app.include_router(analytics_pages_router, prefix="/analytics")
    app.include_router(ai_pages_router, prefix="/ai")
    app.include_router(backtest_pages_router, prefix="/backtests")

    templates = Jinja2Templates(directory=str(settings.base_dir / "app" / "templates"))
    app.state.templates = templates
    error_env = Environment(
        loader=FileSystemLoader(str(settings.base_dir / "app" / "templates")),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )

    @app.exception_handler(LoginRequiredError)
    async def _login_required_handler(request, exc: LoginRequiredError) -> RedirectResponse:
        next_value = quote(exc.next_path or "/dashboard")
        return RedirectResponse(f"/login?next={next_value}&expired=1", status_code=302)

    @app.exception_handler(ForbiddenError)
    async def _forbidden_handler(request, exc: ForbiddenError) -> HTMLResponse:
        html = error_env.get_template("errors/403.html").render(request=request)
        return HTMLResponse(content=html, status_code=403)

    app.mount(
        "/static",
        StaticFiles(directory=str(settings.base_dir / "app" / "static")),
        name="static",
    )

    @app.get("/", tags=["Root"])
    async def root() -> dict[str, str]:
        return {
            "message": settings.app_name,
            "health": f"{settings.api_v1_prefix}/health",
            "docs": "/docs",
        }

    return app


app = create_app()
