from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.scan_result import ScanResult
from app.models.stock import Stock


class StockDetailRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_stock(self, symbol: str) -> Stock | None:
        statement = (
            select(Stock)
            .where(Stock.symbol == symbol.upper())
            .options(selectinload(Stock.sector_ref))
        )
        return await self.session.scalar(statement)

    async def get_latest_price(self, stock_id: int) -> DailyPrice | None:
        statement = (
            select(DailyPrice)
            .where(DailyPrice.stock_id == stock_id)
            .order_by(DailyPrice.trade_date.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def get_20d_averages(self, stock_id: int, latest_date: date) -> dict[str, float | None]:
        start_date = latest_date - timedelta(days=180)
        statement = (
            select(
                func.avg(DailyPrice.volume).label("avg_volume"),
                func.avg(DailyPrice.delivery_percentage).label("avg_delivery"),
            )
            .where(DailyPrice.stock_id == stock_id)
            .where(DailyPrice.trade_date >= start_date)
        )
        result = (await self.session.execute(statement)).first()
        return {
            "avg_volume": float(result.avg_volume) if result and result.avg_volume else None,
            "avg_delivery": float(result.avg_delivery) if result and result.avg_delivery else None,
        }

    async def get_latest_indicator(self, stock_id: int) -> IndicatorResult | None:
        statement = (
            select(IndicatorResult)
            .where(IndicatorResult.stock_id == stock_id)
            .order_by(IndicatorResult.trade_date.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def get_scanner_matches(self, stock_id: int, limit: int = 20) -> list[ScanResult]:
        statement = (
            select(ScanResult)
            .where(ScanResult.stock_id == stock_id)
            .options(selectinload(ScanResult.scan_definition))
            .order_by(ScanResult.scan_date.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def get_price_history(
        self,
        stock_id: int,
        period: str = "1y",
        limit: int = 2000,
    ) -> list[DailyPrice]:
        today = date.today()
        start_date = None
        if period == "1m":
            start_date = today - timedelta(days=30)
        elif period == "3m":
            start_date = today - timedelta(days=90)
        elif period == "6m":
            start_date = today - timedelta(days=180)
        elif period == "1y":
            start_date = today - timedelta(days=365)
        elif period == "3y":
            start_date = today - timedelta(days=365 * 3)
        elif period == "5y":
            start_date = today - timedelta(days=365 * 5)

        statement = select(DailyPrice).where(DailyPrice.stock_id == stock_id)
        if start_date:
            statement = statement.where(DailyPrice.trade_date >= start_date)
        statement = statement.order_by(DailyPrice.trade_date.asc()).limit(limit)
        return list(await self.session.scalars(statement))

    async def get_chart_data(
        self, stock_id: int, period: str = "1y", include_indicators: bool = True
    ) -> dict[str, Any]:
        prices = await self.get_price_history(stock_id, period=period)
        candles: list[dict[str, Any]] = []
        volume: list[dict[str, Any]] = []
        ema20: list[dict[str, Any]] = []
        ema50: list[dict[str, Any]] = []
        ema200: list[dict[str, Any]] = []
        scanner_markers: list[dict[str, Any]] = []

        for p in prices:
            candles.append(
                {
                    "time": p.trade_date.isoformat(),
                    "open": float(p.open),
                    "high": float(p.high),
                    "low": float(p.low),
                    "close": float(p.close),
                }
            )
            volume.append(
                {
                    "time": p.trade_date.isoformat(),
                    "value": float(p.volume),
                }
            )

        if include_indicators and prices:
            start_date = prices[0].trade_date
            indicator_stmt = (
                select(IndicatorResult)
                .where(IndicatorResult.stock_id == stock_id)
                .where(IndicatorResult.trade_date >= start_date)
                .order_by(IndicatorResult.trade_date.asc())
            )
            indicators = list(await self.session.scalars(indicator_stmt))

            indicator_map = {ind.trade_date: ind for ind in indicators}
            for p in prices:
                ind = indicator_map.get(p.trade_date)
                if ind:
                    if ind.ema_20 is not None:
                        ema20.append({"time": p.trade_date.isoformat(), "value": float(ind.ema_20)})
                    if ind.ema_50 is not None:
                        ema50.append({"time": p.trade_date.isoformat(), "value": float(ind.ema_50)})
                    if ind.ema_200 is not None:
                        ema200.append(
                            {"time": p.trade_date.isoformat(), "value": float(ind.ema_200)}
                        )

        scan_results = await self.get_scanner_matches(stock_id, limit=100)
        for sr in scan_results:
            scan_def = sr.scan_definition
            scanner_markers.append(
                {
                    "time": sr.scan_date.isoformat(),
                    "position": "below",
                    "color": "#2196F3",
                    "shape": "arrowUp",
                    "text": scan_def.name if scan_def else "Scan",
                }
            )

        return {
            "candles": candles,
            "volume": volume,
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "scanner_markers": scanner_markers,
        }
