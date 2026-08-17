from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.index_catalog import (
    BRENT_CRUDE_INDEX_CODE,
    INDIA_VIX_INDEX_CODE,
    MARKET_FACTOR_INDEX_CODES,
    USD_INR_INDEX_CODE,
)
from app.market_intelligence.providers.base import MarketProvider, ProviderResult
from app.market_intelligence.providers.manual_market_factor_provider import (
    EXPECTED_IDENTITIES,
)
from app.market_intelligence.session_dates import (
    INDIA_TIMEZONE,
    date_only_publication_cutoff_date,
    indian_cash_reference_cutoff_date,
)
from app.models.market_index import MarketIndex
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.services.index_price_action import (
    price_series_moving_averages,
    price_series_returns,
    price_series_trend,
)

SCORE_MIN = Decimal("-100")
SCORE_MAX = Decimal("100")

FACTOR_DIRECTION_THRESHOLDS = {
    "strong_positive": Decimal("60"),
    "positive": Decimal("25"),
    "negative": Decimal("-25"),
    "strong_negative": Decimal("-60"),
}
VIX_REGIME_THRESHOLDS = {
    "very_low": Decimal("12"),
    "normal": Decimal("18"),
    "elevated": Decimal("25"),
    "high": Decimal("35"),
}
FRESHNESS_FACTORS = {
    "live": Decimal("1"),
    "recent": Decimal("0.85"),
    "stale": Decimal("0.40"),
    "missing": Decimal("0"),
}
RETURN_MULTIPLIERS = {
    INDIA_VIX_INDEX_CODE: {
        1: Decimal("-5"),
        5: Decimal("-3"),
        20: Decimal("-2"),
    },
    USD_INR_INDEX_CODE: {
        1: Decimal("-15"),
        5: Decimal("-10"),
        20: Decimal("-6"),
        60: Decimal("-4"),
    },
    BRENT_CRUDE_INDEX_CODE: {
        1: Decimal("-5"),
        5: Decimal("-3"),
        20: Decimal("-2"),
        60: Decimal("-1.25"),
    },
}
RETURN_WEIGHTS = {
    INDIA_VIX_INDEX_CODE: {1: Decimal("0.35"), 5: Decimal("0.35"), 20: Decimal("0.30")},
    USD_INR_INDEX_CODE: {
        1: Decimal("0.20"),
        5: Decimal("0.25"),
        20: Decimal("0.30"),
        60: Decimal("0.25"),
    },
    BRENT_CRUDE_INDEX_CODE: {
        1: Decimal("0.20"),
        5: Decimal("0.25"),
        20: Decimal("0.30"),
        60: Decimal("0.25"),
    },
}
TREND_RISK_SIGNALS = {
    "strong_bullish": Decimal("-100"),
    "bullish": Decimal("-60"),
    "neutral": Decimal("0"),
    "bearish": Decimal("60"),
    "strong_bearish": Decimal("100"),
}
VIX_PERCENTILE_MIN_SESSIONS = 20


def classify_factor_direction(score: Decimal | float | int | None) -> str | None:
    """Classify India-context contribution; positive means more supportive."""
    if score is None:
        return None
    value = Decimal(str(score))
    if value >= FACTOR_DIRECTION_THRESHOLDS["strong_positive"]:
        return "strong_positive"
    if value >= FACTOR_DIRECTION_THRESHOLDS["positive"]:
        return "positive"
    if value <= FACTOR_DIRECTION_THRESHOLDS["strong_negative"]:
        return "strong_negative"
    if value <= FACTOR_DIRECTION_THRESHOLDS["negative"]:
        return "negative"
    return "neutral"


def classify_vix_regime(level: Decimal | float | int | None) -> str | None:
    if level is None:
        return None
    value = Decimal(str(level))
    if value < VIX_REGIME_THRESHOLDS["very_low"]:
        return "very_low"
    if value < VIX_REGIME_THRESHOLDS["normal"]:
        return "normal"
    if value < VIX_REGIME_THRESHOLDS["elevated"]:
        return "elevated"
    if value < VIX_REGIME_THRESHOLDS["high"]:
        return "high"
    return "extreme"


