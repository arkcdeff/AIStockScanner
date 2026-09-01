from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.models.technical_analysis import TechnicalAnalysis


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

    async def get_history_summary(self, stock_id: int) -> dict[str, Any]:
        row = (
            await self.session.execute(
                select(
                    func.count(DailyPrice.id),
                    func.min(DailyPrice.trade_date),
                    func.max(DailyPrice.trade_date),
                ).where(DailyPrice.stock_id == stock_id)
            )
        ).one()
        return {"sessions": int(row[0] or 0), "first_date": row[1], "latest_date": row[2]}

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

    async def get_latest_analytics(self, stock_id: int) -> TechnicalAnalysis | None:
        statement = (
            select(TechnicalAnalysis)
            .where(TechnicalAnalysis.stock_id == stock_id)
            .order_by(TechnicalAnalysis.trade_date.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def get_scanner_matches(self, stock_id: int, limit: int = 20) -> list[ScanResult]:
        statement = (
            select(ScanResult)
            .join(ScanDefinition, ScanResult.scan_definition_id == ScanDefinition.id)
            .where(ScanResult.stock_id == stock_id)
            .where(ScanDefinition.code != "test_scanner")
            .where(func.lower(ScanDefinition.category) != "test")
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
        ref_date = (
            await self.session.scalar(
                select(func.max(DailyPrice.trade_date)).where(DailyPrice.stock_id == stock_id)
            )
        ) or date.today()
        start_date = None
        if period == "1m":
            start_date = ref_date - timedelta(days=30)
        elif period == "3m":
            start_date = ref_date - timedelta(days=90)
        elif period == "6m":
            start_date = ref_date - timedelta(days=180)
        elif period == "1y":
            start_date = ref_date - timedelta(days=365)
        elif period == "3y":
            start_date = ref_date - timedelta(days=365 * 3)
        elif period == "5y":
            start_date = ref_date - timedelta(days=365 * 5)

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

    async def get_period_returns(
        self, stock_id: int, latest_date: date
    ) -> dict[str, float | None]:
        """Calculate 1W/1M/3M returns from stored closes using session offsets."""
        statement = (
            select(DailyPrice.trade_date, DailyPrice.close)
            .where(DailyPrice.stock_id == stock_id)
            .where(DailyPrice.trade_date <= latest_date)
            .order_by(DailyPrice.trade_date.desc())
            .limit(70)
        )
        rows = (await self.session.execute(statement)).all()
        if not rows:
            return {"return_1w_pct": None, "return_1m_pct": None, "return_3m_pct": None}

        latest_close = float(rows[0][1])
        results: dict[str, float | None] = {}

        for label, offset in [("return_1w_pct", 5), ("return_1m_pct", 22), ("return_3m_pct", 66)]:
            if len(rows) > offset:
                prev_close = float(rows[offset][1])
                if prev_close > 0:
                    results[label] = round(
                        ((latest_close - prev_close) / prev_close) * 100.0, 2
                    )
                else:
                    results[label] = None
            else:
                results[label] = None

        return results

    async def get_data_health(self, stock_id: int) -> dict[str, Any]:
        """Get indicator/analytics/scanner freshness and coverage data."""
        history = await self.get_history_summary(stock_id)

        indicator_date = await self.session.scalar(
            select(func.max(IndicatorResult.trade_date)).where(
                IndicatorResult.stock_id == stock_id
            )
        )

        analytics_date = await self.session.scalar(
            select(func.max(TechnicalAnalysis.trade_date)).where(
                TechnicalAnalysis.stock_id == stock_id
            )
        )

        scanner_count = await self.session.scalar(
            select(func.count(ScanResult.id))
            .join(ScanDefinition, ScanResult.scan_definition_id == ScanDefinition.id)
            .where(ScanResult.stock_id == stock_id)
            .where(ScanDefinition.code != "test_scanner")
            .where(func.lower(ScanDefinition.category) != "test")
        )

        sessions = history["sessions"]
        readiness_note = None
        if sessions < 20:
            readiness_note = f"Only {sessions} sessions — most scanners need 20+"
        elif sessions < 50:
            readiness_note = f"{sessions} of 50 sessions — some scanners need 50+"

        return {
            "history_sessions": sessions,
            "history_first_date": history["first_date"],
            "history_latest_date": history["latest_date"],
            "indicator_latest_date": indicator_date,
            "indicator_available": indicator_date is not None,
            "analytics_available": analytics_date is not None,
            "scanner_matches_count": int(scanner_count or 0),
            "scanner_readiness_note": readiness_note,
        }

    async def get_scanner_match_summary(
        self, stock_id: int, limit: int = 20
    ) -> dict[str, Any]:
        """Get scanner match summary for Stock360 snapshot card."""
        matches = await self.get_scanner_matches(stock_id, limit=limit)
        if not matches:
            return {
                "total_matches": 0,
                "latest_date": None,
                "bullish_count": 0,
                "bearish_count": 0,
                "recent_scanners": [],
            }

        bullish_keywords = {"bullish", "above", "oversold", "golden", "rising", "breakout",
                            "accumulation", "high_volume", "delivery_above"}
        bearish_keywords = {"bearish", "below", "overbought", "death", "squeeze"}

        bullish = 0
        bearish = 0
        recent_names: list[str] = []
        for m in matches:
            code = (m.scan_definition.code if m.scan_definition else "").lower()
            if any(kw in code for kw in bullish_keywords):
                bullish += 1
            elif any(kw in code for kw in bearish_keywords):
                bearish += 1
            name = m.scan_definition.name if m.scan_definition else "Scanner"
            if name not in recent_names and len(recent_names) < 5:
                recent_names.append(name)

        return {
            "total_matches": len(matches),
            "latest_date": matches[0].scan_date if matches else None,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "recent_scanners": recent_names,
        }
