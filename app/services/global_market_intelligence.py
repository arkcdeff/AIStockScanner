from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.index_catalog import (
    ASIAN_INDEX_CODES,
    GLOBAL_INTELLIGENCE_INDEX_CODES,
    US_INDEX_CODES,
)
from app.market_intelligence.session_dates import SESSION_POLICIES, session_cutoff_date
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.services.index_price_action import IndexPriceAction

SCORE_MIN = Decimal("-100")
SCORE_MAX = Decimal("100")

DIRECTION_THRESHOLDS: dict[str, Decimal] = {
    "strong_bullish": Decimal("60"),
    "bullish": Decimal("25"),
    "bearish": Decimal("-25"),
    "strong_bearish": Decimal("-60"),
}

INDIVIDUAL_SCORE_WEIGHTS: dict[str, Decimal] = {
    "return_1": Decimal("0.25"),
    "return_5": Decimal("0.20"),
    "return_20": Decimal("0.15"),
    "trend": Decimal("0.15"),
    "price_action": Decimal("0.25"),
}

RETURN_SIGNAL_MULTIPLIERS: dict[int, Decimal] = {
    1: Decimal("20"),
    5: Decimal("10"),
    20: Decimal("5"),
}

TREND_SIGNALS: dict[str, Decimal] = {
    "strong_bullish": Decimal("100"),
    "bullish": Decimal("60"),
    "neutral": Decimal("0"),
    "bearish": Decimal("-60"),
    "strong_bearish": Decimal("-100"),
}

REGIONAL_WEIGHTS: dict[str, dict[str, Decimal]] = {
    "us": {
        "SP500": Decimal("0.40"),
        "NASDAQ_100": Decimal("0.35"),
        "DOW_JONES": Decimal("0.25"),
    },
    "asia": {
        "NIKKEI_225": Decimal("0.30"),
        "HANG_SENG": Decimal("0.25"),
        "SHANGHAI_COMPOSITE": Decimal("0.25"),
        "KOSPI": Decimal("0.20"),
    },
}

GLOBAL_REGION_WEIGHTS: dict[str, Decimal] = {
    "us": Decimal("0.50"),
    "asia": Decimal("0.50"),
}

FRESHNESS_FACTORS: dict[str, Decimal] = {
    "live": Decimal("1.00"),
    "recent": Decimal("0.85"),
    "stale": Decimal("0.40"),
    "missing": Decimal("0.00"),
}


def _clamp(value: Decimal, low: Decimal = SCORE_MIN, high: Decimal = SCORE_MAX) -> Decimal:
    return max(low, min(high, value))


def _rounded(value: Decimal, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))


def classify_direction(score: Decimal | float | int | None) -> str | None:
    if score is None:
        return None
    value = Decimal(str(score))
    if value >= DIRECTION_THRESHOLDS["strong_bullish"]:
        return "strong_bullish"
    if value >= DIRECTION_THRESHOLDS["bullish"]:
        return "bullish"
    if value <= DIRECTION_THRESHOLDS["strong_bearish"]:
        return "strong_bearish"
    if value <= DIRECTION_THRESHOLDS["bearish"]:
        return "bearish"
    return "neutral"


class _InMemoryPriceRepository:
    """Repository-compatible view over the service's single bulk DB read."""

    def __init__(self, histories: dict[str, list[MarketIndexPrice]]) -> None:
        self._histories = histories

    async def get_date_range_by_index_code(
        self, index_code: str, start_date: date, end_date: date
    ) -> list[MarketIndexPrice]:
        return [
            row
            for row in self._histories.get(index_code, [])
            if start_date <= row.trade_date <= end_date
        ]


