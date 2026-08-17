from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.macro_event_catalog import (
    CORE_EVENT_CODES,
    DIRECTION_INVERTED,
    MACRO_EVENT_BY_CODE,
    SECTOR_EVENT_CODES,
    VALID_MACRO_EVENT_CODES,
)
from app.market_intelligence.providers.base import MarketProvider, ProviderResult
from app.models.macro_event import EconEvent
from app.repositories.macro_event import EconEventRepository

MACRO_PROVIDER_MARKET = "INDIA_MACRO_EVENTS"

# Both core and sector events are candidates; only core events drive the
# headline macro_score domains, while sector/external/fiscal events feed the
# supporting ``other`` domain and completeness.
ALL_TRACKED_CODES: tuple[str, ...] = tuple(sorted(VALID_MACRO_EVENT_CODES))

# Deterministic lookback for released actuals and lookahead for upcoming events.
LOOKBACK_DAYS = 45
LOOKAHEAD_DAYS = 14

# Surprise that is not knowable must never contribute direction or magnitude.
FRESHNESS_FACTORS = {
    "live": Decimal("1"),
    "recent": Decimal("0.85"),
    "stale": Decimal("0.40"),
    "missing": Decimal("0"),
}

MACRO_DIRECTION_THRESHOLDS: dict[str, Decimal] = {
    "strong_supportive": Decimal("60"),
    "supportive": Decimal("25"),
    "restrictive": Decimal("-25"),
    "strongly_restrictive": Decimal("-60"),
}

# Importance weights drive both domain weighting and upcoming-event risk.
IMPORTANCE_WEIGHTS: dict[str, Decimal] = {
    "high": Decimal("1.0"),
    "medium": Decimal("0.5"),
    "low": Decimal("0.2"),
}

# Primary domain weights for the headline macro_score. Weights are renormalized
# over whatever domains actually have a signal.
MACRO_SCORE_WEIGHTS: dict[str, Decimal] = {
    "monetary": Decimal("0.30"),
    "inflation": Decimal("0.30"),
    "growth": Decimal("0.20"),
    "employment": Decimal("0.10"),
    "other": Decimal("0.10"),
}

# Categories are folded into domains.
GROWTH_CATEGORIES = frozenset({"growth", "business_activity"})
OTHER_CATEGORIES = frozenset({"external", "fiscal", "sector"})

# Confidence composition: completeness, freshness, and signal agreement.
CONFIDENCE_WEIGHTS: dict[str, Decimal] = {
    "completeness": Decimal("0.55"),
    "freshness": Decimal("0.25"),
    "signal_agreement": Decimal("0.20"),
}

# Upcoming-event risk parameters. High-uncertainty scheduled events dampen the
# magnitude of the macro_score but never reverse its direction.
UPCOMING_RISK_IMPORTANCE_WEIGHTS = IMPORTANCE_WEIGHTS
UPCOMING_RISK_FULL_WEIGHT = Decimal("2.0")
UPCOMING_RISK_MAX_DAMPEN = Decimal("0.25")

FRESHNESS_QUALITY: dict[str, Decimal] = {
    "recent": Decimal("100"),
    "stale": Decimal("40"),
    "missing": Decimal("0"),
}

SCORE_MIN = Decimal("-100")
SCORE_MAX = Decimal("100")


@dataclass(frozen=True)
class _EventObservation:
    event_code: str
    reference_time: datetime
    actual: Decimal | None
    forecast: Decimal | None
    previous: Decimal | None
    source_name: str | None
    source_reference: str | None
    observed_at: datetime | None
    fetched_at: datetime | None
    freshness_status: str


def classify_macro_direction(score: Decimal | float | int | None) -> str | None:
    if score is None:
        return None
    value = Decimal(str(score))
    if value >= MACRO_DIRECTION_THRESHOLDS["strong_supportive"]:
        return "strong_supportive"
    if value >= MACRO_DIRECTION_THRESHOLDS["supportive"]:
        return "supportive"
    if value > MACRO_DIRECTION_THRESHOLDS["restrictive"]:
        return "neutral"
    if value > MACRO_DIRECTION_THRESHOLDS["strongly_restrictive"]:
        return "restrictive"
    return "strongly_restrictive"


