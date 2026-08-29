from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS, is_product_symbol
from app.models.alert import AlertEvent, AlertRule
from app.models.eod_pipeline_run import EODPipelineRun
from app.models.import_job import ImportJob
from app.models.indicator_result import IndicatorResult
from app.models.scan_definition import ScanDefinition
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.models.technical_analysis import TechnicalAnalysis
from app.repositories.market_data import MarketDataRepository
from app.repositories.scan import ScanDefinitionRepository, ScanResultRepository
from app.repositories.stock import StockRepository
from app.services.notification_ui import NotificationUIService
from app.services.portfolio import PortfolioService
from app.services.watchlist import WatchlistService


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.stock_repo = StockRepository(session)
        self.market_repo = MarketDataRepository(session)
        self.scan_def_repo = ScanDefinitionRepository(session)
        self.scan_result_repo = ScanResultRepository(session)

    async def get_summary(self) -> dict[str, object]:
        stock_count = await self.session.scalar(
            select(func.count()).select_from(Stock).where(
                Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)
            )
        )
        indicator_count = await self.session.scalar(
            select(func.count())
            .select_from(IndicatorResult)
            .join(Stock, IndicatorResult.stock_id == Stock.id)
            .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
        )
        scan_result_count = await self.session.scalar(
            select(func.count())
            .select_from(ScanResult)
            .join(Stock, ScanResult.stock_id == Stock.id)
            .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
        )
        summary = await self.market_repo.product_summary()

        latest_import = await self.session.scalar(
            select(ImportJob).order_by(ImportJob.started_at.desc()).limit(1)
        )

        latest_trade_date = summary.get("last_trade_date")
        market_status = "closed"
        if latest_trade_date:
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
        rows = (
            await self.session.execute(
                select(ScanDefinition.code, func.count(ScanResult.id))
                .outerjoin(ScanResult, ScanResult.scan_definition_id == ScanDefinition.id)
                .outerjoin(Stock, ScanResult.stock_id == Stock.id)
                .where(ScanDefinition.is_active.is_(True))
                .where(
                    (ScanResult.id.is_(None))
                    | (Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                )
                .group_by(ScanDefinition.code)
            )
        ).all()
        return {code: count for code, count in rows}

    async def get_top_categories(self) -> dict[str, int]:
        result = (
            await self.session.execute(
                select(ScanDefinition.category, func.count(ScanResult.id))
                .join(ScanResult, ScanResult.scan_definition_id == ScanDefinition.id)
                .join(Stock, ScanResult.stock_id == Stock.id)
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .group_by(ScanDefinition.category)
            )
        ).all()
        return {category: count for category, count in result}

    async def search_stocks(self, query: str, limit: int = 20) -> list[Stock]:
        statement = (
            select(Stock)
            .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
            .where((Stock.symbol.ilike(f"%{query}%")) | (Stock.company_name.ilike(f"%{query}%")))
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def get_health(self) -> dict[str, object]:
        try:
            await self.session.execute(select(1))
            db_status = "healthy"
        except Exception:
            db_status = "unhealthy"

        stock_count = await self.session.scalar(
            select(func.count()).select_from(Stock).where(
                Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)
            )
        )
        return {
            "database": db_status,
            "total_stocks": stock_count or 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def get_top_rated(self, limit: int = 5) -> list[dict[str, object]]:
        rows = (
            await self.session.execute(
                select(
                    Stock.symbol,
                    Stock.company_name,
                    TechnicalAnalysis.overall_score,
                    TechnicalAnalysis.trend_score,
                    TechnicalAnalysis.momentum_score,
                    TechnicalAnalysis.trade_date,
                    TechnicalAnalysis.is_trending,
                    TechnicalAnalysis.is_breakout,
                )
                .join(Stock, TechnicalAnalysis.stock_id == Stock.id)
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .order_by(TechnicalAnalysis.overall_score.desc())
                .limit(limit)
            )
        ).fetchall()
        return [
            {
                "symbol": row.symbol,
                "company_name": row.company_name,
                "overall_score": row.overall_score,
                "trend_score": row.trend_score,
                "momentum_score": row.momentum_score,
                "trade_date": row.trade_date,
                "is_trending": row.is_trending,
                "is_breakout": row.is_breakout,
            }
            for row in rows
        ]

    async def get_portfolio_summary(self) -> dict[str, object]:
        return await PortfolioService(self.session).overall_summary()

    async def get_watchlist_summary(self) -> dict[str, object]:
        watchlists = await WatchlistService(self.session).list()
        total_stocks = sum(
            sum(1 for entry in w.stocks if is_product_symbol(entry.stock.symbol))
            for w in watchlists
        )
        top = next((w for w in watchlists if w.is_default), watchlists[0] if watchlists else None)
        return {
            "total_watchlists": len(watchlists),
            "total_watchlist_stocks": total_stocks,
            "top_watchlist": top.name if top else None,
            "top_watchlist_count": (
                sum(1 for entry in top.stocks if is_product_symbol(entry.stock.symbol))
                if top
                else 0
            ),
        }

    async def get_alert_summary(self) -> dict[str, object]:
        rules_count = await self.session.scalar(select(func.count()).select_from(AlertRule)) or 0
        enabled_count = (
            await self.session.scalar(
                select(func.count()).select_from(AlertRule).where(AlertRule.enabled.is_(True))
            )
            or 0
        )
        recent_events = (
            await self.session.scalars(
                select(AlertEvent)
                .options(joinedload(AlertEvent.stock))
                .join(Stock, AlertEvent.stock_id == Stock.id)
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .order_by(AlertEvent.created_at.desc())
                .limit(5)
            )
        ).all()
        pending_count = (
            await self.session.scalar(
                select(func.count())
                .select_from(AlertEvent)
                .join(Stock, AlertEvent.stock_id == Stock.id)
                .where(
                    AlertEvent.status == "triggered",
                    Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
                )
            )
            or 0
        )
        return {
            "total_rules": int(rules_count),
            "enabled_rules": int(enabled_count),
            "pending_events": int(pending_count),
            "recent_events": [
                {
                    "id": event.id,
                    "message": event.message,
                    "status": event.status,
                    "created_at": event.created_at,
                    "symbol": event.stock.symbol if event.stock else None,
                }
                for event in recent_events
            ],
        }

    async def get_notification_summary(self) -> dict[str, object]:
        return await NotificationUIService(self.session).get_dashboard_summary()

    async def get_pipeline_status(self) -> dict[str, object]:
        latest = await self.session.scalar(
            select(EODPipelineRun).order_by(EODPipelineRun.started_at.desc()).limit(1)
        )
        if latest is None:
            return {
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
            }
        return {
            "has_run": True,
            "status": latest.status,
            "trade_date": latest.trade_date,
            "finished_at": latest.finished_at,
            "imported_rows": latest.imported_rows,
            "indicators_calculated": latest.indicators_calculated,
            "scanner_matches": latest.scanner_matches,
            "analytics_calculated": latest.analytics_calculated,
            "failed_stage": latest.failed_stage,
            "duration_ms": latest.duration_ms,
        }
