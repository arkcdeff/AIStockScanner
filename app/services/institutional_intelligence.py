from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.providers.base import MarketProvider, ProviderResult
from app.market_intelligence.providers.institutional_flow_provider import (
    EXPECTED_DATASET,
    EXPECTED_LABELS,
    EXPECTED_UNIT,
)
from app.market_intelligence.session_dates import (
    INDIA_TIMEZONE,
    institutional_flow_cutoff_date,
)
from app.models.institutional_flow import InstitutionalFlow
from app.repositories.institutional_flow import InstitutionalFlowRepository

INVESTOR_TYPES = ("FII", "DII")
ROLLING_HORIZONS = (1, 3, 5, 20)

INSTITUTIONAL_DIRECTION_THRESHOLDS: dict[str, Decimal] = {
    "strong_buying": Decimal("60"),
    "buying": Decimal("25"),
    "selling": Decimal("-25"),
    "strong_selling": Decimal("-60"),
}
INVESTOR_WEIGHTS = {"FII": Decimal("0.65"), "DII": Decimal("0.35")}
HORIZON_WEIGHTS = {
    1: Decimal("0.25"),
    3: Decimal("0.20"),
    5: Decimal("0.25"),
    20: Decimal("0.30"),
}
HORIZON_FULL_SCALE_CRORE = {
    1: Decimal("1500"),
    3: Decimal("3000"),
    5: Decimal("5000"),
    20: Decimal("12000"),
}
FRESHNESS_FACTORS = {
    "live": Decimal("1"),
    "recent": Decimal("0.85"),
    "stale": Decimal("0.40"),
    "missing": Decimal("0"),
}
INSTITUTIONAL_PROVIDER_MARKET = "INDIA_INSTITUTIONAL_FLOWS"


@dataclass(frozen=True)
class _FlowObservation:
    trade_date: date
    investor_type: str
    buy_value: Decimal
    sell_value: Decimal
    source_name: str | None
    source_reference: str | None
    observed_at: date | datetime | None
    fetched_at: date | datetime | None
    freshness_status: str


def classify_institutional_direction(score: Decimal | float | int | None) -> str | None:
    if score is None:
        return None
    value = Decimal(str(score))
    if value >= INSTITUTIONAL_DIRECTION_THRESHOLDS["strong_buying"]:
        return "strong_buying"
    if value >= INSTITUTIONAL_DIRECTION_THRESHOLDS["buying"]:
        return "buying"
    if value <= INSTITUTIONAL_DIRECTION_THRESHOLDS["strong_selling"]:
        return "strong_selling"
    if value <= INSTITUTIONAL_DIRECTION_THRESHOLDS["selling"]:
        return "selling"
    return "neutral"


