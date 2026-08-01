from fastapi import APIRouter

from app.api.routes import health, indicators, market_data, scanners, stocks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(stocks.router)
api_router.include_router(market_data.router)
api_router.include_router(indicators.router)
api_router.include_router(scanners.router)

