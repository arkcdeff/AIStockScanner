from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select

from app.models.market_intelligence_snapshot import MarketIntelligenceSnapshot
from app.repositories.base import BaseRepository


class MarketIntelligenceSnapshotRepository(BaseRepository["MarketIntelligenceSnapshot"]):
    def __init__(self, session: Any) -> None:
        super().__init__(MarketIntelligenceSnapshot, session)

    async def get_by_date(self, market_date: date) -> MarketIntelligenceSnapshot | None:
        statement = select(MarketIntelligenceSnapshot).where(
            MarketIntelligenceSnapshot.market_date == market_date
        )
        result = await self.session.scalars(statement)
        return result.one_or_none()

    async def get_latest(self) -> MarketIntelligenceSnapshot | None:
        statement = select(MarketIntelligenceSnapshot).where(
            MarketIntelligenceSnapshot.market_date <= date.today()
        )
        statement = statement.order_by(
            MarketIntelligenceSnapshot.generated_at.desc()
        )
        result = await self.session.scalars(statement)
        return result.first()

    async def list_history(
        self, start_date: date, end_date: date
    ) -> list[MarketIntelligenceSnapshot]:
        statement = select(MarketIntelligenceSnapshot).where(
            MarketIntelligenceSnapshot.market_date >= start_date,
            MarketIntelligenceSnapshot.market_date <= end_date,
            MarketIntelligenceSnapshot.market_date <= date.today(),
        )
        statement = statement.order_by(MarketIntelligenceSnapshot.market_date.desc())
        result = await self.session.scalars(statement)
        return list(result.all())

    async def upsert(
        self,
        market_date: date,
        generated_at: date | None = None,
        price_action_score: float | None = None,
        breadth_score: float | None = None,
        sector_score: float | None = None,
        institutional_score: float | None = None,
        volatility_score: float | None = None,
        global_score: float | None = None,
        gift_nifty_score: float | None = None,
        currency_score: float | None = None,
        crude_score: float | None = None,
        macro_score: float | None = None,
        composite_score: float | None = None,
        confidence_score: float | None = None,
        regime: str | None = None,
        bullish_factors: dict | None = None,
        bearish_factors: dict | None = None,
        risk_factors: dict | None = None,
        missing_factors: dict | None = None,
        data_completeness_percent: float | None = None,
        stale_factor_count: int = 0,
        missing_factor_count: int = 0,
    ) -> MarketIntelligenceSnapshot:
        existing = await self.get_by_date(market_date)
        if existing:
            existing.generated_at = generated_at or existing.generated_at
            existing.price_action_score = price_action_score
            existing.breadth_score = breadth_score
            existing.sector_score = sector_score
            existing.institutional_score = institutional_score
            existing.volatility_score = volatility_score
            existing.global_score = global_score
            existing.gift_nifty_score = gift_nifty_score
            existing.currency_score = currency_score
            existing.crude_score = crude_score
            existing.macro_score = macro_score
            existing.composite_score = composite_score
            existing.confidence_score = confidence_score
            existing.regime = regime
            existing.bullish_factors_json = bullish_factors
            existing.bearish_factors_json = bearish_factors
            existing.risk_factors_json = risk_factors
            existing.missing_factors_json = missing_factors
            existing.data_completeness_percent = data_completeness_percent
            existing.stale_factor_count = stale_factor_count
            existing.missing_factor_count = missing_factor_count
            await self.session.flush()
            return existing
        else:
            new = MarketIntelligenceSnapshot(
                market_date=market_date,
                generated_at=generated_at or market_date,
                price_action_score=price_action_score,
                breadth_score=breadth_score,
                sector_score=sector_score,
                institutional_score=institutional_score,
                volatility_score=volatility_score,
                global_score=global_score,
                gift_nifty_score=gift_nifty_score,
                currency_score=currency_score,
                crude_score=crude_score,
                macro_score=macro_score,
                composite_score=composite_score,
                confidence_score=confidence_score,
                regime=regime,
                bullish_factors_json=bullish_factors,
                bearish_factors_json=bearish_factors,
                risk_factors_json=risk_factors,
                missing_factors_json=missing_factors,
                data_completeness_percent=data_completeness_percent,
                stale_factor_count=stale_factor_count,
                missing_factor_count=missing_factor_count,
            )
            self.session.add(new)
            await self.session.flush()
            return new