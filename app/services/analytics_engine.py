from __future__ import annotations

from decimal import Decimal
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.models.technical_analysis import TechnicalAnalysis


class AnalyticsEngineError(Exception):
    pass


class AnalyticsEngine:
    """Deterministic technical analytics engine.

    Computes composite scores (trend / momentum / volume / delivery /
    volatility / overall), support & resistance, classic and Fibonacci
    pivot points, relative volume, delivery strength, ratings, and
    trending / breakout flags for the latest trading day of each stock.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ public

    async def calculate_for_stock(self, symbol: str) -> TechnicalAnalysis | None:
        stock = (
            await self.session.execute(select(Stock).where(Stock.symbol == symbol.upper()))
        ).scalar_one_or_none()
        if stock is None:
            raise AnalyticsEngineError(f"Stock not found: {symbol}")

        prices = (
            await self.session.scalars(
                select(DailyPrice)
                .where(DailyPrice.stock_id == stock.id)
                .order_by(DailyPrice.trade_date.asc())
            )
        ).all()
        if not prices:
            raise AnalyticsEngineError(f"No price data found for symbol: {symbol}")

        indicators = (
            await self.session.scalars(
                select(IndicatorResult)
                .where(IndicatorResult.stock_id == stock.id)
                .order_by(IndicatorResult.trade_date.asc())
            )
        ).all()
        if not indicators:
            raise AnalyticsEngineError(f"No indicator data found for symbol: {symbol}")

        return await self._compute_and_upsert(stock.id, list(prices), list(indicators))

    async def calculate_all(self, batch_size: int = 50) -> dict[str, int]:
        symbols = (
            await self.session.scalars(
                select(Stock.symbol).where(
                    Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS)
                )
            )
        ).all()
        total_calculated = 0
        total_skipped = 0

        for symbol in symbols:
            try:
                result = await self.calculate_for_stock(symbol)
                if result is not None:
                    total_calculated += 1
                else:
                    total_skipped += 1
            except AnalyticsEngineError as exc:
                logger.warning("Skipping analytics for {}: {}", symbol, exc)
                total_skipped += 1

        return {"calculated": total_calculated, "skipped": total_skipped}

    async def get_analytics(self, symbol: str) -> TechnicalAnalysis | None:
        return await self.session.scalar(
            select(TechnicalAnalysis)
            .options(selectinload(TechnicalAnalysis.stock))
            .join(Stock, TechnicalAnalysis.stock_id == Stock.id)
            .where(Stock.symbol == symbol.upper())
            .order_by(TechnicalAnalysis.trade_date.desc())
            .limit(1)
        )

    async def top_rated(self, limit: int = 10) -> list[TechnicalAnalysis]:
        rows = (
            await self.session.scalars(
                select(TechnicalAnalysis)
                .options(selectinload(TechnicalAnalysis.stock))
                .join(Stock, TechnicalAnalysis.stock_id == Stock.id)
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .order_by(TechnicalAnalysis.overall_score.desc())
                .limit(limit)
            )
        ).all()
        return list(rows)

    async def trending(self, limit: int = 10) -> list[TechnicalAnalysis]:
        rows = (
            await self.session.scalars(
                select(TechnicalAnalysis)
                .options(selectinload(TechnicalAnalysis.stock))
                .join(Stock, TechnicalAnalysis.stock_id == Stock.id)
                .where(TechnicalAnalysis.is_trending.is_(True))
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .order_by(TechnicalAnalysis.overall_score.desc())
                .limit(limit)
            )
        ).all()
        return list(rows)

    async def breakouts(self, limit: int = 10) -> list[TechnicalAnalysis]:
        rows = (
            await self.session.scalars(
                select(TechnicalAnalysis)
                .options(selectinload(TechnicalAnalysis.stock))
                .join(Stock, TechnicalAnalysis.stock_id == Stock.id)
                .where(TechnicalAnalysis.is_breakout.is_(True))
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .order_by(TechnicalAnalysis.overall_score.desc())
                .limit(limit)
            )
        ).all()
        return list(rows)

    # ------------------------------------------------------------------ compute

    async def _compute_and_upsert(
        self,
        stock_id: int,
        prices: list[DailyPrice],
        indicators: list[IndicatorResult],
    ) -> TechnicalAnalysis | None:
        latest_price = prices[-1]
        latest_indicator = indicators[-1]
        payload = self._compute(stock_id, latest_price, prices, latest_indicator)

        base_insert = insert(TechnicalAnalysis).values(**payload)
        insert_stmt = base_insert.on_conflict_do_update(
            constraint="uq_technical_analysis_stock_date",
            set_={key: base_insert.excluded[key] for key in payload if key != "stock_id"},
        ).returning(TechnicalAnalysis.id)
        row_id = (await self.session.execute(insert_stmt)).scalar_one()
        return await self.session.get(TechnicalAnalysis, row_id)

    def _compute(
        self,
        stock_id: int,
        latest: DailyPrice,
        prices: list[DailyPrice],
        indicator: IndicatorResult,
    ) -> dict[str, Any]:
        history = prices[:-1]
        close = float(latest.close)

        support, resistance = self._support_resistance(history, close)
        breakout_high = self._raw_20d_high(history)
        classic = self._classic_pivots(history)
        fib = self._fibonacci_pivots(history)
        relative_volume = self._relative_volume(latest, history)
        delivery_strength = self._delivery_strength(latest, history)

        trend_score = self._trend_score(latest, indicator)
        momentum_score = self._momentum_score(indicator)
        volume_score = self._volume_score(latest, indicator, history)
        delivery_score = self._delivery_score(latest, history)
        volatility_score, atr_pct = self._volatility_score(latest, indicator)

        overall_score = round(
            0.25 * trend_score
            + 0.25 * momentum_score
            + 0.20 * volume_score
            + 0.15 * delivery_score
            + 0.15 * volatility_score
        )
        overall_score = max(0, min(100, overall_score))

        is_breakout = bool(
            breakout_high is not None
            and close > breakout_high
            and relative_volume is not None
            and relative_volume >= 1.5
        )
        is_trending = bool(
            overall_score >= 60
            and trend_score >= 55
            and (
                (indicator.ema_20 is not None and close > float(indicator.ema_20))
                or indicator.supertrend_direction == 1
            )
        )

        return {
            "stock_id": stock_id,
            "trade_date": latest.trade_date,
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "volume_score": volume_score,
            "delivery_score": delivery_score,
            "volatility_score": volatility_score,
            "overall_score": overall_score,
            "support": self._to_decimal(support),
            "resistance": self._to_decimal(resistance),
            "pivot": self._to_decimal(classic["pivot"]),
            "pivot_r1": self._to_decimal(classic["r1"]),
            "pivot_r2": self._to_decimal(classic["r2"]),
            "pivot_r3": self._to_decimal(classic["r3"]),
            "pivot_s1": self._to_decimal(classic["s1"]),
            "pivot_s2": self._to_decimal(classic["s2"]),
            "pivot_s3": self._to_decimal(classic["s3"]),
            "fib_pivot": self._to_decimal(fib["pivot"]),
            "fib_r1": self._to_decimal(fib["r1"]),
            "fib_r2": self._to_decimal(fib["r2"]),
            "fib_r3": self._to_decimal(fib["r3"]),
            "fib_s1": self._to_decimal(fib["s1"]),
            "fib_s2": self._to_decimal(fib["s2"]),
            "fib_s3": self._to_decimal(fib["s3"]),
            "relative_volume": relative_volume,
            "delivery_strength": delivery_strength,
            "trend_rating": self._trend_rating(trend_score),
            "momentum_rating": self._momentum_rating(momentum_score),
            "volatility_rating": self._volatility_rating(atr_pct),
            "is_trending": is_trending,
            "is_breakout": is_breakout,
        }

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _support_resistance(
        history: list[DailyPrice], close: float
    ) -> tuple[float | None, float | None]:
        lows = [float(p.low) for p in history if p.low is not None]
        highs = [float(p.high) for p in history if p.high is not None]
        support = min(lows) if lows else None
        resistance = max(highs) if highs else None
        if support is not None and close < support:
            support = close * 0.98
        if resistance is not None and close > resistance:
            resistance = close * 1.02
        return support, resistance

    @staticmethod
    def _raw_20d_high(history: list[DailyPrice]) -> float | None:
        highs = [float(p.high) for p in history if p.high is not None]
        return max(highs) if highs else None

    @staticmethod
    def _classic_pivots(history: list[DailyPrice]) -> dict[str, float | None]:
        if len(history) < 1:
            return {key: None for key in ("pivot", "r1", "r2", "r3", "s1", "s2", "s3")}
        previous = history[-1]
        high = float(previous.high)
        low = float(previous.low)
        close = float(previous.close)
        pivot = (high + low + close) / 3
        return {
            "pivot": pivot,
            "r1": 2 * pivot - low,
            "r2": pivot + (high - low),
            "r3": high + 2 * (pivot - low),
            "s1": 2 * pivot - high,
            "s2": pivot - (high - low),
            "s3": low - 2 * (high - pivot),
        }

    @staticmethod
    def _fibonacci_pivots(history: list[DailyPrice]) -> dict[str, float | None]:
        if len(history) < 1:
            return {key: None for key in ("pivot", "r1", "r2", "r3", "s1", "s2", "s3")}
        previous = history[-1]
        high = float(previous.high)
        low = float(previous.low)
        close = float(previous.close)
        range_span = high - low
        pivot = (high + low + close) / 3
        return {
            "pivot": pivot,
            "r1": pivot + 0.382 * range_span,
            "r2": pivot + 0.618 * range_span,
            "r3": pivot + 1.0 * range_span,
            "s1": pivot - 0.382 * range_span,
            "s2": pivot - 0.618 * range_span,
            "s3": pivot - 1.0 * range_span,
        }

    @staticmethod
    def _relative_volume(latest: DailyPrice, history: list[DailyPrice]) -> float | None:
        volumes = [int(p.volume) for p in history if p.volume is not None]
        if not volumes:
            return None
        average = sum(volumes) / len(volumes)
        if average <= 0:
            return None
        return round(int(latest.volume) / average, 2)

    @staticmethod
    def _delivery_strength(latest: DailyPrice, history: list[DailyPrice]) -> float | None:
        if latest.delivery_percentage is None:
            return None
        deliveries = [
            float(p.delivery_percentage) for p in history if p.delivery_percentage is not None
        ]
        if not deliveries:
            return None
        average = sum(deliveries) / len(deliveries)
        if average <= 0:
            return None
        return round(float(latest.delivery_percentage) / average, 2)

    @staticmethod
    def _trend_score(latest: DailyPrice, indicator: IndicatorResult) -> int:
        score = 0
        close = float(latest.close)
        if indicator.ema_20 is not None and close > float(indicator.ema_20):
            score += 25
        if indicator.ema_50 is not None and close > float(indicator.ema_50):
            score += 15
        if indicator.ema_200 is not None and close > float(indicator.ema_200):
            score += 15
        if (
            indicator.ema_20 is not None
            and indicator.ema_50 is not None
            and float(indicator.ema_20) > float(indicator.ema_50)
        ):
            score += 20
        if (
            indicator.ema_50 is not None
            and indicator.ema_200 is not None
            and float(indicator.ema_50) > float(indicator.ema_200)
        ):
            score += 10
        if indicator.adx is not None:
            if float(indicator.adx) >= 25:
                score += 10
            elif float(indicator.adx) >= 20:
                score += 5
        if indicator.supertrend_direction == 1:
            score += 5
        return max(0, min(100, score))

    @staticmethod
    def _momentum_score(indicator: IndicatorResult) -> int:
        score = 0
        if indicator.rsi_14 is not None:
            rsi = float(indicator.rsi_14)
            if 55 <= rsi <= 70:
                score += 30
            elif 50 <= rsi < 55:
                score += 25
            elif rsi > 70:
                score += 20
            elif 40 <= rsi < 50:
                score += 15
            elif 30 <= rsi < 40:
                score += 10
            else:
                score += 5
        if indicator.macd is not None and indicator.macd_signal is not None:
            if float(indicator.macd) > float(indicator.macd_signal):
                score += 25
            else:
                score += 5
            if float(indicator.macd) > 0:
                score += 10
        if indicator.stochastic_k is not None and indicator.stochastic_d is not None:
            if float(indicator.stochastic_k) > float(indicator.stochastic_d):
                score += 15
            if float(indicator.stochastic_k) > 50:
                score += 10
        return max(0, min(100, score))

    @staticmethod
    def _volume_score(
        latest: DailyPrice,
        indicator: IndicatorResult,
        history: list[DailyPrice],
    ) -> int:
        score = 0
        volume = int(latest.volume)
        relative_volume = AnalyticsEngine._relative_volume(latest, history)
        if relative_volume is not None:
            if relative_volume >= 2.0:
                score += 40
            elif relative_volume >= 1.5:
                score += 30
            elif relative_volume >= 1.0:
                score += 20
            else:
                score += 10
        volumes = [int(p.volume) for p in history if p.volume is not None]
        if volumes and volume >= max(volumes):
            score += 30
        elif volumes and volume >= 0.8 * max(volumes):
            score += 15
        if indicator.obv is not None and indicator.obv > 0:
            score += 15
        return max(0, min(100, score))

    @staticmethod
    def _delivery_score(latest: DailyPrice, history: list[DailyPrice]) -> int:
        if latest.delivery_percentage is None:
            return 0
        delivery = float(latest.delivery_percentage)
        score = 0
        if delivery >= 60:
            score += 40
        elif delivery >= 50:
            score += 30
        elif delivery >= 40:
            score += 20
        else:
            score += 10
        deliveries = [
            float(p.delivery_percentage) for p in history if p.delivery_percentage is not None
        ]
        if deliveries:
            average = sum(deliveries) / len(deliveries)
            if delivery > average:
                score += 30
            if delivery >= max(deliveries):
                score += 30
        return max(0, min(100, score))

    @staticmethod
    def _volatility_score(
        latest: DailyPrice, indicator: IndicatorResult
    ) -> tuple[int, float | None]:
        if indicator.atr is None or latest.close is None:
            return 50, None
        atr_pct = float(indicator.atr) / float(latest.close) * 100
        if atr_pct < 1.0:
            score = 40
        elif atr_pct <= 2.5:
            score = 85
        elif atr_pct <= 4.0:
            score = 65
        elif atr_pct <= 6.0:
            score = 40
        else:
            score = 20
        return score, round(atr_pct, 2)

    @staticmethod
    def _trend_rating(score: int) -> str:
        if score >= 75:
            return "Strong Uptrend"
        if score >= 60:
            return "Uptrend"
        if score >= 45:
            return "Neutral"
        if score >= 30:
            return "Downtrend"
        return "Strong Downtrend"

    @staticmethod
    def _momentum_rating(score: int) -> str:
        if score >= 75:
            return "Strong Bullish"
        if score >= 60:
            return "Bullish"
        if score >= 45:
            return "Neutral"
        if score >= 30:
            return "Bearish"
        return "Strong Bearish"

    @staticmethod
    def _volatility_rating(atr_pct: float | None) -> str:
        if atr_pct is None:
            return "Unknown"
        if atr_pct >= 4.0:
            return "High"
        if atr_pct >= 1.5:
            return "Moderate"
        return "Low"

    @staticmethod
    def _to_decimal(value: float | None) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(round(value, 4)))