class InstitutionalIntelligence:
    """Deterministic FII/FPI and DII activity from bounded persisted flows."""

    _LOOKBACK_DAYS = 60

    def __init__(
        self,
        session: AsyncSession,
        providers: tuple[MarketProvider, ...] = (),
        *,
        flow_repo: InstitutionalFlowRepository | None = None,
    ) -> None:
        self._providers = providers
        self._flow_repo = flow_repo or InstitutionalFlowRepository(session)

    async def evaluate(self, evaluation_at: datetime) -> dict[str, Any]:
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("evaluation_at must be timezone-aware")
        cutoff = institutional_flow_cutoff_date(evaluation_at)
        rows = await self._flow_repo.get_range_for_types(
            cutoff - timedelta(days=self._LOOKBACK_DAYS), cutoff, INVESTOR_TYPES
        )
        local_date = evaluation_at.astimezone(ZoneInfo(INDIA_TIMEZONE)).date()
        stored_histories: dict[str, list[_FlowObservation]] = defaultdict(list)
        for row in rows:
            if row.trade_date > cutoff:
                continue
            if row.fetched_at is not None and row.fetched_at > local_date:
                continue
            if row.investor_type in INVESTOR_TYPES:
                observation = self._stored_observation(row)
                if observation is not None:
                    stored_histories[row.investor_type].append(observation)
        for history in stored_histories.values():
            history.sort(key=lambda row: row.trade_date, reverse=True)

        start_date = cutoff - timedelta(days=self._LOOKBACK_DAYS)
        provider_histories, provider_attempts, provider_levels = (
            await self._provider_histories(start_date, cutoff, evaluation_at)
        )
        histories: dict[str, list[_FlowObservation]] = {}
        fallback_levels: dict[str, str] = {}
        for investor_type in INVESTOR_TYPES:
            provider_rows = provider_histories.get(investor_type, [])
            stored_rows = stored_histories.get(investor_type, [])
            histories[investor_type] = self._merge_histories(provider_rows, stored_rows)
            if provider_rows:
                fallback_levels[investor_type] = provider_levels[investor_type]
            else:
                fallback_levels[investor_type] = self._stored_fallback_level(
                    stored_rows, cutoff
                )

        investors = {
            investor_type: self._investor_context(
                investor_type,
                histories.get(investor_type, []),
                cutoff,
                fallback_levels[investor_type],
            )
            for investor_type in INVESTOR_TYPES
        }
        score, normalized_weights = _weighted_score(
            {key: value["score"] for key, value in investors.items()}, INVESTOR_WEIGHTS
        )
        combined = self._combined_context(investors)
        completeness = (
            sum(Decimal(str(item["completeness"])) for item in investors.values())
            / Decimal(len(INVESTOR_TYPES))
        )
        available = [item for item in investors.values() if item["score"] is not None]
        if not available:
            confidence = Decimal("0")
            freshness = "missing"
        else:
            freshness_value = sum(
                FRESHNESS_FACTORS.get(item["freshness"], Decimal("0")) for item in available
            ) / Decimal(len(available))
            confidence = _clamp(
                completeness * Decimal("0.70")
                + freshness_value * Decimal("100") * Decimal("0.30"),
                Decimal("0"),
                Decimal("100"),
            )
            freshness = _worst_freshness(item["freshness"] for item in available)

        return {
            "evaluation_at": evaluation_at.isoformat(),
            "cutoff_date": cutoff.isoformat(),
            "unit": "INR_CRORE",
            "investors": investors,
            "combined": combined,
            "institutional_score": _number(score),
            "direction": classify_institutional_direction(score),
            "freshness": freshness,
            "confidence": _number(confidence),
            "completeness": _number(completeness),
            "fallback_by_investor": fallback_levels,
            "provider_attempts": self._public_attempts(provider_attempts),
            "score_breakdown": {
                "configured_investor_weights": {
                    key: _number(value) for key, value in INVESTOR_WEIGHTS.items()
                },
                "normalized_investor_weights": normalized_weights,
                "direction_thresholds": {
                    key: _number(value)
                    for key, value in INSTITUTIONAL_DIRECTION_THRESHOLDS.items()
                },
            },
            "provenance": {
                "dataset": "provisional_cash_market_institutional_activity",
                "fii_identity": "FII/FPI normalized to canonical investor_type FII",
                "calculation": "net_value = buy_value - sell_value; provider net is not trusted",
                "storage_precision": (
                    "legacy database columns are float; all intelligence math uses Decimal"
                ),
                "timestamp_precision": "date",
            },
            "limitations": {
                "publication": "Date-only stored flows use a prior-local-date replay cutoff.",
                "scope": (
                    "Cash-market provisional institutional activity, not derivatives or "
                    "custody data."
                ),
            },
        }

    @staticmethod
    def _investor_context(
        investor_type: str,
        history: list[_FlowObservation],
        cutoff: date,
        fallback_level: str,
    ) -> dict[str, Any]:
        base = {
            "investor_type": investor_type,
            "source_investor_label": "FII/FPI" if investor_type == "FII" else "DII",
            "market_date": None,
            "buy_value": None,
            "sell_value": None,
            "net_value": None,
            "cumulative": {horizon: None for horizon in ROLLING_HORIZONS},
            "score": None,
            "direction": None,
            "freshness": "missing",
            "confidence": 0.0,
            "completeness": 0.0,
            "source_name": None,
            "source_reference": None,
            "observed_at": None,
            "fetched_at": None,
            "cutoff_date": cutoff.isoformat(),
            "fallback_level": fallback_level,
            "score_breakdown": {},
        }
        normalized: list[tuple[_FlowObservation, Decimal, Decimal, Decimal]] = []
        for row in history:
            if row.buy_value is None or row.sell_value is None:
                continue
            buy = Decimal(str(row.buy_value))
            sell = Decimal(str(row.sell_value))
            if not buy.is_finite() or not sell.is_finite() or buy < 0 or sell < 0:
                continue
            normalized.append((row, buy, sell, buy - sell))
        if not normalized:
            return base

        rolling: dict[int, Decimal | None] = {}
        for horizon in ROLLING_HORIZONS:
            rolling[horizon] = (
                sum((item[3] for item in normalized[:horizon]), Decimal("0"))
                if len(normalized) >= horizon
                else None
            )
        components = {
            horizon: (
                _clamp(value / HORIZON_FULL_SCALE_CRORE[horizon] * Decimal("100"))
                if value is not None
                else None
            )
            for horizon, value in rolling.items()
        }
        score, weights = _weighted_score(components, HORIZON_WEIGHTS)
        latest, buy, sell, net = normalized[0]
        completeness = (
            Decimal(sum(value is not None for value in rolling.values()))
            / Decimal(len(ROLLING_HORIZONS))
            * Decimal("100")
        )
        freshness = (
            "stale"
            if latest.trade_date < cutoff
            else latest.freshness_status
            if latest.freshness_status in FRESHNESS_FACTORS
            else "stale"
        )
        confidence = _clamp(
            completeness * Decimal("0.70")
            + FRESHNESS_FACTORS[freshness] * Decimal("100") * Decimal("0.30"),
            Decimal("0"),
            Decimal("100"),
        )
        base.update(
            {
                "market_date": latest.trade_date.isoformat(),
                "buy_value": _number(buy),
                "sell_value": _number(sell),
                "net_value": _number(net),
                "cumulative": {key: _number(value) for key, value in rolling.items()},
                "score": _number(score),
                "direction": classify_institutional_direction(score),
                "freshness": freshness,
                "confidence": _number(confidence),
                "completeness": _number(completeness),
                "source_name": latest.source_name,
                "source_reference": latest.source_reference,
                "observed_at": latest.observed_at.isoformat() if latest.observed_at else None,
                "fetched_at": latest.fetched_at.isoformat() if latest.fetched_at else None,
                "score_breakdown": {
                    "component_signals": {
                        key: _number(value) for key, value in components.items()
                    },
                    "normalized_horizon_weights": weights,
                },
            }
        )
        return base

    async def _provider_histories(
        self,
        start_date: date,
        end_date: date,
        evaluation_at: datetime,
    ) -> tuple[
        dict[str, list[_FlowObservation]],
        list[ProviderResult],
        dict[str, str],
    ]:
        selected: dict[str, list[_FlowObservation]] = {}
        levels: dict[str, str] = {}
        attempts: list[ProviderResult] = []
        supporting = [
            provider
            for provider in self._providers
            if INSTITUTIONAL_PROVIDER_MARKET
            in getattr(provider, "supports_markets", [])
        ]
        for position, provider in enumerate(supporting):
            try:
                result = await provider.fetch(
                    evaluation_at=evaluation_at,
                    start_date=start_date,
                    end_date=end_date,
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
            histories = self._validated_provider_histories(
                result, start_date, end_date, evaluation_at
            )
            for investor_type in INVESTOR_TYPES:
                if investor_type in selected or not histories.get(investor_type):
                    continue
                selected[investor_type] = histories[investor_type]
                levels[investor_type] = (
                    "primary_provider" if position == 0 else "secondary_provider"
                )
            if len(selected) == len(INVESTOR_TYPES):
                break
        return selected, attempts, levels

    @staticmethod
    def _validated_provider_histories(
        result: ProviderResult,
        start_date: date,
        end_date: date,
        evaluation_at: datetime,
    ) -> dict[str, list[_FlowObservation]]:
        if result.status not in {"success", "cached", "stale"}:
            return {}
        payload = result.data if isinstance(result.data, list) else [result.data]
        if not payload or not all(isinstance(item, dict) for item in payload):
            return {}
        deduplicated: dict[tuple[str, date], tuple[datetime, _FlowObservation]] = {}
        for item in payload:
            investor_type = item.get("investor_type")
            if (
                investor_type not in INVESTOR_TYPES
                or item.get("source_investor_label") != EXPECTED_LABELS[investor_type]
                or item.get("unit") != EXPECTED_UNIT
                or item.get("dataset") != EXPECTED_DATASET
            ):
                return {}
            try:
                trade_date = item["trade_date"]
                observed_at = item["observed_at"]
                available_at = item["available_at"]
                buy = Decimal(str(item["buy_value"]))
                sell = Decimal(str(item["sell_value"]))
            except (InvalidOperation, KeyError, TypeError, ValueError):
                return {}
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
                or not buy.is_finite()
                or not sell.is_finite()
                or buy < 0
                or sell < 0
                or not str(item.get("source_reference") or "").strip()
            ):
                return {}
            observation = _FlowObservation(
                trade_date=trade_date,
                investor_type=investor_type,
                buy_value=buy,
                sell_value=sell,
                source_name=result.provider_name,
                source_reference=str(item["source_reference"]),
                observed_at=observed_at,
                fetched_at=available_at,
                freshness_status=result.freshness,
            )
            key = (investor_type, trade_date)
            previous = deduplicated.get(key)
            if previous is None or available_at > previous[0]:
                deduplicated[key] = (available_at, observation)
        histories: dict[str, list[_FlowObservation]] = defaultdict(list)
        for _, observation in deduplicated.values():
            histories[observation.investor_type].append(observation)
        for history in histories.values():
            history.sort(key=lambda row: row.trade_date, reverse=True)
        return dict(histories)

    @staticmethod
    def _stored_observation(row: InstitutionalFlow) -> _FlowObservation | None:
        if row.buy_value is None or row.sell_value is None:
            return None
        buy = Decimal(str(row.buy_value))
        sell = Decimal(str(row.sell_value))
        if not buy.is_finite() or not sell.is_finite() or buy < 0 or sell < 0:
            return None
        return _FlowObservation(
            trade_date=row.trade_date,
            investor_type=row.investor_type,
            buy_value=buy,
            sell_value=sell,
            source_name=row.source_name,
            source_reference=None,
            observed_at=row.observed_at,
            fetched_at=row.fetched_at,
            freshness_status=row.freshness_status,
        )

    @staticmethod
    def _merge_histories(
        provider_rows: list[_FlowObservation],
        stored_rows: list[_FlowObservation],
    ) -> list[_FlowObservation]:
        by_date = {row.trade_date: row for row in stored_rows}
        by_date.update({row.trade_date: row for row in provider_rows})
        return sorted(by_date.values(), key=lambda row: row.trade_date, reverse=True)

    @staticmethod
    def _stored_fallback_level(history: list[_FlowObservation], cutoff: date) -> str:
        if not history:
            return "unavailable"
        return (
            "exact_stored_observation"
            if history[0].trade_date == cutoff
            else "stale_stored_observation"
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
    def _combined_context(investors: dict[str, dict[str, Any]]) -> dict[str, Any]:
        cumulative: dict[int, float | None] = {}
        for horizon in ROLLING_HORIZONS:
            values = [investors[key]["cumulative"][horizon] for key in INVESTOR_TYPES]
            cumulative[horizon] = (
                _number(sum((Decimal(str(value)) for value in values), Decimal("0")))
                if all(value is not None for value in values)
                else None
            )
        daily_values = [investors[key]["net_value"] for key in INVESTOR_TYPES]
        daily = (
            _number(sum((Decimal(str(value)) for value in daily_values), Decimal("0")))
            if all(value is not None for value in daily_values)
            else None
        )
        return {"daily_net_value": daily, "cumulative": cumulative}


def _weighted_score(
    values: dict[Any, Decimal | float | int | None],
    weights: dict[Any, Decimal],
) -> tuple[Decimal | None, dict[Any, float]]:
    available = {
        key: Decimal(str(value)) for key, value in values.items() if value is not None
    }
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
    low: Decimal = Decimal("-100"),
    high: Decimal = Decimal("100"),
) -> Decimal:
    return max(low, min(high, value))


def _number(value: Decimal | None, places: int = 2) -> float | None:
    if value is None:
        return None
    quantum = Decimal("1").scaleb(-places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))