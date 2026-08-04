from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.provider import get_provider
from app.core.config import settings
from app.repositories.ai_explanation import AIExplanationRepository
from app.services.dashboard import DashboardService


class AIInsightsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ai_repo = AIExplanationRepository(session)
        self.dashboard = DashboardService(session)

    async def get_ai_status(self) -> dict[str, object]:
        try:
            provider = get_provider()
            model = provider.model_name()
        except Exception:  # noqa: BLE001
            provider = None
            model = None

        pipeline = await self.dashboard.get_pipeline_status()
        latest_trade_date = await self.ai_repo.latest_trade_date()
        return {
            "enabled": settings.ai_enabled,
            "provider": provider.name if provider else "unavailable",
            "model": model,
            "explanations_total": await self.ai_repo.count_total(),
            "explanations_latest_date": latest_trade_date,
            "explanations_for_latest_date": (
                await self.ai_repo.count_for_trade_date(latest_trade_date)
                if latest_trade_date
                else 0
            ),
            "pipeline": pipeline,
        }

    async def get_top_opportunities(self, limit: int = 5) -> list[dict[str, object]]:
        return await self.dashboard.get_top_rated(limit=limit)

    async def get_highest_confidence(self, limit: int = 5) -> list[dict[str, object]]:
        rows = await self.ai_repo.highest_confidence(limit=limit)
        return [
            {
                "id": row.id,
                "symbol": row.stock.symbol if row.stock else "?",
                "company_name": row.stock.company_name if row.stock else "",
                "trade_date": row.trade_date,
                "provider": row.provider,
                "model": row.model,
                "confidence": row.confidence,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def get_recent_explanations(self, limit: int = 5) -> list[dict[str, object]]:
        rows = await self.ai_repo.list_recent(limit=limit)
        return [
            {
                "id": row.id,
                "symbol": row.stock.symbol if row.stock else "?",
                "company_name": row.stock.company_name if row.stock else "",
                "trade_date": row.trade_date,
                "provider": row.provider,
                "model": row.model,
                "confidence": row.confidence,
                "created_at": row.created_at,
            }
            for row in rows
        ]