class MacroIntelligence:
    """Deterministic macro & event intelligence for Indian equities."""

    def __init__(
        self,
        session: AsyncSession,
        providers: tuple[MarketProvider, ...] = (),
        *,
        event_repo: EconEventRepository | None = None,
    ) -> None:
        self._providers = providers
        self._event_repo = event_repo or EconEventRepository(session)

    async def evaluate(self, evaluation_at: datetime) -> dict[str, Any]:
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("evaluation_at must be timezone-aware")
        start_time = evaluation_at - timedelta(days=LOOKBACK_DAYS)
        end_time = evaluation_at + timedelta(days=LOOKAHEAD_DAYS)

        stored = await self._event_repo.get_range_for_codes(
            start_time, end_time, ALL_TRACKED_CODES
        )
        stored_by_code = defaultdict(list)
        for row in stored:
            observation = self._stored_observation(row, evaluation_at)
            if observation is not None:
                stored_by_code[row.event_code].append(observation)

        attempts, provider_rows = await self._provider_rows(
            start_time, end_time, evaluation_at
        )
        provider_by_code = defaultdict(list)
        for observation in provider_rows:
            provider_by_code[observation.event_code].append(observation)

        merged: dict[str, list[_EventObservation]] = {}
        for code in ALL_TRACKED_CODES:
            merged[code] = _merge_histories(
                provider_by_code.get(code, []), stored_by_code.get(code, [])
            )

        event_contexts = {
            code: self._event_context(code, merged.get(code, []), evaluation_at)
            for code in ALL_TRACKED_CODES
        }

        domains = self._build_domains(event_contexts)
        base_score, weights = _weighted_score(
            {key: value["score"] for key, value in domains["primary"].items()},
            MACRO_SCORE_WEIGHTS,
        )
        upcoming_risk = _upcoming_event_risk(event_contexts, evaluation_at)
        macro_score = (
            _clamp(base_score * (Decimal("1") - upcoming_risk))
            if base_score is not None
            else None
        )

        completeness = self._completeness(event_contexts)
        confidence, freshness = self._confidence(
            completeness, event_contexts, domains
        )

        return {
            "evaluation_at": evaluation_at.isoformat(),
            "lookback_start": start_time.isoformat(),
            "lookahead_end": end_time.isoformat(),
            "macro_score": _number(macro_score),
            "direction": classify_macro_direction(macro_score),
            "freshness": freshness,
            "confidence": _number(confidence),
            "completeness": _number(completeness),
            "domains": domains,
            "upcoming_risk": _number(upcoming_risk * Decimal("100")),
            "upcoming_events": [
                event_contexts[code]
                for code in ALL_TRACKED_CODES
                if event_contexts[code]["lifecycle"] == "upcoming"
            ],
            "events": {
                code: event_contexts[code] for code in ALL_TRACKED_CODES
            },
            "provider_attempts": self._public_attempts(attempts),
            "score_breakdown": {
                "configured_weights": {
                    key: _number(value) for key, value in MACRO_SCORE_WEIGHTS.items()
                },
                "normalized_weights": weights,
                "importance_weights": {
                    key: _number(value)
                    for key, value in IMPORTANCE_WEIGHTS.items()
                },
                "direction_thresholds": {
                    key: _number(value)
                    for key, value in MACRO_DIRECTION_THRESHOLDS.items()
                },
                "upcoming_risk": {
                    "full_weight": _number(UPCOMING_RISK_FULL_WEIGHT),
                    "max_dampen": _number(UPCOMING_RISK_MAX_DAMPEN),
                },
            },
            "provenance": {
                "catalog": "app/market_intelligence/macro_event_catalog.py",
                "catalog_size": len(VALID_MACRO_EVENT_CODES),
                "core_events": sorted(CORE_EVENT_CODES),
                "sector_events": sorted(SECTOR_EVENT_CODES),
                "surprise": (
                    "surprise = actual - forecast when a consensus forecast is "
                    "available, else actual - previous; normalized by the "
                    "declarative surprise_full_scale; inverted-direction events "
                    "(inflation, unemployment) flip the sign."
                ),
                "lifecycle": (
                    "upcoming (reference_time after evaluation) contributes no "
                    "directional signal; known events with a knowable actual "
                    "contribute; scheduled-but-not-knowable and missing events "
                    "are excluded from direction."
                ),
                "no_look_ahead": (
                    "provider rows and persisted rows are only eligible when "
                    "observed_at and available_at are on or before evaluation_at."
                ),
                "storage_precision": (
                    "econ_events numeric columns are Decimal/Numeric; all "
                    "intelligence math uses Decimal."
                ),
                "timestamp_precision": "aware timestamps (Asia/Kolkata, America/New_York)",
            },
            "limitations": {
                "source": (
                    "No stable audited automation contract; values only enter via "
                    "a validated local import or validated provider. Missing data "
                    "is reported, never fabricated."
                ),
                "forecast": (
                    "Consensus forecasts are not licensed automatically; where a "
                    "forecast is absent the surprise falls back to previous, and "
                    "where neither is present the event contributes no direction."
                ),
                "scope": (
                    "A declarative set of known scheduled events; does not scrape "
                    "unreliable news indiscriminately."
                ),
            },
        }

    @staticmethod
    def _stored_observation(
        row: EconEvent, evaluation_at: datetime
    ) -> _EventObservation | None:
        if row.event_code not in VALID_MACRO_EVENT_CODES:
            return None
        actual = _to_decimal(row.actual)
        forecast = _to_decimal(row.forecast)
        previous = _to_decimal(row.previous)
        observed_at = row.observed_at
        available_at = row.available_at
        if available_at is not None and available_at > evaluation_at:
            # A persisted row whose availability has not arrived cannot have its
            # values treated as known; drop it from directional consideration.
            return None
        if (
            observed_at is not None
            and observed_at.tzinfo is not None
            and observed_at.utcoffset() is not None
            and observed_at > evaluation_at
        ):
            return None
        return _EventObservation(
            event_code=row.event_code,
            reference_time=row.reference_time,
            actual=actual,
            forecast=forecast,
            previous=previous,
            source_name=row.source_name,
            source_reference=row.source_reference,
            observed_at=observed_at,
            fetched_at=row.fetched_at,
            freshness_status=row.freshness_status
            if row.freshness_status in FRESHNESS_FACTORS
            else "stale",
        )

    async def _provider_rows(
        self,
        start_time: datetime,
        end_time: datetime,
        evaluation_at: datetime,
    ) -> tuple[list[ProviderResult], list[_EventObservation]]:
        supporting = [
            provider
            for provider in self._providers
            if MACRO_PROVIDER_MARKET in getattr(provider, "supports_markets", [])
        ]
        attempts: list[ProviderResult] = []
        observations: list[_EventObservation] = []
        for provider in supporting:
            try:
                result = await provider.fetch(
                    evaluation_at=evaluation_at,
                    start_time=start_time,
                    end_time=end_time,
                    event_codes=ALL_TRACKED_CODES,
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
            observations.extend(
                self._validated_provider_events(result, start_time, end_time, evaluation_at)
            )
        return attempts, observations

    @staticmethod
    def _validated_provider_events(
        result: ProviderResult,
        start_time: datetime,
        end_time: datetime,
        evaluation_at: datetime,
    ) -> list[_EventObservation]:
        if result.status not in {"success", "cached", "stale"}:
            return []
        payload = result.data if isinstance(result.data, list) else [result.data]
        if not payload or not all(isinstance(item, dict) for item in payload):
            return []
        deduplicated: dict[tuple[str, datetime], tuple[datetime, _EventObservation]] = {}
        for item in payload:
            code = item.get("event_code")
            if code not in VALID_MACRO_EVENT_CODES:
                continue
            try:
                reference_time = item["reference_time"]
                observed_at = item["observed_at"]
                available_at = item["available_at"]
                actual = _to_decimal(item.get("actual"))
                forecast = _to_decimal(item.get("forecast"))
                previous = _to_decimal(item.get("previous"))
            except (InvalidOperation, KeyError, TypeError, ValueError):
                continue
            if (
                not isinstance(reference_time, datetime)
                or reference_time.tzinfo is None
                or reference_time.utcoffset() is None
                or not isinstance(observed_at, datetime)
                or observed_at.tzinfo is None
                or observed_at.utcoffset() is None
                or not isinstance(available_at, datetime)
                or available_at.tzinfo is None
                or available_at.utcoffset() is None
                or available_at < observed_at
                or observed_at > evaluation_at
                or available_at > evaluation_at
                or not start_time <= reference_time <= end_time
                or not str(item.get("source_reference") or "").strip()
            ):
                continue
            observation = _EventObservation(
                event_code=code,
                reference_time=reference_time,
                actual=actual,
                forecast=forecast,
                previous=previous,
                source_name=result.provider_name,
                source_reference=str(item["source_reference"]),
                observed_at=observed_at,
                fetched_at=available_at,
                freshness_status=result.freshness,
            )
            key = (code, reference_time)
            previous_entry = deduplicated.get(key)
            if previous_entry is None or available_at > previous_entry[0]:
                deduplicated[key] = (available_at, observation)
        return list(value for _, value in deduplicated.values())

    @staticmethod
    def _event_context(
        code: str,
        history: list[_EventObservation],
        evaluation_at: datetime,
    ) -> dict[str, Any]:
        definition = MACRO_EVENT_BY_CODE.get(code)
        base = {
            "event_code": code,
            "name": definition.name if definition else code,
            "category": definition.category if definition else "unknown",
            "importance": definition.importance if definition else "low",
            "reference_time": None,
            "lifecycle": "missing",
            "time_to_event": None,
            "actual": None,
            "forecast": None,
            "previous": None,
            "surprise": None,
            "signal": None,
            "unit": definition.unit if definition else None,
            "available_at": None,
            "source_name": None,
            "source_reference": None,
            "freshness_status": "missing",
        }
        if not history or definition is None:
            return base

        latest = history[0]
        reference_time = latest.reference_time
        base["reference_time"] = reference_time.isoformat()
        time_to_event = (reference_time - evaluation_at).total_seconds() / 86400.0
        base["time_to_event"] = _number(Decimal(str(round(time_to_event, 4))), 4)

        if reference_time > evaluation_at:
            # Scheduled but not yet occurred: contributes no directional signal.
            base.update(
                {
                    "lifecycle": "upcoming",
                    "available_at": (
                        latest.fetched_at.isoformat() if latest.fetched_at else None
                    ),
                    "source_name": latest.source_name,
                    "source_reference": latest.source_reference,
                    "freshness_status": latest.freshness_status,
                }
            )
            return base

        # reference_time has passed: the event is known only if its actual is
        # knowable (available on or before evaluation).
        knowable = (
            latest.actual is not None
            and latest.fetched_at is not None
            and latest.fetched_at <= evaluation_at
        )
        if not knowable:
            base.update(
                {
                    "lifecycle": "scheduled_not_knowable",
                    "available_at": (
                        latest.fetched_at.isoformat() if latest.fetched_at else None
                    ),
                    "source_name": latest.source_name,
                    "source_reference": latest.source_reference,
                    "freshness_status": latest.freshness_status,
                }
            )
            return base

        base.update(
            {
                "lifecycle": "known",
                "actual": _number(latest.actual),
                "forecast": _number(latest.forecast),
                "previous": _number(latest.previous),
                "available_at": latest.fetched_at.isoformat(),
                "source_name": latest.source_name,
                "source_reference": latest.source_reference,
                "freshness_status": latest.freshness_status,
            }
        )
        surprise = _surprise(latest, definition.has_forecast)
        if surprise is None:
            return base
        base["surprise"] = _number(surprise)
        raw_signal = _clamp(
            surprise / Decimal(str(definition.surprise_full_scale)) * Decimal("100")
        )
        sign = Decimal("-1") if definition.direction == DIRECTION_INVERTED else Decimal("1")
        base["signal"] = _number(raw_signal * sign)
        return base

    def _build_domains(self, event_contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        signals: dict[str, list[tuple[Decimal, Decimal]]] = defaultdict(list)
        for code, context in event_contexts.items():
            definition = MACRO_EVENT_BY_CODE.get(code)
            if definition is None or context["signal"] is None:
                continue
            importance = IMPORTANCE_WEIGHTS[definition.importance]
            signals[_domain_for_category(definition.category)].append(
                (Decimal(str(context["signal"])), importance)
            )
        primary: dict[str, dict[str, Any]] = {}
        for domain in ("monetary", "inflation", "growth", "employment", "other"):
            entries = signals.get(domain, [])
            if not entries:
                primary[domain] = {
                    "score": None,
                    "contributors": [],
                    "freshness": "missing",
                }
                continue
            total_weight = sum((weight for _, weight in entries), Decimal("0"))
            score = sum(
                (signal * (weight / total_weight) for signal, weight in entries),
                Decimal("0"),
            )
            primary[domain] = {
                "score": _number(score),
                "contributors": [code for code, _ in entries],
                "freshness": "recent",
            }
        return {"primary": primary, "supporting": {"other": primary["other"]}}

    @staticmethod
    def _completeness(event_contexts: dict[str, dict[str, Any]]) -> Decimal:
        core = [code for code in ALL_TRACKED_CODES if code in CORE_EVENT_CODES]
        if not core:
            return Decimal("0")
        rowed = sum(
            1
            for code in core
            if event_contexts[code]["lifecycle"] != "missing"
        )
        return Decimal(rowed) / Decimal(len(core)) * Decimal("100")

    def _confidence(
        self,
        completeness: Decimal,
        event_contexts: dict[str, dict[str, Any]],
        domains: dict[str, Any],
    ) -> tuple[Decimal, str]:
        known = [
            context
            for context in event_contexts.values()
            if context["lifecycle"] == "known"
        ]
        if not known:
            return Decimal("0"), "missing"
        # Freshness derives from the stored/provider freshness of known events.
        freshness_note = _worst_known_freshness(known)
        freshness_value = FRESHNESS_FACTORS.get(freshness_note, Decimal("0")) * Decimal("100")
        agreement = _signal_agreement(domains)
        confidence = _clamp(
            completeness * CONFIDENCE_WEIGHTS["completeness"]
            + freshness_value * CONFIDENCE_WEIGHTS["freshness"]
            + agreement * CONFIDENCE_WEIGHTS["signal_agreement"],
            Decimal("0"),
            Decimal("100"),
        )
        return confidence, freshness_note

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


def _domain_for_category(category: str) -> str:
    if category == "monetary_policy":
        return "monetary"
    if category == "inflation":
        return "inflation"
    if category == "employment":
        return "employment"
    if category in GROWTH_CATEGORIES:
        return "growth"
    if category in OTHER_CATEGORIES:
        return "other"
    return "other"


def _surprise(observation: _EventObservation, has_forecast: bool) -> Decimal | None:
    if observation.actual is None:
        return None
    if has_forecast and observation.forecast is not None:
        return observation.actual - observation.forecast
    if observation.previous is not None:
        return observation.actual - observation.previous
    return None


def _merge_histories(
    provider_rows: list[_EventObservation],
    stored_rows: list[_EventObservation],
) -> list[_EventObservation]:
    by_reference = {row.reference_time: row for row in stored_rows}
    for row in provider_rows:
        if row.reference_time not in by_reference:
            by_reference[row.reference_time] = row
    return sorted(by_reference.values(), key=lambda row: row.reference_time, reverse=True)


def _worst_known_freshness(known: list[dict[str, Any]]) -> str:
    priority = {"missing": 0, "stale": 1, "recent": 2, "live": 3}
    values = [
        context.get("freshness_status", "stale")
        if context.get("freshness_status") in FRESHNESS_FACTORS
        else "stale"
        for context in known
    ]
    worst = min(values, key=lambda value: priority.get(value, 0)) if values else "missing"
    return worst


def _upcoming_event_risk(
    event_contexts: dict[str, dict[str, Any]], evaluation_at: datetime
) -> Decimal:
    total = Decimal("0")
    for context in event_contexts.values():
        if context["lifecycle"] != "upcoming":
            continue
        importance = context.get("importance", "low")
        total += IMPORTANCE_WEIGHTS.get(importance, Decimal("0"))
    fraction = _clamp(
        total / UPCOMING_RISK_FULL_WEIGHT,
        Decimal("0"),
        Decimal("1"),
    )
    return fraction * UPCOMING_RISK_MAX_DAMPEN


def _signal_agreement(domains: dict[str, Any]) -> Decimal:
    primary = domains["primary"]
    positive = [
        Decimal(str(value["score"]))
        for value in primary.values()
        if value["score"] is not None and Decimal(str(value["score"])) > 0
    ]
    negative = [
        Decimal(str(value["score"]))
        for value in primary.values()
        if value["score"] is not None and Decimal(str(value["score"])) < 0
    ]
    if positive and negative:
        balance = min(
            abs(sum(positive)), abs(sum(negative))
        ) / (abs(sum(positive)) + abs(sum(negative)))
        return Decimal("100") * (Decimal("1") - balance * Decimal("0.8"))
    return Decimal("100")


def _weighted_score(
    values: dict[str, Decimal | float | int | None],
    weights: dict[str, Decimal],
) -> tuple[Decimal | None, dict[str, float]]:
    available = {
        key: Decimal(str(value)) for key, value in values.items() if value is not None
    }
    if not available:
        return None, {}
    total = sum(weights[key] for key in available)
    normalized = {key: weights[key] / total for key in available}
    score = sum(available[key] * normalized[key] for key in available)
    return _clamp(score), {key: _number(value, 4) for key, value in normalized.items()}


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


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