class GlobalMarketIntelligence:
    """Deterministic US, Asian, and global market context for an aware instant."""

    _LOOKBACK_DAYS = 400

    def __init__(
        self,
        session: AsyncSession,
        *,
        price_repo: MarketIndexPriceRepository | None = None,
    ) -> None:
        self._price_repo = price_repo or MarketIndexPriceRepository(session)

    async def evaluate(self, evaluation_at: datetime) -> dict[str, Any]:
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("evaluation_at must be timezone-aware")

        cutoffs = {
            code: session_cutoff_date(code, evaluation_at)
            for code in GLOBAL_INTELLIGENCE_INDEX_CODES
        }
        end_date = max(cutoffs.values())
        start_date = min(cutoffs.values()) - timedelta(days=self._LOOKBACK_DAYS)
        rows = await self._price_repo.get_ranges_by_index_codes(
            GLOBAL_INTELLIGENCE_INDEX_CODES,
            start_date,
            end_date,
        )

        histories: dict[str, list[MarketIndexPrice]] = defaultdict(list)
        for row in rows:
            code = row.market_index.code
            if row.trade_date <= cutoffs[code]:
                histories[code].append(row)
        for history in histories.values():
            history.sort(key=lambda item: item.trade_date, reverse=True)

        memory_repo = _InMemoryPriceRepository(dict(histories))
        markets: dict[str, dict[str, Any]] = {}
        for code in GLOBAL_INTELLIGENCE_INDEX_CODES:
            markets[code] = await self._market_context(
                code,
                cutoffs[code],
                histories.get(code, []),
                memory_repo,
            )

        us = self._regional_context("us", US_INDEX_CODES, markets)
        asia = self._regional_context("asia", ASIAN_INDEX_CODES, markets)
        global_score, global_weights = self._weighted_score(
            {
                "us": us["score"],
                "asia": asia["score"],
            },
            GLOBAL_REGION_WEIGHTS,
        )
        completeness = Decimal(sum(market["score"] is not None for market in markets.values()))
        completeness = completeness / Decimal(len(GLOBAL_INTELLIGENCE_INDEX_CODES)) * 100
        confidence = self._confidence(markets, global_score, completeness)

        return {
            "evaluation_at": evaluation_at.isoformat(),
            "markets": markets,
            "us_context": us,
            "asian_context": asia,
            "global_score": _rounded(global_score) if global_score is not None else None,
            "global_direction": classify_direction(global_score),
            "score_breakdown": {
                "regional_scores": {"us": us["score"], "asia": asia["score"]},
                "normalized_region_weights": global_weights,
            },
            "completeness": _rounded(completeness),
            "confidence": _rounded(confidence),
        }

    async def _market_context(
        self,
        code: str,
        cutoff: date,
        history: list[MarketIndexPrice],
        memory_repo: _InMemoryPriceRepository,
    ) -> dict[str, Any]:
        policy = SESSION_POLICIES[code]
        base = {
            "index_code": code,
            "timezone": policy.timezone,
            "close_time": policy.close_time.isoformat(timespec="minutes"),
            "cutoff_date": cutoff.isoformat(),
            "market_date": None,
            "source_name": None,
            "source_reference": None,
            "freshness": "missing",
            "returns": {1: None, 5: None, 20: None},
            "trend": None,
            "price_action_score": None,
            "score": None,
            "direction": None,
            "score_breakdown": {},
        }
        if not history:
            return base

        action = IndexPriceAction(code, cutoff)
        action.set_repo(memory_repo)  # type: ignore[arg-type]
        returns = await action.returns((1, 5, 20))
        trend = (await action.trend()).get("trend")
        price_action = await action.price_action_score()
        price_action_score = price_action.get("price_action_score")

        components: dict[str, Decimal | None] = {}
        for sessions in (1, 5, 20):
            value = returns.get(sessions)
            components[f"return_{sessions}"] = (
                _clamp(Decimal(str(value)) * RETURN_SIGNAL_MULTIPLIERS[sessions])
                if value is not None
                else None
            )
        components["trend"] = TREND_SIGNALS.get(trend) if trend is not None else None
        components["price_action"] = (
            Decimal(str(price_action_score)) if price_action_score is not None else None
        )
        score, normalized_weights = self._weighted_score(components, INDIVIDUAL_SCORE_WEIGHTS)
        latest = history[0]
        base.update(
            {
                "market_date": latest.trade_date.isoformat(),
                "source_name": latest.source_name,
                "source_reference": latest.source_reference,
                "freshness": latest.freshness_status,
                "returns": returns,
                "trend": trend,
                "price_action_score": price_action_score,
                "score": _rounded(score) if score is not None else None,
                "direction": classify_direction(score),
                "score_breakdown": {
                    "component_signals": {
                        key: _rounded(value) if value is not None else None
                        for key, value in components.items()
                    },
                    "normalized_weights": normalized_weights,
                    "price_action": price_action.get("score_breakdown", {}),
                },
            }
        )
        return base

    def _regional_context(
        self,
        region: str,
        codes: tuple[str, ...],
        markets: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        scores = {code: markets[code]["score"] for code in codes}
        score, normalized_weights = self._weighted_score(scores, REGIONAL_WEIGHTS[region])
        one_day = [markets[code]["returns"][1] for code in codes]
        available_returns = [value for value in one_day if value is not None]
        positive = sum(value > 0 for value in available_returns)
        negative = sum(value < 0 for value in available_returns)
        flat = sum(value == 0 for value in available_returns)
        breadth_percent = (
            Decimal(positive - negative) / Decimal(len(available_returns)) * 100
            if available_returns
            else None
        )
        return {
            "score": _rounded(score) if score is not None else None,
            "direction": classify_direction(score),
            "available_markets": sum(value is not None for value in scores.values()),
            "expected_markets": len(codes),
            "normalized_weights": normalized_weights,
            "breadth": {
                "positive": positive,
                "negative": negative,
                "flat": flat,
                "available": len(available_returns),
                "breadth_percent": (
                    _rounded(breadth_percent) if breadth_percent is not None else None
                ),
            },
        }

    @staticmethod
    def _weighted_score(
        values: dict[str, Decimal | float | int | None],
        configured_weights: dict[str, Decimal],
    ) -> tuple[Decimal | None, dict[str, float]]:
        available = {
            key: Decimal(str(value))
            for key, value in values.items()
            if value is not None and key in configured_weights
        }
        if not available:
            return None, {}
        total_weight = sum(configured_weights[key] for key in available)
        normalized = {key: configured_weights[key] / total_weight for key in available}
        score = sum(available[key] * normalized[key] for key in available)
        return _clamp(score), {key: _rounded(weight, 4) for key, weight in normalized.items()}

    @staticmethod
    def _confidence(
        markets: dict[str, dict[str, Any]],
        global_score: Decimal | None,
        completeness: Decimal,
    ) -> Decimal:
        available = [market for market in markets.values() if market["score"] is not None]
        if not available or global_score is None:
            return Decimal("0")
        freshness = sum(
            FRESHNESS_FACTORS.get(market["freshness"], Decimal("0")) for market in available
        ) / Decimal(len(available))
        dispersion = sum(
            abs(Decimal(str(market["score"])) - global_score) for market in available
        ) / Decimal(len(available))
        agreement = _clamp(Decimal("100") - dispersion, Decimal("0"), Decimal("100"))
        confidence = (
            completeness * Decimal("0.55")
            + freshness * Decimal("100") * Decimal("0.25")
            + agreement * Decimal("0.20")
        )
        return _clamp(confidence, Decimal("0"), Decimal("100"))