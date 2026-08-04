from fastapi import APIRouter

from app.api.routes import (
    ai,
    alerts,
    analytics,
    dashboard,
    health,
    indicators,
    market_data,
    pipeline,
    portfolio,
    scanners,
    stocks,
    watchlists,
)
from app.api.routes import scanner_workspace as scanner_workspace_module
from app.api.routes import stock_detail as stock_detail_module

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(stocks.router)
api_router.include_router(market_data.router)
api_router.include_router(indicators.router)
api_router.include_router(scanners.router)
api_router.include_router(pipeline.router)
api_router.include_router(analytics.router)
api_router.include_router(ai.router)
api_router.include_router(watchlists.router)
api_router.include_router(portfolio.router)
api_router.include_router(alerts.router)

dashboard_router = APIRouter()
dashboard_router.include_router(dashboard.router)

scanner_workspace_router = APIRouter()
scanner_workspace_router.include_router(scanner_workspace_module.router)

stock_detail_router = APIRouter()
stock_detail_router.include_router(stock_detail_module.router)
