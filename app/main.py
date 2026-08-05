from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from loguru import logger
from starlette.staticfiles import StaticFiles

from app.api.router import (
    api_router,
    dashboard_router,
    scanner_workspace_router,
    stock_detail_router,
)
from app.api.routes.alert_pages import router as alert_pages_router
from app.api.routes.notification_pages import router as notification_pages_router
from app.api.routes.portfolio_pages import router as portfolio_pages_router
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
        version="0.1.0",
        debug=settings.app_debug,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.include_router(dashboard_router)
    app.include_router(scanner_workspace_router, prefix="/scanner-results")
    app.include_router(stock_detail_router, prefix="/stocks")
    app.include_router(watchlist_pages_router, prefix="/watchlists")
    app.include_router(portfolio_pages_router, prefix="/portfolio")
    app.include_router(alert_pages_router, prefix="/alerts")
    app.include_router(notification_pages_router, prefix="/notifications")

    templates = Jinja2Templates(directory=str(settings.base_dir / "app" / "templates"))
    app.state.templates = templates

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
