from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.import_job import ImportJob
from app.models.indicator_result import IndicatorResult
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.repositories.market_data import MarketDataRepository
from app.repositories.scan import ScanDefinitionRepository, ScanResultRepository
from app.repositories.stock import StockRepository


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.stock_repo = StockRepository(session)
        self.market_repo = MarketDataRepository(session)
        self.scan_def_repo = ScanDefinitionRepository(session)
        self.scan_result_repo = ScanResultRepository(session)

    async def get_summary(self) -> dict[str, object]:
        stock_count = await self.session.scalar(select(func.count()).select_from(Stock))
        indicator_count = await self.session.scalar(select(func.count()).select_from(IndicatorResult))
        scan_result_count = await self.session.scalar(select(func.count()).select_from(ScanResult))
        summary = await self.market_repo.summary()

        latest_import = (
            await self.session.scalar(
                select(ImportJob).order_by(ImportJob.started_at.desc()).limit(1)
            )
        )

        latest_trade_date = summary.get("last_trade_date")
        market_status = "closed"
        if latest_trade_date:
            from datetime import date

            if isinstance(latest_trade_date, datetime):
                latest_trade_date = latest_trade_date.date()
            if latest_trade_date == datetime.now(UTC).date():
                market_status = "open"

        return {
            "total_stocks": stock_count or 0,
            "total_indicators": indicator_count or 0,
            "total_scanner_matches": scan_result_count or 0,
            "latest_trade_date": latest_trade_date,
            "market_status": market_status,
            "import_status": {
                "status": latest_import.status if latest_import else "never",
                "trade_date": latest_import.trade_date if latest_import else None,
                "source": latest_import.source if latest_import else None,
                "started_at": latest_import.started_at if latest_import else None,
                "completed_at": latest_import.completed_at if latest_import else None,
            },
            "daily_prices": summary.get("daily_prices", 0),
            "first_trade_date": summary.get("first_trade_date"),
        }

    async def get_recent_imports(self, limit: int = 5) -> list[ImportJob]:
        return await self.market_repo.list_imports(limit=limit)

    async def get_scanner_summary(self) -> dict[str, int]:
        scan_defs = await self.scan_def_repo.list_active()
        summary: dict[str, int] = {}
        for scan_def in scan_defs:
            count = await self.session.scalar(
                select(func.count())
                .select_from(ScanResult)
                .where(ScanResult.scan_definition_id == scan_def.id)
            )
            summary[scan_def.code] = count or 0
        return summary

    async def get_top_categories(self) -> dict[str, int]:
        scan_defs = await self.scan_def_repo.list_active()
        result = (
            await self.session.execute(
                select(ScanDefinition.category, func.count(ScanResult.id))
                .join(ScanResult, ScanResult.scan_definition_id == ScanDefinition.id)
                .group_by(ScanDefinition.category)
            )
        ).all()
        return {category: count for category, count in result}

    async def search_stocks(self, query: str, limit: int = 20) -> list[Stock]:
        statement = (
            select(Stock)
            .where(
                (Stock.symbol.ilike(f"%{query}%"))
                | (Stock.company_name.ilike(f"%{query}%"))
            )
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def get_health(self) -> dict[str, object]:
        try:
            await self.session.execute(select(1))
            db_status = "healthy"
        except Exception:
            db_status = "unhealthy"

        stock_count = await self.session.scalar(select(func.count()).select_from(Stock))
        return {
            "database": db_status,
            "total_stocks": stock_count or 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }
