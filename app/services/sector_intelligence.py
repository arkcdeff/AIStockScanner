from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.sector_universe import (
    SECTOR_BENCHMARK_CODE,
    SECTOR_DEFINITIONS_BY_CODE,
    SECTOR_INDEX_CODES,
    cyclical_codes,
    defensive_codes,
)
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.services.global_market_intelligence import (
    FRESHNESS_FACTORS,
    TREND_SIGNALS,
    classify_direction,
)
from app.services.index_price_action import (
    IndexPriceAction,
    price_series_range_position,
    price_series_returns,
)

SCORE_MIN = Decimal("-100")
SCORE_MAX = Decimal("100")

#: Relative-strength scoring weights (starting architecture, centralized).
RELATIVE_STRENGTH_WEIGHTS: dict[int, Decimal] = {
    5: Decimal("0.20"),
    20: Decimal("0.45"),
    60: Decimal("0.35"),
}
#: 10 percentage points of relative outperformance maps to +100 (linear, clamped).
RELATIVE_RETURN_SCALE = Decimal("10")

#: Sector-momentum scoring weights (starting architecture, centralized).
MOMENTUM_WEIGHTS: dict[str, Decimal] = {
    "return_5": Decimal("0.25"),
    "return_20": Decimal("0.30"),
    "return_60": Decimal("0.15"),
    "sma20_position": Decimal("0.20"),
    "sma50_position": Decimal("0.10"),
}
MOMENTUM_RETURN_MULTIPLIERS: dict[int, Decimal] = {
    5: Decimal("6"),
    20: Decimal("3"),
    60: Decimal("1.5"),
}
SMA_DISTANCE_SCALE = Decimal("10")

#: Per-sector score weights (price-action 30%, relative strength 35%,
#: momentum 25%, trend 10%) — centralized and renormalized when missing.
SECTOR_SCORE_WEIGHTS: dict[str, Decimal] = {
    "price_action": Decimal("0.30"),
    "relative_strength": Decimal("0.35"),
    "momentum": Decimal("0.25"),
    "trend": Decimal("0.10"),
}

#: Participation-blend weights (each metric contributes 0-100).
PARTICIPATION_WEIGHTS: dict[str, Decimal] = {
    "positive_1d": Decimal("0.10"),
    "positive_5d": Decimal("0.20"),
    "positive_20d": Decimal("0.15"),
    "above_sma20": Decimal("0.15"),
    "above_sma50": Decimal("0.10"),
    "above_sma200": Decimal("0.10"),
    "outperform_20d": Decimal("0.10"),
    "momentum_positive": Decimal("0.10"),
}

#: Overall sector-intelligence-score weights (Task 18.7 factor score only;
#: not the final composite, which belongs to Task 18.9).
SECTOR_INTELLIGENCE_WEIGHTS: dict[str, Decimal] = {
    "aggregate_sector_scores": Decimal("0.40"),
    "participation": Decimal("0.25"),
    "leader_laggard_breadth": Decimal("0.15"),
    "cyclical_context": Decimal("0.10"),
    "divergence": Decimal("0.10"),
}

DIVERGENCE_SIGNAL_VALUES: dict[str, Decimal] = {
    "bullish_confirmation": Decimal("60"),
    "positive_divergence": Decimal("40"),
    "mixed": Decimal("0"),
    "negative_divergence": Decimal("-40"),
    "bearish_confirmation": Decimal("-60"),
}


def _clamp(
    value: Decimal | None,
    low: Decimal = SCORE_MIN,
    high: Decimal = SCORE_MAX,
) -> Decimal | None:
    if value is None:
        return None
    return max(low, min(high, value))


def _rounded(value: Decimal, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))


def _linear_signal(value: Decimal | None, center: Decimal = Decimal("50")) -> Decimal | None:
    if value is None:
        return None
    return _clamp((value - center) * 2)


def _weighted_score(
    values: dict[str, Any],
    configured_weights: dict[str, Decimal],
    *,
    low: Decimal = SCORE_MIN,
    high: Decimal = SCORE_MAX,
) -> tuple[Decimal | None, dict[str, float]]:
    available = {
        key: Decimal(str(value))
        for key, value in values.items()
        if value is not None and key in configured_weights
    }
    if not available:
        return None, {}
    total = sum(configured_weights[key] for key in available)
    normalized = {key: configured_weights[key] / total for key in available}
    score = sum(available[key] * normalized[key] for key in available)
    return _clamp(score, low, high), {
        key: _rounded(weight, 4) for key, weight in normalized.items()
    }


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values) / Decimal(len(values)) if values else None


class _InMemoryPriceRepository:
    """Repository-compatible view over the service's single bulk read.

    Histories are pre-sorted descending by ``trade_date`` to mirror the
    repository contract expected by ``IndexPriceAction``.
    """

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


class SectorIntelligence:
    """Deterministic NSE sector intelligence for a requested session date.

    Sector indices are read from the existing ``MarketIndexPrice`` domain in a
    single bounded bulk query (sectors + NIFTY_50 benchmark together). Price
    features reuse ``IndexPriceAction``; no formula is copied here. Everything
    is bounded by ``market_date`` so no future row can affect the output.
    """

    _LOOKBACK_DAYS = 400

    def __init__(
        self,
        session: AsyncSession,
        *,
        price_repo: MarketIndexPriceRepository | None = None,
        sector_codes: tuple[str, ...] = SECTOR_INDEX_CODES,
        benchmark_code: str = SECTOR_BENCHMARK_CODE,
        leaders_count: int = 3,
    ) -> None:
        self._price_repo = price_repo or MarketIndexPriceRepository(session)
        self._sector_codes = sector_codes
        self._benchmark_code = benchmark_code
        self._leaders_count = leaders_count

    async def evaluate(self, market_date: date) -> dict[str, Any]:
        start_date = market_date - timedelta(days=self._LOOKBACK_DAYS)
        codes = tuple(self._sector_codes) + (self._benchmark_code,)
        rows = await self._price_repo.get_ranges_by_index_codes(
            codes, start_date, market_date
        )
        histories = self._group_histories(rows)
        memory_repo = _InMemoryPriceRepository(histories)

        benchmark = await self._index_features(
            self._benchmark_code,
            histories.get(self._benchmark_code, []),
            memory_repo,
            market_date,
        )
        sectors: dict[str, dict[str, Any]] = {}
        for code in self._sector_codes:
            sectors[code] = await self._sector_report(
                code,
                histories.get(code, []),
                benchmark,
                memory_repo,
                market_date,
            )

        available_count = sum(
            1
            for report in sectors.values()
            if report["price"]["latest_close"] is not None
        )
        participation = self._participation(sectors)
        concentration = self._leadership_concentration(sectors)
        divergence = self._divergence(sectors, histories, benchmark, market_date)
        cyclical = self._cyclical_defensive(sectors)

        scores = [
            Decimal(str(report["score"]))
            for report in sectors.values()
            if report["score"] is not None
        ]
        aggregate_score = _mean(scores)
        positive_ratio = concentration["measurements"]["positive_ratio"]

        intelligence_components: dict[str, Any] = {
            "aggregate_sector_scores": aggregate_score,
            "participation": _linear_signal(participation["percent"]),
            "leader_laggard_breadth": (
                _clamp((Decimal(str(positive_ratio)) - Decimal("0.5")) * 200)
                if positive_ratio is not None
                else None
            ),
            "cyclical_context": cyclical["cyclical_minus_defensive"],
            "divergence": DIVERGENCE_SIGNAL_VALUES.get(divergence["signal"]),
        }
        sector_intelligence_score, intelligence_weights = _weighted_score(
            intelligence_components, SECTOR_INTELLIGENCE_WEIGHTS
        )

        freshness_label, freshness_factor = self._aggregate_freshness(
            sectors, benchmark
        )
        completeness = self._completeness(
            sectors, benchmark, participation
        )
        confidence = self._confidence(
            completeness,
            freshness_factor,
            sector_intelligence_score,
            [
                Decimal(str(report["score"]))
                for report in sectors.values()
                if report["score"] is not None
            ],
        )

        leaders, laggards = self._ranking(sectors)

        return {
            "market_date": market_date.isoformat(),
            "benchmark": self._benchmark_code,
            "sector_count": len(self._sector_codes),
            "available_sector_count": available_count,
            "sectors": {
                code: self._public_sector_report(report)
                for code, report in sectors.items()
            },
            "leaders": leaders,
            "laggards": laggards,
            "sector_participation_percent": (
                _rounded(participation["percent"])
                if participation["percent"] is not None
                else None
            ),
            "participation_breakdown": {
                key: (_rounded(value) if value is not None else None)
                for key, value in participation["metrics"].items()
            },
            "rotation": self._rotation_census(sectors),
            "leadership_concentration": concentration,
            "cyclical_score": (
                _rounded(cyclical["cyclical_score"])
                if cyclical["cyclical_score"] is not None
                else None
            ),
            "defensive_score": (
                _rounded(cyclical["defensive_score"])
                if cyclical["defensive_score"] is not None
                else None
            ),
            "cyclical_minus_defensive": (
                _rounded(cyclical["cyclical_minus_defensive"])
                if cyclical["cyclical_minus_defensive"] is not None
                else None
            ),
            "risk_context": cyclical["risk_context"],
            "sector_divergence": divergence,
            "sector_intelligence_score": (
                _rounded(sector_intelligence_score)
                if sector_intelligence_score is not None
                else None
            ),
            "direction": classify_direction(sector_intelligence_score),
            "completeness_percent": _rounded(completeness),
            "confidence_score": _rounded(confidence),
            "data_as_of": self._data_as_of(sectors, benchmark),
            "freshness": freshness_label,
            "component_breakdown": {
                "component_signals": {
                    key: (_rounded(value) if value is not None else None)
                    for key, value in intelligence_components.items()
                },
                "normalized_weights": intelligence_weights,
                "aggregate_sector_scores": (
                    _rounded(aggregate_score) if aggregate_score is not None else None
                ),
                "available_sector_count": available_count,
            },
            "provenance": {
                "engine": "SectorIntelligence",
                "benchmark": self._benchmark_code,
                "lookback_days": self._LOOKBACK_DAYS,
                "repository": self._price_repo.__class__.__name__,
                "bulk_query_count": 1,
                "sector_codes": list(self._sector_codes),
                "methodology": [
                    (
                        "Sector indices use the existing MarketIndex/MarketIndexPrice "
                        "domain; NIFTY_BANK and NIFTY_FIN_SERV reuse existing catalog "
                        "identities without duplication."
                    ),
                    (
                        "Relative return = sector return minus NIFTY_50 return for the "
                        "same horizon; missing benchmark keeps the value None (never 0)."
                    ),
                    (
                        "Relative-strength scale: 10 percentage points of "
                        "outperformance maps to +100, clamped to -100..+100."
                    ),
                    (
                        "Momentum combines 5/20/60-session returns and SMA20/SMA50 "
                        "distance signals; weights are centralized and renormalized."
                    ),
                    (
                        "Per-sector score weights: price-action 30%, relative strength "
                        "35%, momentum 25%, trend 10%."
                    ),
                    (
                        "Participation percent is a renormalized blend of independent "
                        "participation metrics; missing sectors lower completeness and "
                        "never count as negative."
                    ),
                    (
                        "Rotation compares 5 versus 20-session relative return plus "
                        "momentum; exact state rules are documented in code."
                    ),
                    (
                        "No-look-ahead: every read is bounded by the evaluation date; "
                        "a mandatory future-invariance test guards deterministic replay."
                    ),
                ],
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _group_histories(rows: list[MarketIndexPrice]) -> dict[str, list[MarketIndexPrice]]:
        by_key: dict[tuple[str, date], MarketIndexPrice] = {}
        for row in rows:
            code = row.market_index.code
            key = (code, row.trade_date)
            if key not in by_key or (row.id or 0) > (by_key[key].id or 0):
                by_key[key] = row
        grouped: dict[str, list[MarketIndexPrice]] = defaultdict(list)
        for (code, _trade_date), row in by_key.items():
            grouped[code].append(row)
        for history in grouped.values():
            history.sort(key=lambda row: row.trade_date, reverse=True)
        return dict(grouped)

    async def _index_features(
        self,
        code: str,
        history: list[MarketIndexPrice],
        memory_repo: _InMemoryPriceRepository,
        market_date: date,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "latest_close": None,
            "data_as_of": None,
            "freshness": "missing",
            "source_name": None,
            "source_reference": None,
            "returns": {1: None, 5: None, 20: None, 60: None},
            "moving_averages": {
                "sma_20": None,
                "sma_50": None,
                "sma_100": None,
                "sma_200": None,
                "sma_20_above": None,
                "sma_50_above": None,
                "sma_100_above": None,
                "sma_200_above": None,
                "sma_20_distance_percent": None,
                "sma_50_distance_percent": None,
                "sma_100_distance_percent": None,
                "sma_200_distance_percent": None,
            },
            "trend": None,
            "momentum": {5: None, 20: None, 60: None},
            "position_20d": {
                "high": None,
                "low": None,
                "distance_from_high_percent": None,
                "distance_from_low_percent": None,
                "position_in_range_percent": None,
            },
            "position_52w": {
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
                "distance_from_52w_high_percent": None,
                "distance_from_52w_low_percent": None,
                "position_in_52w_range_percent": None,
            },
            "price_action_score": None,
            "price_action_breakdown": {},
        }
        if not history:
            return base
        action = IndexPriceAction(code, market_date)
        action.set_repo(memory_repo)  # type: ignore[arg-type]
        latest = history[0]
        base.update(
            {
                "latest_close": float(Decimal(str(latest.close))),
                "data_as_of": latest.trade_date,
                "freshness": (
                    "recent" if latest.trade_date == market_date else "stale"
                ),
                "source_name": latest.source_name,
                "source_reference": latest.source_reference,
                "returns": await action.returns((1, 5, 20, 60)),
                "moving_averages": await action.moving_averages((20, 50, 100, 200)),
                "trend": (await action.trend()).get("trend"),
                "momentum": await action.momentum((5, 20, 60)),
                "position_20d": price_series_range_position(history, 20),
                "position_52w": await action.fifty_two_week_position(),
            }
        )
        price_action = await action.price_action_score()
        base["price_action_score"] = price_action.get("price_action_score")
        base["price_action_breakdown"] = price_action.get("score_breakdown", {})
        return base

    async def _sector_report(
        self,
        code: str,
        history: list[MarketIndexPrice],
        benchmark: dict[str, Any],
        memory_repo: _InMemoryPriceRepository,
        market_date: date,
    ) -> dict[str, Any]:
        definition = SECTOR_DEFINITIONS_BY_CODE[code]
        features = await self._index_features(code, history, memory_repo, market_date)

        returns = features["returns"]
        relative_returns = self._relative_returns(returns, benchmark["returns"])
        relative_strength, rel_components, rel_weights = self._relative_strength(
            relative_returns
        )
        momentum_score, mom_components, mom_weights = self._momentum_score(features)
        price_action = features["price_action_score"]

        score_components: dict[str, Any] = {
            "price_action": (
                Decimal(str(price_action)) if price_action is not None else None
            ),
            "relative_strength": relative_strength,
            "momentum": momentum_score,
            "trend": TREND_SIGNALS.get(features["trend"]),
        }
        score, score_weights = _weighted_score(score_components, SECTOR_SCORE_WEIGHTS)

        return {
            "code": code,
            "name": definition.name,
            "group": definition.group,
            "cyclicality": definition.cyclicality,
            "price": {
                "latest_close": features["latest_close"],
                "data_as_of": features["data_as_of"],
                "freshness": features["freshness"],
                "source_name": features["source_name"],
                "source_reference": features["source_reference"],
            },
            "returns": returns,
            "moving_averages": features["moving_averages"],
            "trend": features["trend"],
            "momentum": features["momentum"],
            "price_action_score": price_action,
            "position_20d": features["position_20d"],
            "position_52w": features["position_52w"],
            "relative_returns": relative_returns,
            "relative_strength": (
                _rounded(relative_strength)
                if relative_strength is not None
                else None
            ),
            "relative_strength_breakdown": {
                "component_signals": {
                    key: (_rounded(value) if value is not None else None)
                    for key, value in rel_components.items()
                },
                "normalized_weights": rel_weights,
            },
            "momentum_score": (
                _rounded(momentum_score) if momentum_score is not None else None
            ),
            "momentum_breakdown": {
                key: (_rounded(value) if value is not None else None)
                for key, value in mom_components.items()
            },
            "rotation": self._rotation_state(
                relative_returns, momentum_score
            ),
            "score": _rounded(score) if score is not None else None,
            "direction": classify_direction(score),
            "score_breakdown": {
                "component_signals": {
                    key: (_rounded(value) if value is not None else None)
                    for key, value in score_components.items()
                },
                "normalized_weights": score_weights,
            },
        }

    @staticmethod
    def _relative_returns(
        sector_returns: dict[int, Any], benchmark_returns: dict[int, Any]
    ) -> dict[int, float | None]:
        relative: dict[int, float | None] = {}
        for horizon in (1, 5, 20, 60):
            sector = sector_returns.get(horizon)
            benchmark = benchmark_returns.get(horizon)
            if sector is None or benchmark is None:
                relative[horizon] = None
            else:
                relative[horizon] = _rounded(
                    Decimal(str(sector)) - Decimal(str(benchmark))
                )
        return relative

    @staticmethod
    def _relative_strength(
        relative_returns: dict[int, float | None],
    ) -> tuple[Decimal | None, dict[int, Decimal | None], dict[str, float]]:
        components: dict[int, Decimal | None] = {}
        for horizon, _weight in RELATIVE_STRENGTH_WEIGHTS.items():
            value = relative_returns.get(horizon)
            components[horizon] = (
                _clamp(Decimal(str(value)) * RELATIVE_RETURN_SCALE)
                if value is not None
                else None
            )
        score, weights = _weighted_score(components, RELATIVE_STRENGTH_WEIGHTS)
        return score, components, weights

    @staticmethod
    def _momentum_score(
        features: dict[str, Any],
    ) -> tuple[Decimal | None, dict[str, Decimal | None], dict[str, float]]:
        components: dict[str, Decimal | None] = {}
        for key, horizon in (("return_5", 5), ("return_20", 20), ("return_60", 60)):
            value = features["returns"].get(horizon)
            components[key] = (
                _clamp(Decimal(str(value)) * MOMENTUM_RETURN_MULTIPLIERS[horizon])
                if value is not None
                else None
            )
        for key, window in (("sma20_position", 20), ("sma50_position", 50)):
            distance = features["moving_averages"].get(
                f"sma_{window}_distance_percent"
            )
            components[key] = (
                _clamp(Decimal(str(distance)) * SMA_DISTANCE_SCALE)
                if distance is not None
                else None
            )
        score, weights = _weighted_score(components, MOMENTUM_WEIGHTS)
        return score, components, weights

    def _rotation_state(
        self,
        relative_returns: dict[int, float | None],
        momentum_score: float | None,
    ) -> dict[str, Any]:
        """Deterministic rotation classification for one sector.

        Uses 5-session (short) and 20-session (medium) relative returns, plus the
        momentum score for acceleration/deceleration. States:
        leader / weakening_leader / emerging_leader / improving / deteriorating /
        laggard / neutral. ``None`` when the 5/20 relative horizons are missing.
        """
        output = {
            "short": relative_returns.get(5),
            "medium": relative_returns.get(20),
            "long": relative_returns.get(60),
            "momentum_score": momentum_score,
            "state": None,
        }
        short = output["short"]
        medium = output["medium"]
        if short is None or medium is None:
            return output
        leading = medium > 0
        improving = short > medium
        accelerating = momentum_score is not None and momentum_score >= 5
        decelerating = momentum_score is not None and momentum_score <= -5
        if leading and improving and not decelerating:
            state = "leader"
        elif leading and (improving or not improving) and decelerating:
            state = "weakening_leader"
        elif leading and not improving:
            state = "weakening_leader"
        elif not leading and improving and (short > 0 or accelerating):
            state = "emerging_leader"
        elif not leading and improving:
            state = "improving"
        elif not leading and not improving and short < 0:
            state = "laggard"
        elif not leading and not improving:
            state = "deteriorating"
        else:
            state = "neutral"
        output["state"] = state
        return output

    def _participation(
        self, sectors: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        def metric_ratio(fn) -> Decimal | None:
            values = [fn(report) for report in sectors.values()]
            available = [value for value in values if value is not None]
            if not available:
                return None
            return (
                Decimal(sum(1 for value in available if value))
                / Decimal(len(available))
                * 100
            )

        metrics: dict[str, Decimal | None] = {
            "positive_1d": metric_ratio(
                lambda r: r["returns"][1] is not None and r["returns"][1] > 0
                if r["returns"][1] is not None
                else None
            ),
            "positive_5d": metric_ratio(
                lambda r: r["returns"][5] is not None and r["returns"][5] > 0
                if r["returns"][5] is not None
                else None
            ),
            "positive_20d": metric_ratio(
                lambda r: r["returns"][20] is not None and r["returns"][20] > 0
                if r["returns"][20] is not None
                else None
            ),
            "above_sma20": metric_ratio(
                lambda r: r["moving_averages"]["sma_20_above"]
            ),
            "above_sma50": metric_ratio(
                lambda r: r["moving_averages"]["sma_50_above"]
            ),
            "above_sma200": metric_ratio(
                lambda r: r["moving_averages"]["sma_200_above"]
            ),
            "outperform_20d": metric_ratio(
                lambda r: (
                    r["relative_returns"][20] > 0
                    if r["relative_returns"][20] is not None
                    else None
                )
            ),
            "momentum_positive": metric_ratio(
                lambda r: (
                    r["momentum_score"] > 0
                    if r["momentum_score"] is not None
                    else None
                )
            ),
        }
        percent, weights = _weighted_score(
            metrics, PARTICIPATION_WEIGHTS, low=Decimal("0"), high=Decimal("100")
        )
        return {
            "percent": percent,
            "metrics": metrics,
            "normalized_weights": weights,
        }

    @staticmethod
    def _mean_of_sector_scores(
        sectors: dict[str, dict[str, Any]], codes: list[str]
    ) -> Decimal | None:
        values = [
            Decimal(str(sectors[code]["score"]))
            for code in codes
            if sectors[code]["score"] is not None
        ]
        return _mean(values)

    def _cyclical_defensive(
        self, sectors: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        cyclical = self._mean_of_sector_scores(sectors, list(cyclical_codes()))
        defensive = self._mean_of_sector_scores(sectors, list(defensive_codes()))
        if cyclical is not None and defensive is not None:
            delta = cyclical - defensive
            if delta >= Decimal("10"):
                risk_context = "risk_on"
            elif delta <= Decimal("-10"):
                risk_context = "risk_off"
            else:
                risk_context = "balanced"
        else:
            delta = None
            risk_context = None
        return {
            "cyclical_score": cyclical,
            "defensive_score": defensive,
            "cyclical_minus_defensive": delta,
            "risk_context": risk_context,
        }

    def _divergence(
        self,
        sectors: dict[str, dict[str, Any]],
        histories: dict[str, list[MarketIndexPrice]],
        benchmark: dict[str, Any],
        market_date: date,
    ) -> dict[str, Any]:
        result = {
            "benchmark": self._benchmark_code,
            "benchmark_return_pct_1": benchmark["returns"].get(1),
            "participation_improvement_pct": None,
            "signal": "insufficient_data",
        }
        benchmark_return = benchmark["returns"].get(1)
        if benchmark_return is None:
            return result

        now = self._participation_index(sectors, histories, market_date)
        reference = self._session_prior(histories, market_date, 5)
        if reference is None:
            return result
        then = self._participation_index(sectors, histories, reference)
        if now is None or then is None:
            return result
        improvement = now - then
        result["participation_improvement_pct"] = _rounded(improvement)

        if benchmark_return > 0:
            if improvement >= 10:
                result["signal"] = "bullish_confirmation"
            elif improvement <= -10:
                result["signal"] = "negative_divergence"
            else:
                result["signal"] = "mixed"
        elif benchmark_return < 0:
            if improvement >= 10:
                result["signal"] = "positive_divergence"
            elif improvement <= -10:
                result["signal"] = "bearish_confirmation"
            else:
                result["signal"] = "mixed"
        else:
            result["signal"] = "mixed"
        return result

    @staticmethod
    def _session_prior(
        histories: dict[str, list[MarketIndexPrice]],
        market_date: date,
        offset: int,
    ) -> date | None:
        dates: set[date] = set()
        for history in histories.values():
            for row in history:
                if row.trade_date <= market_date:
                    dates.add(row.trade_date)
        ordered = sorted(dates)
        if len(ordered) <= offset:
            return None
        return ordered[-1 - offset]

    def _participation_index(
        self,
        sectors: dict[str, dict[str, Any]],
        histories: dict[str, list[MarketIndexPrice]],
        ref_date: date,
    ) -> Decimal | None:
        one_day = self._fraction_positive(histories, ref_date, 1)
        five_day = self._fraction_positive(histories, ref_date, 5)
        values = [value for value in (one_day, five_day) if value is not None]
        if not values:
            return None
        return sum(values) / Decimal(len(values))

    def _fraction_positive(
        self,
        histories: dict[str, list[MarketIndexPrice]],
        ref_date: date,
        horizon: int,
    ) -> Decimal | None:
        total = 0
        positive = 0
        for code in self._sector_codes:
            history = histories.get(code, [])
            bounded = [row for row in history if row.trade_date <= ref_date]
            value = price_series_returns(bounded, (horizon,)).get(horizon)
            if value is None:
                continue
            total += 1
            if value > 0:
                positive += 1
        if total == 0:
            return None
        return Decimal(positive) / Decimal(total) * 100

    @staticmethod
    def _leadership_concentration(
        sectors: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        scores = [
            Decimal(str(report["score"]))
            for report in sectors.values()
            if report["score"] is not None
        ]
        measurements: dict[str, Any] = {
            "available_count": len(scores),
            "positive_count": None,
            "positive_ratio": None,
            "top5_share": None,
            "dispersion_mad": None,
        }
        if len(scores) < 2:
            return {"state": None, "measurements": measurements}
        positive = [score for score in scores if score > 0]
        positive_ratio = Decimal(len(positive)) / Decimal(len(scores))
        top5_share = (
            sum(positive[:5]) / sum(positive) if positive else Decimal("0")
        )
        mean = sum(scores) / Decimal(len(scores))
        dispersion = sum(abs(score - mean) for score in scores) / Decimal(
            len(scores)
        )
        measurements.update(
            {
                "positive_count": len(positive),
                "positive_ratio": _rounded(positive_ratio),
                "top5_share": _rounded(top5_share),
                "dispersion_mad": _rounded(dispersion),
            }
        )
        # Transparent, deterministic classification documented in code.
        if positive_ratio >= Decimal("0.75"):
            state = (
                "broad"
                if top5_share <= Decimal("0.60")
                else "moderately_broad"
            )
        elif positive_ratio >= Decimal("0.55"):
            state = (
                "moderately_broad"
                if top5_share <= Decimal("0.75")
                else "concentrated"
            )
        elif positive_ratio >= Decimal("0.40"):
            state = (
                "balanced" if top5_share <= Decimal("0.85") else "concentrated"
            )
        else:
            state = (
                "highly_concentrated"
                if top5_share >= Decimal("0.85")
                else "concentrated"
            )
        return {"state": state, "measurements": measurements}

    def _ranking(
        self, sectors: dict[str, dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        def key(report: dict[str, Any]) -> tuple[float, float, str]:
            score = float(report["score"]) if report["score"] is not None else float("-inf")
            relative = (
                float(report["relative_strength"])
                if report["relative_strength"] is not None
                else float("-inf")
            )
            return (-score, -relative, report["code"])

        ordered = sorted(sectors.values(), key=key)
        ranked = [report for report in ordered if report["score"] is not None]
        count = self._leaders_count
        return self._rank_list(ranked[-count:]), self._rank_list(ranked[:count])

    def _rank_list(self, reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        for report in reversed(reports):
            items.append(
                {
                    "code": report["code"],
                    "name": report["name"],
                    "group": report["group"],
                    "latest_close": report["price"]["latest_close"],
                    "returns": {
                        horizon: report["returns"].get(horizon)
                        for horizon in (1, 5, 20, 60)
                    },
                    "relative_strength": report["relative_strength"],
                    "momentum": report["momentum_score"],
                    "score": report["score"],
                    "direction": report["direction"],
                    "freshness": report["price"]["freshness"],
                }
            )
        return items

    def _rotation_census(
        self, sectors: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        states: dict[str, int] = defaultdict(int)
        classified = 0
        for report in sectors.values():
            state = report["rotation"]["state"]
            if state is None:
                continue
            classified += 1
            states[state] += 1
        return {
            "classified_count": classified,
            "states": dict(states),
            "leaders": states["leader"],
            "emerging_leaders": states["emerging_leader"],
            "weakening_leaders": states["weakening_leader"],
            "improving": states["improving"],
            "deteriorating": states["deteriorating"],
            "laggards": states["laggard"],
        }

    @staticmethod
    def _data_as_of(
        sectors: dict[str, dict[str, Any]], benchmark: dict[str, Any]
    ) -> date | None:
        candidates = [
            report["price"]["data_as_of"]
            for report in sectors.values()
            if report["price"]["data_as_of"] is not None
        ]
        if benchmark["data_as_of"] is not None:
            candidates.append(benchmark["data_as_of"])
        return max(candidates) if candidates else None

    @staticmethod
    def _aggregate_freshness(
        sectors: dict[str, dict[str, Any]], benchmark: dict[str, Any]
    ) -> tuple[str, Decimal]:
        labels = [
            report["price"]["freshness"] for report in sectors.values()
        ] + [benchmark["freshness"]]
        if not labels:
            return "missing", Decimal("0")
        factors = [
            FRESHNESS_FACTORS.get(label, Decimal("0")) for label in labels
        ]
        average = sum(factors) / Decimal(len(factors))
        if average >= Decimal("0.85"):
            return "recent", average
        if average >= Decimal("0.40"):
            return "stale", average
        return "missing", average

    def _completeness(
        self,
        sectors: dict[str, dict[str, Any]],
        benchmark: dict[str, Any],
        participation: dict[str, Any],
    ) -> Decimal:
        def fraction_ratio(predicate) -> Decimal:
            total = len(self._sector_codes)
            matched = sum(
                1 for report in sectors.values() if predicate(report)
            )
            return Decimal(matched) / Decimal(total) if total else Decimal("0")

        factors = [
            fraction_ratio(lambda r: r["score"] is not None),
            fraction_ratio(lambda r: r["relative_strength"] is not None),
            fraction_ratio(
                lambda r: r["moving_averages"]["sma_200"] is not None
            ),
            Decimal("1") if benchmark["returns"].get(1) is not None else Decimal("0"),
            Decimal("1")
            if benchmark["returns"].get(60) is not None
            else Decimal("0"),
            Decimal("1") if participation["percent"] is not None else Decimal("0"),
        ]
        return sum(factors) / Decimal(len(factors)) * 100

    def _confidence(
        self,
        completeness: Decimal,
        freshness_factor: Decimal,
        sector_intelligence_score: Decimal | None,
        scores: list[Decimal],
    ) -> Decimal:
        if sector_intelligence_score is None or not scores:
            return Decimal("0")
        dispersion = sum(abs(score - sector_intelligence_score) for score in scores) / Decimal(
            len(scores)
        )
        agreement = _clamp(
            Decimal("100") - dispersion, Decimal("0"), Decimal("100")
        )
        confidence = (
            completeness * Decimal("0.55")
            + freshness_factor * Decimal("100") * Decimal("0.25")
            + agreement * Decimal("0.20")
        )
        return _clamp(confidence, Decimal("0"), Decimal("100"))

    @staticmethod
    def _public_sector_report(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": report["code"],
            "name": report["name"],
            "group": report["group"],
            "cyclicality": report["cyclicality"],
            "price": report["price"],
            "returns": report["returns"],
            "moving_averages": report["moving_averages"],
            "trend": report["trend"],
            "momentum": report["momentum"],
            "price_action_score": report["price_action_score"],
            "position_20d": report["position_20d"],
            "position_52w": report["position_52w"],
            "relative_returns": report["relative_returns"],
            "relative_strength": report["relative_strength"],
            "relative_strength_breakdown": report["relative_strength_breakdown"],
            "momentum_score": report["momentum_score"],
            "momentum_breakdown": report["momentum_breakdown"],
            "rotation": report["rotation"],
            "score": report["score"],
            "direction": report["direction"],
            "score_breakdown": report["score_breakdown"],
        }