from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.index_catalog import GIFT_NIFTY_INDEX_CODE
from app.market_intelligence.providers.base import MarketProvider, ProviderResult
from app.market_intelligence.providers.gift_nifty_provider import EXPECTED_IDENTITY
from app.market_intelligence.session_dates import (
    INDIA_TIMEZONE,
    gift_nifty_completed_daily_cutoff_date,
    gift_nifty_session_context,
    indian_cash_context,
    indian_cash_reference_cutoff_date,
)
from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository

NIFTY_REFERENCE_CODE = "NIFTY_50"

GAP_DIRECTION_THRESHOLDS: dict[str, Decimal] = {
    "strong_gap_up": Decimal("0.75"),
    "gap_up": Decimal("0.20"),
    "gap_down": Decimal("-0.20"),
    "strong_gap_down": Decimal("-0.75"),
}
SCORE_FULL_SCALE_GAP_PERCENT = Decimal("1.00")

LIVE_MAX_AGE_MINUTES = Decimal("15")
RECENT_MAX_AGE_MINUTES = Decimal("120")
FRESHNESS_CONFIDENCE_FACTORS: dict[str, Decimal] = {
    "live": Decimal("1"),
    "recent": Decimal("0.75"),
    "stale": Decimal("0.25"),
    "missing": Decimal("0"),
}
CONTEXT_CONFIDENCE_POINTS: dict[str, Decimal] = {
    "pre_open": Decimal("20"),
    "cash_market_open": Decimal("12"),
    "post_close": Decimal("6"),
    "weekend": Decimal("4"),
    "holiday": Decimal("4"),
    "unavailable": Decimal("0"),
}
COMPLETENESS_WEIGHTS: dict[str, Decimal] = {
    "gift_observation": Decimal("45"),
    "nifty_reference": Decimal("45"),
    "momentum": Decimal("10"),
}


def classify_gift_direction(gap_percent: Decimal | int | float | None) -> str | None:
    if gap_percent is None:
        return None
    value = Decimal(str(gap_percent))
    if value >= GAP_DIRECTION_THRESHOLDS["strong_gap_up"]:
        return "strong_gap_up"
    if value >= GAP_DIRECTION_THRESHOLDS["gap_up"]:
        return "gap_up"
    if value <= GAP_DIRECTION_THRESHOLDS["strong_gap_down"]:
        return "strong_gap_down"
    if value <= GAP_DIRECTION_THRESHOLDS["gap_down"]:
        return "gap_down"
    return "flat"


