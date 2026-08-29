from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.product_provenance import (
    NON_PRODUCTION_AI_PROVIDERS,
    SYNTHETIC_FIXTURE_SYMBOLS,
)
from app.models.ai_explanation import AIExplanation
from app.models.stock import Stock


class AIExplanationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_stock_date_provider(
        self, stock_id: int, trade_date: date, provider: str
    ) -> AIExplanation | None:
        statement = (
            select(AIExplanation)
            .where(
                AIExplanation.stock_id == stock_id,
                AIExplanation.trade_date == trade_date,
                AIExplanation.provider == provider,
            )
            .options(selectinload(AIExplanation.stock))
        )
        return await self.session.scalar(statement)

    async def upsert(
        self,
        stock_id: int,
        trade_date: date,
        provider: str,
        model: str,
        prompt_hash: str,
        response: str,
        confidence: int | None,
    ) -> AIExplanation:
        existing = await self.get_by_stock_date_provider(stock_id, trade_date, provider)
        if existing is not None:
            existing.model = model
            existing.prompt_hash = prompt_hash
            existing.response = response
            existing.confidence = confidence
            self.session.add(existing)
            await self.session.flush()
            return existing

        explanation = AIExplanation(
            stock_id=stock_id,
            trade_date=trade_date,
            provider=provider,
            model=model,
            prompt_hash=prompt_hash,
            response=response,
            confidence=confidence,
        )
        self.session.add(explanation)
        await self.session.flush()
        return explanation

    @staticmethod
    def _product_filters():
        return (
            Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS),
            AIExplanation.provider.not_in(NON_PRODUCTION_AI_PROVIDERS),
        )

    async def list_recent(
        self, limit: int = 10, *, product_only: bool = False
    ) -> list[AIExplanation]:
        statement = (
            select(AIExplanation)
            .options(selectinload(AIExplanation.stock))
            .order_by(AIExplanation.created_at.desc())
        )
        if product_only:
            statement = statement.join(Stock).where(*self._product_filters())
        statement = statement.limit(limit)
        return list(await self.session.scalars(statement))

    async def highest_confidence(
        self, limit: int = 10, *, product_only: bool = False
    ) -> list[AIExplanation]:
        statement = (
            select(AIExplanation)
            .options(selectinload(AIExplanation.stock))
            .where(AIExplanation.confidence.isnot(None))
            .order_by(AIExplanation.confidence.desc(), AIExplanation.created_at.desc())
        )
        if product_only:
            statement = statement.join(Stock).where(*self._product_filters())
        statement = statement.limit(limit)
        return list(await self.session.scalars(statement))

    async def count_for_trade_date(
        self, trade_date: date, *, product_only: bool = False
    ) -> int:
        statement = select(func.count(AIExplanation.id)).where(
            AIExplanation.trade_date == trade_date
        )
        if product_only:
            statement = statement.join(Stock).where(*self._product_filters())
        return await self.session.scalar(statement) or 0

    async def latest_trade_date(self, *, product_only: bool = False) -> date | None:
        statement = select(func.max(AIExplanation.trade_date))
        if product_only:
            statement = statement.join(Stock).where(*self._product_filters())
        return await self.session.scalar(statement)

    async def count_total(self, *, product_only: bool = False) -> int:
        statement = select(func.count(AIExplanation.id))
        if product_only:
            statement = statement.join(Stock).where(*self._product_filters())
        return await self.session.scalar(statement) or 0

    async def list_stock_explanations(self, symbol: str, limit: int = 10) -> list[AIExplanation]:
        statement = (
            select(AIExplanation)
            .join(Stock, AIExplanation.stock_id == Stock.id)
            .where(Stock.symbol == symbol.upper())
            .options(selectinload(AIExplanation.stock))
            .order_by(AIExplanation.trade_date.desc())
            .limit(limit)
        )
        return list(await self.session.scalars(statement))