class ExternalMarketFactorIntelligence:
    """Independent India VIX, USD/INR spot, and Europe Brent spot context."""

    _LOOKBACK_DAYS = 550

    def __init__(
        self,
        session: AsyncSession,
        providers: tuple[MarketProvider, ...] = (),
        *,
        price_repo: MarketIndexPriceRepository | None = None,
    ) -> None:
        self._providers = providers
        self._price_repo = price_repo or MarketIndexPriceRepository(session)

    async def evaluate(self, evaluation_at: datetime) -> dict[str, Any]:
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("evaluation_at must be timezone-aware")
        cutoffs = {
            INDIA_VIX_INDEX_CODE: indian_cash_reference_cutoff_date(evaluation_at),
            USD_INR_INDEX_CODE: date_only_publication_cutoff_date(
                INDIA_TIMEZONE, evaluation_at
            ),
            BRENT_CRUDE_INDEX_CODE: date_only_publication_cutoff_date("UTC", evaluation_at),
        }
        rows = await self._price_repo.get_ranges_by_index_codes(
            MARKET_FACTOR_INDEX_CODES,
            min(cutoffs.values()) - timedelta(days=self._LOOKBACK_DAYS),
            max(cutoffs.values()),
        )
        histories: dict[str, list[MarketIndexPrice]] = defaultdict(list)
        for row in rows:
            code = row.market_index.code
            if code not in cutoffs or row.trade_date > cutoffs[code]:
                continue
            local_date = evaluation_at.astimezone(
                ZoneInfo(INDIA_TIMEZONE if code != BRENT_CRUDE_INDEX_CODE else "UTC")
            ).date()
            if row.fetched_at is not None and row.fetched_at > local_date:
                continue
            histories[code].append(row)
        for history in histories.values():
            history.sort(key=lambda row: row.trade_date, reverse=True)

        resolved: dict[str, list[MarketIndexPrice]] = {}
        resolutions: dict[str, dict[str, Any]] = {}
        start_date = min(cutoffs.values()) - timedelta(days=self._LOOKBACK_DAYS)
        for code in MARKET_FACTOR_INDEX_CODES:
            provider_rows, attempts, provider_level = await self._provider_history(
                code, start_date, cutoffs[code], evaluation_at
            )
            stored_rows = histories.get(code, [])
            if provider_rows:
                resolved[code] = self._merge_histories(provider_rows, stored_rows)
                fallback_level = provider_level
            else:
                resolved[code] = stored_rows
                fallback_level = self._stored_fallback_level(stored_rows, cutoffs[code])
            resolved[code].sort(key=lambda row: row.trade_date, reverse=True)
            resolutions[code] = {
                "fallback_level": fallback_level,
                "provider_attempts": self._public_attempts(attempts),
            }

        vix = self._vix_context(resolved[INDIA_VIX_INDEX_CODE], cutoffs)
        currency = self._trend_factor_context(
            USD_INR_INDEX_CODE,
            resolved[USD_INR_INDEX_CODE],
            cutoffs[USD_INR_INDEX_CODE],
        )
        crude = self._trend_factor_context(
            BRENT_CRUDE_INDEX_CODE,
            resolved[BRENT_CRUDE_INDEX_CODE],
            cutoffs[BRENT_CRUDE_INDEX_CODE],
        )
        for code, context in (
            (INDIA_VIX_INDEX_CODE, vix),
            (USD_INR_INDEX_CODE, currency),
            (BRENT_CRUDE_INDEX_CODE, crude),
        ):
            context.update(resolutions[code])
        factors = {"india_vix": vix, "usd_inr": currency, "brent_crude": crude}
        completeness = sum(
            (Decimal(str(item["completeness"])) for item in factors.values()),
            Decimal("0"),
        ) / Decimal(len(factors))
        confidence = sum(
            (Decimal(str(item["confidence"])) for item in factors.values()), Decimal("0")
        ) / Decimal(len(factors))
        return {
            "evaluation_at": evaluation_at.isoformat(),
            "factors": factors,
            "completeness": _number(_clamp(completeness, Decimal("0"), Decimal("100"))),
            "confidence": _number(_clamp(confidence, Decimal("0"), Decimal("100"))),
            "freshness": _worst_freshness(item["freshness"] for item in factors.values()),
            "limitations": {
                "aggregation": (
                    "Quality summary only; no final composite or market regime is computed."
                ),
                "timestamp_precision": (
                    "Persisted histories are date-only. USD/INR and Brent conservatively "
                    "exclude the "
                    "evaluation local date; India VIX uses the completed NSE cash-session cutoff."
                ),
                "interpretation": (
                    "Scores are contextual India risk inputs, not universal directional "
                    "predictions."
                ),
            },
        }

    def _vix_context(
        self,
        history: list[MarketIndexPrice],
        cutoffs: dict[str, date],
    ) -> dict[str, Any]:
        base = self._empty_context(INDIA_VIX_INDEX_CODE, cutoffs[INDIA_VIX_INDEX_CODE])
        base.update(
            {
                "returns": {1: None, 5: None, 20: None},
                "historical_percentile": None,
                "historical_rank": None,
                "regime": None,
            }
        )
        if not history:
            return base
        returns = price_series_returns(history, (1, 5, 20))
        latest = history[0]
        level = Decimal(str(latest.close))
        closes = [Decimal(str(row.close)) for row in history[:252]]
        percentile = (
            Decimal(sum(value <= level for value in closes)) / Decimal(len(closes)) * Decimal("100")
            if len(closes) >= VIX_PERCENTILE_MIN_SESSIONS
            else None
        )
        rank = (
            Decimal(sum(value < level for value in closes)) / Decimal(len(closes)) * Decimal("100")
            if len(closes) >= VIX_PERCENTILE_MIN_SESSIONS
            else None
        )
        level_signal = (
            _clamp((Decimal("18") - level) / Decimal("13") * Decimal("100"))
            if level.is_finite() and level > 0
            else None
        )
        return_signals = {
            horizon: (
                _clamp(value * RETURN_MULTIPLIERS[INDIA_VIX_INDEX_CODE][horizon])
                if value is not None
                else None
            )
            for horizon, value in returns.items()
        }
        momentum_score, normalized_weights = _weighted_score(
            return_signals, RETURN_WEIGHTS[INDIA_VIX_INDEX_CODE]
        )
        score_values = {"level": level_signal, "momentum": momentum_score}
        score, score_weights = _weighted_score(
            score_values, {"level": Decimal("0.55"), "momentum": Decimal("0.45")}
        )
        completeness = self._completeness(
            [level_signal, returns[1], returns[5], returns[20], percentile]
        )
        freshness = self._row_freshness(latest, cutoffs[INDIA_VIX_INDEX_CODE])
        confidence = self._confidence(completeness, freshness)
        base.update(
            self._common_values(latest, score, completeness, confidence, freshness),
        )
        base.update(
            {
                "latest": _number(level, 4),
                "returns": {key: _number(value, 4) for key, value in returns.items()},
                "historical_percentile": _number(percentile),
                "historical_rank": _number(rank),
                "regime": classify_vix_regime(level),
                "score_breakdown": {
                    "level_signal": _number(level_signal),
                    "momentum_signal": _number(momentum_score),
                    "normalized_score_weights": score_weights,
                    "return_signals": {
                        key: _number(value) for key, value in return_signals.items()
                    },
                    "normalized_return_weights": normalized_weights,
                    "regime_thresholds_index_points": {
                        key: _number(value) for key, value in VIX_REGIME_THRESHOLDS.items()
                    },
                    "percentile_minimum_sessions": VIX_PERCENTILE_MIN_SESSIONS,
                },
            }
        )
        return base

    def _trend_factor_context(
        self,
        code: str,
        history: list[MarketIndexPrice],
        cutoff: date,
    ) -> dict[str, Any]:
        base = self._empty_context(code, cutoff)
        base.update(
            {
                "returns": {1: None, 5: None, 20: None, 60: None},
                "moving_averages": {"sma_20": None, "sma_50": None, "sma_200": None},
                "trend": None,
                "market_direction": None,
            }
        )
        if not history:
            return base
        latest = history[0]
        returns = price_series_returns(history, (1, 5, 20, 60))
        moving_averages = price_series_moving_averages(history, (20, 50, 200))
        trend = price_series_trend(history)
        return_signals = {
            horizon: (
                _clamp(value * RETURN_MULTIPLIERS[code][horizon])
                if value is not None
                else None
            )
            for horizon, value in returns.items()
        }
        momentum_score, normalized_weights = _weighted_score(
            return_signals, RETURN_WEIGHTS[code]
        )
        trend_signal = TREND_RISK_SIGNALS.get(trend) if trend is not None else None
        score, score_weights = _weighted_score(
            {"returns": momentum_score, "trend": trend_signal},
            {"returns": Decimal("0.70"), "trend": Decimal("0.30")},
        )
        completeness = self._completeness(
            [returns[1], returns[5], returns[20], returns[60], moving_averages["sma_20"]]
        )
        freshness = self._row_freshness(latest, cutoff)
        confidence = self._confidence(completeness, freshness)
        base.update(self._common_values(latest, score, completeness, confidence, freshness))
        base.update(
            {
                "latest": _number(Decimal(str(latest.close)), 4),
                "returns": {key: _number(value, 4) for key, value in returns.items()},
                "moving_averages": {
                    key: _number(value, 4) if isinstance(value, Decimal) else value
                    for key, value in moving_averages.items()
                },
                "trend": trend,
                "market_direction": self._market_direction(code, returns[1]),
                "score_breakdown": {
                    "return_signals": {
                        key: _number(value) for key, value in return_signals.items()
                    },
                    "trend_signal": _number(trend_signal),
                    "normalized_return_weights": normalized_weights,
                    "normalized_score_weights": score_weights,
                },
            }
        )
        return base

    @staticmethod
    def _market_direction(code: str, one_day_return: Decimal | None) -> str | None:
        if one_day_return is None:
            return None
        if one_day_return == 0:
            return "flat"
        if code == USD_INR_INDEX_CODE:
            return "inr_weakening" if one_day_return > 0 else "inr_strengthening"
        return "rising" if one_day_return > 0 else "falling"

    @staticmethod
    def _empty_context(code: str, cutoff: date) -> dict[str, Any]:
        identity = EXPECTED_IDENTITIES[code]
        return {
            "index_code": code,
            "market_date": None,
            "cutoff_date": cutoff.isoformat(),
            "latest": None,
            "score": None,
            "direction": None,
            "freshness": "missing",
            "confidence": 0.0,
            "completeness": 0.0,
            "source_name": None,
            "source_reference": None,
            "observed_at": None,
            "fetched_at": None,
            "identity": identity,
            "fallback_level": "unavailable",
            "provider_attempts": [],
            "score_breakdown": {},
        }

    @staticmethod
    def _common_values(
        latest: MarketIndexPrice,
        score: Decimal | None,
        completeness: Decimal,
        confidence: Decimal,
        freshness: str,
    ) -> dict[str, Any]:
        return {
            "market_date": latest.trade_date.isoformat(),
            "score": _number(score),
            "direction": classify_factor_direction(score),
            "freshness": freshness,
            "confidence": _number(confidence),
            "completeness": _number(completeness),
            "source_name": latest.source_name,
            "source_reference": latest.source_reference,
            "observed_at": latest.observed_at.isoformat() if latest.observed_at else None,
            "fetched_at": latest.fetched_at.isoformat() if latest.fetched_at else None,
        }

    async def _provider_history(
        self,
        code: str,
        start_date: date,
        end_date: date,
        evaluation_at: datetime,
    ) -> tuple[list[MarketIndexPrice], list[ProviderResult], str]:
        attempts: list[ProviderResult] = []
        supporting = [
            provider
            for provider in self._providers
            if code in getattr(provider, "supports_indices", [])
        ]
        for position, provider in enumerate(supporting):
            try:
                fetch_range = getattr(provider, "fetch_range", None)
                if callable(fetch_range):
                    result = await fetch_range(
                        code, start_date, end_date, evaluation_at=evaluation_at
                    )
                else:
                    result = await provider.fetch(
                        index_code=code,
                        trade_date=end_date,
                        evaluation_at=evaluation_at,
                    )
            except Exception as exc:
                result = ProviderResult(
                    provider_name=getattr(provider, "name", provider.__class__.__name__),
                    status="failed",
                    freshness="missing",
                    error_code="provider_exception",
                    message=f"Provider raised {exc.__class__.__name__}",
                )
            attempts.append(result)
            rows = self._validated_provider_rows(
                code, result, start_date, end_date, evaluation_at
            )
            if rows:
                level = "primary_provider" if position == 0 else "secondary_provider"
                return rows, attempts, level
        return [], attempts, "unavailable"

    @staticmethod
    def _validated_provider_rows(
        code: str,
        result: ProviderResult,
        start_date: date,
        end_date: date,
        evaluation_at: datetime,
    ) -> list[MarketIndexPrice]:
        if result.status not in {"success", "cached", "stale"}:
            return []
        payload = result.data if isinstance(result.data, list) else [result.data]
        if not payload or not all(isinstance(item, dict) for item in payload):
            return []
        identity = EXPECTED_IDENTITIES[code]
        by_date: dict[date, tuple[datetime, MarketIndexPrice]] = {}
        for item in payload:
            if item.get("index_code") != code or any(
                item.get(field) != expected for field, expected in identity.items()
            ):
                return []
            try:
                trade_date = item["trade_date"]
                observed_at = item["observed_at"]
                available_at = item["available_at"]
                values = {
                    field: Decimal(str(item[field]))
                    for field in ("open", "high", "low", "close")
                }
            except (InvalidOperation, KeyError, TypeError, ValueError):
                return []
            if (
                not isinstance(trade_date, date)
                or isinstance(trade_date, datetime)
                or not isinstance(observed_at, datetime)
                or observed_at.tzinfo is None
                or observed_at.utcoffset() is None
                or not isinstance(available_at, datetime)
                or available_at.tzinfo is None
                or available_at.utcoffset() is None
                or available_at < observed_at
                or observed_at > evaluation_at
                or available_at > evaluation_at
                or not start_date <= trade_date <= end_date
                or any(not value.is_finite() or value <= 0 for value in values.values())
                or values["high"] < max(values.values())
                or values["low"] > min(values.values())
                or not str(item.get("source_reference") or "").strip()
            ):
                return []
            row = MarketIndexPrice(
                market_index_id=0,
                trade_date=trade_date,
                **values,
                observed_at=observed_at,
                fetched_at=available_at,
                source_name=result.provider_name,
                source_reference=str(item["source_reference"]),
                freshness_status=result.freshness,
                market_index=MarketIndex(code=code, name=code, country="TASK_18_5"),
            )
            previous = by_date.get(trade_date)
            if previous is None or available_at > previous[0]:
                by_date[trade_date] = (available_at, row)
        return [item[1] for item in sorted(by_date.values(), key=lambda item: item[1].trade_date)]

    @staticmethod
    def _stored_fallback_level(history: list[MarketIndexPrice], cutoff: date) -> str:
        if not history:
            return "unavailable"
        return (
            "exact_stored_observation"
            if history[0].trade_date == cutoff
            else "stale_stored_observation"
        )

    @staticmethod
    def _merge_histories(
        provider_rows: list[MarketIndexPrice],
        stored_rows: list[MarketIndexPrice],
    ) -> list[MarketIndexPrice]:
        by_date = {row.trade_date: row for row in stored_rows}
        by_date.update({row.trade_date: row for row in provider_rows})
        return sorted(by_date.values(), key=lambda row: row.trade_date, reverse=True)

    @staticmethod
    def _row_freshness(row: MarketIndexPrice, cutoff: date) -> str:
        if row.trade_date < cutoff:
            return "stale"
        return (
            row.freshness_status
            if row.freshness_status in FRESHNESS_FACTORS
            else "stale"
        )

    @staticmethod
    def _public_attempts(attempts: list[ProviderResult]) -> list[dict[str, Any]]:
        return [
            {
                "provider_name": item.provider_name,
                "status": item.status,
                "freshness": item.freshness,
                "error_code": item.error_code,
            }
            for item in attempts
        ]

    @staticmethod
    def _completeness(values: list[Any]) -> Decimal:
        return Decimal(sum(value is not None for value in values)) / Decimal(len(values)) * 100

    @staticmethod
    def _confidence(completeness: Decimal, freshness: str) -> Decimal:
        return _clamp(
            completeness * Decimal("0.75")
            + FRESHNESS_FACTORS.get(freshness, Decimal("0")) * Decimal("25"),
            Decimal("0"),
            Decimal("100"),
        )


def _weighted_score(
    values: dict[Any, Decimal | None],
    weights: dict[Any, Decimal],
) -> tuple[Decimal | None, dict[Any, float]]:
    available = {key: value for key, value in values.items() if value is not None}
    if not available:
        return None, {}
    total = sum(weights[key] for key in available)
    normalized = {key: weights[key] / total for key in available}
    score = sum(available[key] * normalized[key] for key in available)
    return _clamp(score), {key: _number(value, 4) for key, value in normalized.items()}


def _worst_freshness(values: Any) -> str:
    priority = {"missing": 0, "stale": 1, "recent": 2, "live": 3}
    items = list(values)
    return min(items, key=lambda value: priority.get(value, 0)) if items else "missing"


def _clamp(
    value: Decimal,
    low: Decimal = SCORE_MIN,
    high: Decimal = SCORE_MAX,
) -> Decimal:
    return max(low, min(high, value))


def _number(value: Decimal | None, places: int = 2) -> float | None:
    if value is None:
        return None
    quantum = Decimal("1").scaleb(-places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))