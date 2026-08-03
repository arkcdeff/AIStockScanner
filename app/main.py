from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from app.api.router import api_router, dashboard_router, scanner_workspace_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("Starting {} in {} mode", settings.app_name, settings.app_env)
    yield
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