class GiftNiftyIntelligence:
    """Deterministic GIFT Nifty opening context at an aware evaluation instant."""

    _LOOKBACK_DAYS = 400

    def __init__(
        self,
        session: AsyncSession,
        providers: tuple[MarketProvider, ...] = (),
        *,
        price_repo: MarketIndexPriceRepository | None = None,
        cash_holidays: Iterable[date] = (),
    ) -> None:
        self._providers = providers
        self._price_repo = price_repo or MarketIndexPriceRepository(session)
        self._cash_holidays = frozenset(cash_holidays)

    async def evaluate(self, evaluation_at: datetime) -> dict[str, Any]:
        if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
            raise ValueError("evaluation_at must be timezone-aware")

        local = evaluation_at.astimezone(ZoneInfo(INDIA_TIMEZONE))
        cash_context = indian_cash_context(
            evaluation_at, holiday_dates=self._cash_holidays
        )
        nifty_cutoff = indian_cash_reference_cutoff_date(evaluation_at)
        gift_daily_cutoff = gift_nifty_completed_daily_cutoff_date(evaluation_at)
        end_date = max(nifty_cutoff, gift_daily_cutoff)
        start_date = min(nifty_cutoff, gift_daily_cutoff) - timedelta(
            days=self._LOOKBACK_DAYS
        )
        rows = await self._price_repo.get_ranges_by_index_codes(
            (GIFT_NIFTY_INDEX_CODE, NIFTY_REFERENCE_CODE), start_date, end_date
        )
        gift_rows, nifty_rows = self._group_knowable_rows(rows, evaluation_at)

        provider_attempts: list[ProviderResult] = []
        observation: dict[str, Any] | None = None
        fallback_level = "unavailable"
        for position, provider in enumerate(self._supporting_providers()):
            result = await provider.fetch(
                index_code=GIFT_NIFTY_INDEX_CODE,
                evaluation_at=evaluation_at,
            )
            provider_attempts.append(result)
            observation = self._provider_observation(result, evaluation_at)
            if observation is not None:
                fallback_level = "primary_provider" if position == 0 else "secondary_provider"
                break

        if observation is None:
            eligible_gift = [row for row in gift_rows if row.trade_date <= gift_daily_cutoff]
            if eligible_gift:
                latest_gift = max(eligible_gift, key=lambda row: row.trade_date)
                fallback_level = (
                    "exact_stored_observation"
                    if latest_gift.trade_date == gift_daily_cutoff
                    else "stale_stored_observation"
                )
                observation = self._stored_observation(latest_gift)

        eligible_nifty = [row for row in nifty_rows if row.trade_date <= nifty_cutoff]
        nifty_reference = (
            max(eligible_nifty, key=lambda row: row.trade_date) if eligible_nifty else None
        )

        gift_price = observation["price"] if observation is not None else None
        reference_close = (
            Decimal(str(nifty_reference.close)) if nifty_reference is not None else None
        )
        gap_points: Decimal | None = None
        gap_percent: Decimal | None = None
        if gift_price is not None and reference_close not in {None, Decimal("0")}:
            gap_points = gift_price - reference_close
            gap_percent = gap_points / reference_close * Decimal("100")

        direction = classify_gift_direction(gap_percent)
        score = self._score(gap_percent)
        momentum_percent = None  # A single quote or daily row is not intraday history.
        freshness = observation["freshness"] if observation is not None else "missing"
        completeness = self._completeness(
            observation is not None,
            nifty_reference is not None and reference_close != 0,
            momentum_percent is not None,
        )
        output_context = cash_context if observation is not None else "unavailable"
        confidence = self._confidence(
            observation is not None and gap_percent is not None,
            freshness,
            output_context,
            momentum_percent is not None,
        )

        return {
            "evaluation_at": evaluation_at.isoformat(),
            "evaluation_timezone": INDIA_TIMEZONE,
            "context_label": output_context,
            "cash_context_label": cash_context,
            "gift_session_label": gift_nifty_session_context(evaluation_at),
            "gift_observation": self._public_observation(observation),
            "nifty_reference": self._public_reference(nifty_reference, nifty_cutoff),
            "indicated_gap_points": _rounded(gap_points, 4),
            "indicated_gap_percent": _rounded(gap_percent, 4),
            "direction": direction,
            "momentum_percent": momentum_percent,
            "gift_nifty_score": _rounded(score, 2),
            "confidence_score": _rounded(confidence, 2),
            "data_completeness_percent": _rounded(completeness, 2),
            "fallback_level": fallback_level,
            "provider_attempts": [
                {
                    "provider_name": item.provider_name,
                    "status": item.status,
                    "freshness": item.freshness,
                    "error_code": item.error_code,
                }
                for item in provider_attempts
            ],
            "calculation": {
                "gap_points_formula": "gift_nifty_price - nifty_reference_close",
                "gap_percent_formula": (
                    "indicated_gap_points / nifty_reference_close * 100"
                ),
                "direction_thresholds_percent": {
                    key: float(value) for key, value in GAP_DIRECTION_THRESHOLDS.items()
                },
                "score_full_scale_gap_percent": float(SCORE_FULL_SCALE_GAP_PERCENT),
            },
            "limitations": {
                "timestamp_replay": (
                    "Provider/manual observations support aware timestamps. Stored "
                    "MarketIndexPrice fallback is date-only and cannot prove intraday knowability."
                ),
                "momentum": (
                    "Unavailable unless legitimate timestamped same-contract history is supplied."
                ),
                "prediction": "Opening context only; not a guaranteed market prediction.",
                "session_policy": (
                    "Current audited NSE IX hours; historical timing changes are not inferred."
                ),
            },
            "provenance": {
                "instrument": {
                    "index_code": GIFT_NIFTY_INDEX_CODE,
                    "exchange": "NSE_IX",
                    "instrument_type": "FUTIDX",
                    "underlying": NIFTY_REFERENCE_CODE,
                    "currency": "USD",
                },
                "source_policy": (
                    "No undocumented NSE IX endpoint or proxy; explicit provider/manual data, "
                    "then date-only stored fallback."
                ),
                "gift_daily_cutoff_date": gift_daily_cutoff.isoformat(),
                "nifty_reference_cutoff_date": nifty_cutoff.isoformat(),
                "evaluated_local_date": local.date().isoformat(),
            },
        }

    def _supporting_providers(self) -> list[MarketProvider]:
        return [
            provider
            for provider in self._providers
            if GIFT_NIFTY_INDEX_CODE in getattr(provider, "supports_indices", [])
        ]

    @staticmethod
    def _provider_observation(
        result: ProviderResult, evaluation_at: datetime
    ) -> dict[str, Any] | None:
        if result.status not in {"success", "cached", "stale"}:
            return None
        if not isinstance(result.data, dict):
            return None
        if result.data.get("index_code") != GIFT_NIFTY_INDEX_CODE:
            return None
        if any(result.data.get(field) != expected for field, expected in EXPECTED_IDENTITY.items()):
            return None
        contract_symbol = str(result.data.get("contract_symbol") or "").strip().upper()
        if not contract_symbol.startswith("NIFTY"):
            return None
        try:
            price = Decimal(str(result.data["price"]))
            observed_at = datetime.fromisoformat(result.observed_at or "")
            available_at = datetime.fromisoformat(result.fetched_at or "")
        except (InvalidOperation, KeyError, TypeError, ValueError):
            return None
        if (
            not price.is_finite()
            or price <= 0
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
            or available_at.tzinfo is None
            or available_at.utcoffset() is None
            or available_at < observed_at
            or observed_at > evaluation_at
            or available_at > evaluation_at
        ):
            return None
        age_minutes = Decimal(str((evaluation_at - observed_at).total_seconds())) / Decimal("60")
        freshness = _freshness_for_age(age_minutes)
        return {
            "price": price,
            "observed_at": observed_at,
            "available_at": available_at,
            "market_date": result.market_date,
            "timestamp_precision": "timestamp",
            "provider_name": result.provider_name,
            "provider_status": result.status,
            "freshness": freshness,
            "age_minutes": max(age_minutes, Decimal("0")),
            "source_reference": result.data.get("source_reference"),
            "contract_symbol": contract_symbol,
            "expiry_date": result.data.get("expiry_date"),
        }

    @staticmethod
    def _stored_observation(row: MarketIndexPrice) -> dict[str, Any]:
        return {
            "price": Decimal(str(row.close)),
            "observed_at": row.observed_at,
            "available_at": row.fetched_at,
            "market_date": row.trade_date.isoformat(),
            "timestamp_precision": "date",
            "provider_name": row.source_name or "market_index_database",
            "provider_status": "stale",
            "freshness": "stale",
            "age_minutes": None,
            "source_reference": row.source_reference,
            "contract_symbol": None,
            "expiry_date": None,
        }

    @staticmethod
    def _group_knowable_rows(
        rows: list[MarketIndexPrice], evaluation_at: datetime
    ) -> tuple[list[MarketIndexPrice], list[MarketIndexPrice]]:
        gift: list[MarketIndexPrice] = []
        nifty: list[MarketIndexPrice] = []
        local_date = evaluation_at.astimezone(ZoneInfo(INDIA_TIMEZONE)).date()
        for row in rows:
            if row.fetched_at is not None and row.fetched_at > local_date:
                continue
            code = row.market_index.code
            if code == GIFT_NIFTY_INDEX_CODE:
                gift.append(row)
            elif code == NIFTY_REFERENCE_CODE:
                nifty.append(row)
        return gift, nifty

    @staticmethod
    def _score(gap_percent: Decimal | None) -> Decimal | None:
        if gap_percent is None:
            return None
        return _clamp(gap_percent / SCORE_FULL_SCALE_GAP_PERCENT * Decimal("100"))

    @staticmethod
    def _completeness(has_gift: bool, has_reference: bool, has_momentum: bool) -> Decimal:
        value = Decimal("0")
        if has_gift:
            value += COMPLETENESS_WEIGHTS["gift_observation"]
        if has_reference:
            value += COMPLETENESS_WEIGHTS["nifty_reference"]
        if has_momentum:
            value += COMPLETENESS_WEIGHTS["momentum"]
        return _clamp(value, Decimal("0"), Decimal("100"))

    @staticmethod
    def _confidence(
        gap_available: bool,
        freshness: str,
        context: str,
        has_momentum: bool,
    ) -> Decimal:
        if not gap_available:
            return Decimal("0")
        value = Decimal("40")
        value += FRESHNESS_CONFIDENCE_FACTORS.get(freshness, Decimal("0")) * Decimal("30")
        value += CONTEXT_CONFIDENCE_POINTS.get(context, Decimal("0"))
        if has_momentum:
            value += Decimal("10")
        return _clamp(value, Decimal("0"), Decimal("100"))

    @staticmethod
    def _public_observation(observation: dict[str, Any] | None) -> dict[str, Any] | None:
        if observation is None:
            return None
        return {
            **observation,
            "price": _rounded(observation["price"], 4),
            "observed_at": _iso(observation["observed_at"]),
            "available_at": _iso(observation["available_at"]),
            "expiry_date": _iso(observation["expiry_date"]),
            "age_minutes": _rounded(observation["age_minutes"], 2),
        }

    @staticmethod
    def _public_reference(
        reference: MarketIndexPrice | None, cutoff: date
    ) -> dict[str, Any] | None:
        if reference is None:
            return None
        return {
            "index_code": NIFTY_REFERENCE_CODE,
            "close": _rounded(Decimal(str(reference.close)), 4),
            "market_date": reference.trade_date.isoformat(),
            "cutoff_date": cutoff.isoformat(),
            "source_name": reference.source_name,
            "source_reference": reference.source_reference,
            "freshness": reference.freshness_status,
            "timestamp_precision": "date",
        }


def _freshness_for_age(age_minutes: Decimal) -> str:
    if age_minutes < 0:
        return "missing"
    if age_minutes <= LIVE_MAX_AGE_MINUTES:
        return "live"
    if age_minutes <= RECENT_MAX_AGE_MINUTES:
        return "recent"
    return "stale"


def _clamp(
    value: Decimal,
    low: Decimal = Decimal("-100"),
    high: Decimal = Decimal("100"),
) -> Decimal:
    return max(low, min(high, value))


def _rounded(value: Decimal | None, places: int) -> float | None:
    if value is None:
        return None
    quantum = Decimal("1").scaleb(-places)
    return float(value.quantize(quantum, rounding=ROUND_HALF_UP))


def _iso(value: date | datetime | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)