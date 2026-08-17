from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intelligence.breadth_config import (
    BREADTH_BENCHMARK_CODE,
    BREADTH_CALCULATION_VERSION,
    BREADTH_DIRECTION_THRESHOLDS,
    BREADTH_HISTORY_LOOKBACK_CALENDAR_DAYS,
    BREADTH_MARKET_CLOSE_TIME,
    BREADTH_SCORE_WEIGHTS,
    BREADTH_STATE_THRESHOLDS,
    COMPLETENESS_WEIGHTS,
    CONFIDENCE_WEIGHTS,
    CURRENT_MEMBERSHIP_UNIVERSE_QUALITY,
    DIVERGENCE_COMPONENT_SCORES,
    FRESHNESS_QUALITY,
    HIGH_LOW_BALANCE_WEIGHTS,
    HIGH_LOW_WINDOWS,
    MOMENTUM_CHANGE_SCALES,
    MOMENTUM_DIRECTION_THRESHOLD,
    MOMENTUM_SESSION_WINDOWS,
    MOMENTUM_WEIGHTS,
    PARTICIPATION_WEIGHTS,
    QUALITY_MAX,
    QUALITY_MIN,
    SCORE_MAX,
    SCORE_MIN,
    SMA_WINDOWS,
)
from app.market_intelligence.breadth_universe import (
    BREADTH_UNIVERSES,
    DEFAULT_BREADTH_UNIVERSE,
    BreadthUniverse,
)
from app.market_intelligence.session_dates import INDIA_TIMEZONE
from app.repositories.breadth import BreadthRepository
from app.repositories.market_index_price import MarketIndexPriceRepository
from app.services.index_price_action import price_series_moving_averages, price_series_returns

DIVERGENCE_SIGNALS = DIVERGENCE_COMPONENT_SCORES


def _clamp(value: Decimal | None, low: Decimal = SCORE_MIN, high: Decimal = SCORE_MAX):
    return None if value is None else max(low, min(high, value))


def _round(value: Decimal, places: int = 2) -> float:
    return float(value.quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP))


def _pct(numerator: int, denominator: int) -> Decimal | None:
    return Decimal(numerator) / Decimal(denominator) * 100 if denominator > 0 else None


def _signal(value: Decimal | None) -> Decimal | None:
    return _clamp((value - 50) * 2) if value is not None else None


def breadth_state(score: Decimal | None) -> str | None:
    if score is None:
        return None
    if score >= BREADTH_STATE_THRESHOLDS["STRONG"]:
        return "STRONG"
    if score >= BREADTH_STATE_THRESHOLDS["POSITIVE"]:
        return "POSITIVE"
    if score > BREADTH_STATE_THRESHOLDS["WEAK"]:
        return "NEUTRAL"
    if score > BREADTH_STATE_THRESHOLDS["VERY_WEAK"]:
        return "WEAK"
    return "VERY_WEAK"


def breadth_direction(score: Decimal | None) -> str | None:
    if score is None:
        return None
    if score >= BREADTH_DIRECTION_THRESHOLDS["strong_bullish"]:
        return "strong_bullish"
    if score >= BREADTH_DIRECTION_THRESHOLDS["bullish"]:
        return "bullish"
    if score > BREADTH_DIRECTION_THRESHOLDS["bearish"]:
        return "neutral"
    if score > BREADTH_DIRECTION_THRESHOLDS["strong_bearish"]:
        return "bearish"
    return "strong_bearish"


class MarketBreadth:
    """Deterministic breadth from one bounded stock load and one benchmark load."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        breadth_repo: Any | None = None,
        index_price_repo: Any | None = None,
        universe: BreadthUniverse = DEFAULT_BREADTH_UNIVERSE,
    ) -> None:
        self._breadth_repo = breadth_repo or BreadthRepository(session)
        self._index_repo = index_price_repo or MarketIndexPriceRepository(session)
        self._universe = universe

    async def evaluate(
        self, market_date: date, *, evaluation_time: datetime | None = None
    ) -> dict[str, Any]:
        evaluated_at = self._evaluation_time(market_date, evaluation_time)
        cutoff = self._cutoff(market_date, evaluated_at)
        start = cutoff - timedelta(days=BREADTH_HISTORY_LOOKBACK_CALENDAR_DAYS)
        stocks = await self._breadth_repo.list_universe_stocks(self._universe)
        rows = await self._breadth_repo.load_daily_prices(
            [stock.id for stock in stocks], start, cutoff
        )
        benchmark = await self._benchmark(start, cutoff)
        histories = self._group(rows)
        panel = self._panel(histories)
        result = self._empty(market_date, evaluated_at, cutoff, start, len(stocks))
        stock_as_of = max((row.trade_date for row in rows), default=None)
        benchmark_as_of = max((row.trade_date for row in benchmark), default=None)
        freshness = self._freshness(market_date, stock_as_of, benchmark_as_of)
        result.update(
            data_as_of=stock_as_of.isoformat() if stock_as_of else None,
            freshness=freshness["overall"],
            source_freshness=freshness,
        )
        result["provenance"].update(
            stock_data_as_of=result["data_as_of"],
            benchmark_data_as_of=benchmark_as_of.isoformat() if benchmark_as_of else None,
        )
        counts = panel.get(market_date)
        if counts is None:
            self._aliases(result)
            return result

        classified = counts["advancing"] + counts["declining"] + counts["unchanged"]
        advancing_pct = _pct(counts["advancing"], classified)
        declining_pct = _pct(counts["declining"], classified)
        unchanged_pct = _pct(counts["unchanged"], classified)
        ratio, ratio_state = self._ratio(counts["advancing"], counts["declining"])
        net = counts["advancing"] - counts["declining"]
        ad_line, anchor = self._ad_line(panel, market_date)
        sma = self._sma(histories, market_date)
        high_low = self._high_low(histories, market_date)
        high_low_balance, high_low_weights = self._high_low_balance(high_low)
        participation_inputs = {
            "advancing_percent": advancing_pct,
            "percent_above_sma20": sma[20]["percent_above"],
            "percent_above_sma50": sma[50]["percent_above"],
            "percent_above_sma200": sma[200]["percent_above"],
            "new_high_low_balance": high_low_balance,
        }
        participation, participation_weights = self._weighted(
            participation_inputs, PARTICIPATION_WEIGHTS, QUALITY_MIN, QUALITY_MAX
        )
        momentum, momentum_direction, momentum_parts = self._momentum(histories, panel, market_date)
        divergence = self._divergence(momentum_direction, benchmark, market_date)
        components = {
            "advance_decline": _clamp(
                Decimal(net) / Decimal(classified) * 100 if classified else None
            ),
            "sma20_participation": _signal(sma[20]["percent_above"]),
            "sma50_participation": _signal(sma[50]["percent_above"]),
            "sma200_participation": _signal(sma[200]["percent_above"]),
            "new_high_low": _signal(high_low_balance),
            "momentum": momentum,
            "divergence": DIVERGENCE_COMPONENT_SCORES.get(divergence["signal"]),
        }
        score, score_weights = self._weighted(components, BREADTH_SCORE_WEIGHTS)
        completeness_parts = self._completeness_parts(
            len(stocks),
            counts["current_close"],
            classified,
            sma,
            high_low,
            divergence["benchmark_return_percent"] is not None,
        )
        completeness, _ = self._weighted(
            completeness_parts, COMPLETENESS_WEIGHTS, QUALITY_MIN, QUALITY_MAX
        )
        completeness = completeness or Decimal(0)
        confidence, confidence_parts = self._confidence(
            completeness, result["freshness"], score, components
        )
        result.update(
            current_close_count=counts["current_close"],
            classified_count=classified,
            missing_count=len(stocks) - classified,
            missing_current_close_count=len(stocks) - counts["current_close"],
            missing_previous_close_count=counts["missing_previous"],
            advancing_count=counts["advancing"],
            declining_count=counts["declining"],
            unchanged_count=counts["unchanged"],
            advancing_percent=self._optional(advancing_pct),
            declining_percent=self._optional(declining_pct),
            unchanged_percent=self._optional(unchanged_pct),
            advance_decline_ratio=self._optional(ratio, 4),
            advance_decline_ratio_state=ratio_state,
            advance_decline_difference=net,
            net_advances=net,
            advance_decline_line=ad_line,
            advance_decline_line_anchor_date=anchor.isoformat() if anchor else None,
            sma_breadth=self._public_sma(sma),
            high_low_breadth=self._public_hl(high_low),
            participation_score=self._optional(participation),
            participation_breakdown={
                "components": self._public(participation_inputs),
                "normalized_weights": participation_weights,
            },
            breadth_momentum_score=self._optional(momentum),
            breadth_momentum_direction=momentum_direction,
            momentum_breakdown=self._public(momentum_parts),
            divergence=divergence,
            breadth_score=self._optional(score),
            breadth_state=breadth_state(score),
            breadth_direction=breadth_direction(score),
            score_breakdown={
                "components": self._public(components),
                "normalized_weights": score_weights,
                "configured_weights": self._public(BREADTH_SCORE_WEIGHTS, 4),
                "high_low_normalized_weights": high_low_weights,
            },
            completeness=_round(completeness),
            completeness_breakdown=self._public(completeness_parts),
            confidence=_round(confidence),
            confidence_breakdown=confidence_parts,
        )
        self._aliases(result)
        return result

    async def _benchmark(self, start: date, end: date) -> list[Any]:
        bulk = getattr(self._index_repo, "get_ranges_by_index_codes", None)
        if bulk:
            return await bulk((BREADTH_BENCHMARK_CODE,), start, end)
        return await self._index_repo.get_date_range_by_index_code(
            BREADTH_BENCHMARK_CODE, start, end
        )

    @staticmethod
    def _evaluation_time(market_date: date, value: datetime | None) -> datetime:
        if value is None:
            return datetime.combine(
                market_date, BREADTH_MARKET_CLOSE_TIME, tzinfo=ZoneInfo(INDIA_TIMEZONE)
            )
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluation_time must be timezone-aware")
        return value

    @staticmethod
    def _cutoff(requested: date, evaluated_at: datetime) -> date:
        local = evaluated_at.astimezone(ZoneInfo(INDIA_TIMEZONE))
        completed = local.date()
        if local.timetz().replace(tzinfo=None) < BREADTH_MARKET_CLOSE_TIME:
            completed -= timedelta(days=1)
        return min(requested, completed)

    def _empty(self, market_date, evaluated_at, cutoff, start, size) -> dict[str, Any]:
        sma = {
            str(w): {"stocks_above": None, "eligible_count": 0, "percent_above": None}
            for w in SMA_WINDOWS
        }
        hl = {
            label: {
                "new_highs": None,
                "new_lows": None,
                "eligible_count": 0,
                "new_highs_percent": None,
                "new_lows_percent": None,
                "balance_percent": None,
            }
            for label in HIGH_LOW_WINDOWS
        }
        return {
            "market_date": market_date.isoformat(),
            "evaluation_time": evaluated_at.isoformat(),
            "completed_market_cutoff": cutoff.isoformat(),
            "calculation_version": BREADTH_CALCULATION_VERSION,
            "universe": {
                "code": self._universe.code,
                "name": self._universe.name,
                "exchange": self._universe.exchange,
                "series": list(self._universe.series),
                "expected_size": size,
                "methodology": self._universe.methodology,
                "point_in_time_membership": False,
                "quality_score": _round(CURRENT_MEMBERSHIP_UNIVERSE_QUALITY),
            },
            "eligible_count": size,
            "current_close_count": 0,
            "classified_count": 0,
            "missing_count": size,
            "missing_current_close_count": size,
            "missing_previous_close_count": 0,
            "advancing_count": None,
            "declining_count": None,
            "unchanged_count": None,
            "advancing_percent": None,
            "declining_percent": None,
            "unchanged_percent": None,
            "advance_decline_ratio": None,
            "advance_decline_ratio_state": "missing",
            "advance_decline_difference": None,
            "net_advances": None,
            "advance_decline_line": None,
            "advance_decline_line_anchor_date": None,
            "sma_breadth": sma,
            "high_low_breadth": hl,
            "participation_score": None,
            "participation_breakdown": {"components": {}, "normalized_weights": {}},
            "breadth_momentum_score": None,
            "breadth_momentum_direction": None,
            "momentum_breakdown": {},
            "divergence": {
                "benchmark_code": BREADTH_BENCHMARK_CODE,
                "benchmark_return_percent": None,
                "breadth_momentum_direction": None,
                "signal": None,
            },
            "breadth_score": None,
            "breadth_state": None,
            "breadth_direction": None,
            "score_breakdown": {
                "components": {},
                "normalized_weights": {},
                "configured_weights": self._public(BREADTH_SCORE_WEIGHTS, 4),
                "high_low_normalized_weights": {},
            },
            "completeness": 0.0,
            "completeness_breakdown": {},
            "confidence": 0.0,
            "confidence_breakdown": {},
            "data_as_of": None,
            "freshness": "missing",
            "source_freshness": {
                "daily_prices": {"data_as_of": None, "status": "missing"},
                "benchmark": {"data_as_of": None, "status": "missing"},
                "overall": "missing",
            },
            "provenance": {
                "stock_source": "daily_prices",
                "benchmark_source": "market_index_prices",
                "benchmark_code": BREADTH_BENCHMARK_CODE,
                "query_start_date": start.isoformat(),
                "query_end_date": cutoff.isoformat(),
                "lookback_calendar_days": BREADTH_HISTORY_LOOKBACK_CALENDAR_DAYS,
                "stock_history_query_count": 1,
                "benchmark_history_query_count": 1,
                "ad_line_convention": (
                    "zero-anchored cumulative net advances from the first "
                    "classified session in the bounded query window"
                ),
                "high_low_convention": (
                    "strict breakout of the prior 20/252 stored sessions, excluding today's close"
                ),
                "ratio_convention": (
                    "None plus infinite_positive when advances exist and "
                    "declines are zero; undefined when both are zero"
                ),
                "stock_data_as_of": None,
                "benchmark_data_as_of": None,
            },
        }

    @staticmethod
    def _group(rows):
        grouped = defaultdict(list)
        for row in rows:
            grouped[row.stock_id].append(row)
        for history in grouped.values():
            history.sort(key=lambda row: row.trade_date)
        return dict(grouped)

    @staticmethod
    def _panel(histories):
        panel = defaultdict(
            lambda: {
                "current_close": 0,
                "advancing": 0,
                "declining": 0,
                "unchanged": 0,
                "missing_previous": 0,
            }
        )
        for history in histories.values():
            for index, row in enumerate(history):
                panel[row.trade_date]["current_close"] += 1
                if index == 0:
                    panel[row.trade_date]["missing_previous"] += 1
                elif row.close > history[index - 1].close:
                    panel[row.trade_date]["advancing"] += 1
                elif row.close < history[index - 1].close:
                    panel[row.trade_date]["declining"] += 1
                else:
                    panel[row.trade_date]["unchanged"] += 1
        return dict(sorted(panel.items()))

    @staticmethod
    def _ratio(advancing, declining):
        if advancing == declining == 0:
            return None, "undefined"
        if declining == 0:
            return None, "infinite_positive"
        return Decimal(advancing) / Decimal(declining), "finite"

    @staticmethod
    def _ad_line(panel, market_date):
        sessions = [
            d
            for d, c in panel.items()
            if d <= market_date and c["advancing"] + c["declining"] + c["unchanged"] > 0
        ]
        if market_date not in panel or not sessions:
            return None, None
        return sum(panel[d]["advancing"] - panel[d]["declining"] for d in sessions), sessions[0]

    @staticmethod
    def _through(history, ref):
        return [row for row in history if row.trade_date <= ref]

    def _sma(self, histories, ref):
        out = {
            w: {"stocks_above": 0, "eligible_count": 0, "percent_above": None} for w in SMA_WINDOWS
        }
        for history in histories.values():
            bounded = self._through(history, ref)
            if not bounded or bounded[-1].trade_date != ref:
                continue
            values = price_series_moving_averages(bounded, SMA_WINDOWS)
            for window in SMA_WINDOWS:
                marker = values[f"sma_{window}_above"]
                if marker is not None:
                    out[window]["eligible_count"] += 1
                    out[window]["stocks_above"] += int(marker)
        for values in out.values():
            values["percent_above"] = _pct(values["stocks_above"], values["eligible_count"])
        return out

    def _high_low(self, histories, ref):
        out = {
            label: {
                "new_highs": 0,
                "new_lows": 0,
                "eligible_count": 0,
                "new_highs_percent": None,
                "new_lows_percent": None,
                "balance_percent": None,
            }
            for label in HIGH_LOW_WINDOWS
        }
        for history in histories.values():
            bounded = self._through(history, ref)
            if not bounded or bounded[-1].trade_date != ref:
                continue
            latest = Decimal(str(bounded[-1].close))
            prior = [Decimal(str(row.close)) for row in bounded[:-1]]
            for label, window in HIGH_LOW_WINDOWS.items():
                if len(prior) >= window:
                    sample = prior[-window:]
                    out[label]["eligible_count"] += 1
                    out[label]["new_highs"] += int(latest > max(sample))
                    out[label]["new_lows"] += int(latest < min(sample))
        for values in out.values():
            eligible = values["eligible_count"]
            if not eligible:
                values["new_highs"] = values["new_lows"] = None
                continue
            values["new_highs_percent"] = _pct(values["new_highs"], eligible)
            values["new_lows_percent"] = _pct(values["new_lows"], eligible)
            values["balance_percent"] = _clamp(
                Decimal(50)
                + Decimal(values["new_highs"] - values["new_lows"]) / Decimal(eligible) * 50,
                QUALITY_MIN,
                QUALITY_MAX,
            )
        return out

    def _high_low_balance(self, values):
        return self._weighted(
            {k: v["balance_percent"] for k, v in values.items()},
            HIGH_LOW_BALANCE_WEIGHTS,
            QUALITY_MIN,
            QUALITY_MAX,
        )

    def _momentum(self, histories, panel, market_date):
        dates = [d for d in panel if d <= market_date]
        if market_date not in dates:
            return None, None, {}
        position = dates.index(market_date)

        def prior(n):
            return dates[position - n] if position >= n else None

        def adv(ref):
            if ref is None:
                return None
            c = panel[ref]
            return _pct(c["advancing"], c["advancing"] + c["declining"] + c["unchanged"])

        current_adv = adv(market_date)
        parts = {}
        for horizon in MOMENTUM_SESSION_WINDOWS:
            key = f"advancing_percent_change_{horizon}"
            parts[key] = self._change(current_adv, adv(prior(horizon)), MOMENTUM_CHANGE_SCALES[key])
        parts["ad_line_impulse_5"] = self._impulse(panel, dates, position, 5)
        parts["ad_line_impulse_20"] = self._impulse(panel, dates, position, 20)
        current_sma = self._sma(histories, market_date)
        sma5 = self._sma(histories, prior(5)) if prior(5) else None
        sma20 = self._sma(histories, prior(20)) if prior(20) else None
        parts["sma20_change_5"] = self._change(
            current_sma[20]["percent_above"],
            sma5[20]["percent_above"] if sma5 else None,
            MOMENTUM_CHANGE_SCALES["sma20_change_5"],
        )
        parts["sma50_change_20"] = self._change(
            current_sma[50]["percent_above"],
            sma20[50]["percent_above"] if sma20 else None,
            MOMENTUM_CHANGE_SCALES["sma50_change_20"],
        )
        current_hl, _ = self._high_low_balance(self._high_low(histories, market_date))
        ref5 = prior(5)
        prior_hl = self._high_low_balance(self._high_low(histories, ref5))[0] if ref5 else None
        parts["high_low_change_5"] = self._change(
            current_hl, prior_hl, MOMENTUM_CHANGE_SCALES["high_low_change_5"]
        )
        score, _ = self._weighted(parts, MOMENTUM_WEIGHTS)
        direction = (
            None
            if score is None
            else "improving"
            if score >= MOMENTUM_DIRECTION_THRESHOLD
            else "deteriorating"
            if score <= -MOMENTUM_DIRECTION_THRESHOLD
            else "stable"
        )
        return score, direction, parts

    @staticmethod
    def _change(current, previous, scale):
        return (
            _clamp((current - previous) * scale)
            if (isinstance(current, Decimal) and isinstance(previous, Decimal))
            else None
        )

    @staticmethod
    def _impulse(panel, dates, position, horizon):
        if position < horizon:
            return None
        selected = dates[position - horizon + 1 : position + 1]
        net = sum(panel[d]["advancing"] - panel[d]["declining"] for d in selected)
        total = sum(
            panel[d]["advancing"] + panel[d]["declining"] + panel[d]["unchanged"] for d in selected
        )
        return _clamp(Decimal(net) / Decimal(total) * 100) if total else None

    @staticmethod
    def _divergence(momentum_direction, benchmark, market_date):
        result = {
            "benchmark_code": BREADTH_BENCHMARK_CODE,
            "benchmark_return_percent": None,
            "breadth_momentum_direction": momentum_direction,
            "signal": None,
        }
        rows = sorted(
            (row for row in benchmark if row.trade_date <= market_date),
            key=lambda row: row.trade_date,
        )
        if len(rows) < 2 or rows[-1].trade_date != market_date or momentum_direction is None:
            return result
        index_return = price_series_returns(rows, (1,))[1]
        if index_return is None:
            return result
        result["benchmark_return_percent"] = _round(index_return, 4)
        if index_return > 0 and momentum_direction == "improving":
            signal = "bullish_confirmation"
        elif index_return > 0 and momentum_direction == "deteriorating":
            signal = "negative_divergence"
        elif index_return < 0 and momentum_direction == "deteriorating":
            signal = "bearish_confirmation"
        elif index_return < 0 and momentum_direction == "improving":
            signal = "positive_divergence"
        else:
            signal = "neutral"
        result["signal"] = signal
        return result

    @staticmethod
    def _freshness(market_date, stock_date, benchmark_date):
        def status(value):
            if value is None:
                return "missing"
            return "recent" if value == market_date else "stale"

        stock, benchmark = status(stock_date), status(benchmark_date)
        if stock == "missing":
            overall = "missing"
        elif "stale" in (stock, benchmark):
            overall = "stale"
        else:
            overall = "recent"
        return {
            "daily_prices": {
                "data_as_of": stock_date.isoformat() if stock_date else None,
                "status": stock,
            },
            "benchmark": {
                "data_as_of": benchmark_date.isoformat() if benchmark_date else None,
                "status": benchmark,
            },
            "overall": overall,
        }

    @staticmethod
    def _completeness_parts(size, current, classified, sma, hl, benchmark):
        if not size:
            return {key: Decimal(0) for key in COMPLETENESS_WEIGHTS}

        def coverage(count):
            return Decimal(count) / Decimal(size) * 100

        return {
            "current_close_coverage": coverage(current),
            "classified_coverage": coverage(classified),
            "sma20_coverage": coverage(sma[20]["eligible_count"]),
            "sma50_coverage": coverage(sma[50]["eligible_count"]),
            "sma200_coverage": coverage(sma[200]["eligible_count"]),
            "high_low_20d_coverage": coverage(hl["20d"]["eligible_count"]),
            "high_low_52w_coverage": coverage(hl["52w"]["eligible_count"]),
            "benchmark_coverage": Decimal(100 if benchmark else 0),
        }

    def _confidence(self, completeness, freshness, score, components):
        available = [value for value in components.values() if value is not None]
        if score is None or not available:
            return Decimal(0), {}
        agreement = _clamp(
            100 - sum(abs(value - score) for value in available) / Decimal(len(available)) / 2,
            QUALITY_MIN,
            QUALITY_MAX,
        )
        inputs = {
            "completeness": completeness,
            "freshness": FRESHNESS_QUALITY.get(freshness, Decimal(0)),
            "universe_quality": CURRENT_MEMBERSHIP_UNIVERSE_QUALITY,
            "signal_agreement": agreement,
        }
        value, _ = self._weighted(inputs, CONFIDENCE_WEIGHTS, QUALITY_MIN, QUALITY_MAX)
        return value or Decimal(0), self._public(inputs)

    @staticmethod
    def _weighted(values, weights, low=SCORE_MIN, high=SCORE_MAX):
        available = {
            key: value for key, value in values.items() if value is not None and key in weights
        }
        if not available:
            return None, {}
        total = sum(weights[key] for key in available)
        normalized = {key: weights[key] / total for key in available}
        value = sum(available[key] * normalized[key] for key in available)
        return _clamp(value, low, high), {
            key: _round(weight, 4) for key, weight in normalized.items()
        }

    @staticmethod
    def _optional(value, places=2):
        return _round(value, places) if isinstance(value, Decimal) else None

    @classmethod
    def _public(cls, values, places=2):
        return {key: cls._optional(value, places) for key, value in values.items()}

    @classmethod
    def _public_sma(cls, values):
        return {
            str(k): {
                "stocks_above": v["stocks_above"],
                "eligible_count": v["eligible_count"],
                "percent_above": cls._optional(v["percent_above"]),
            }
            for k, v in values.items()
        }

    @classmethod
    def _public_hl(cls, values):
        return {
            label: {
                key: (cls._optional(value) if isinstance(value, Decimal) else value)
                for key, value in data.items()
            }
            for label, data in values.items()
        }

    @staticmethod
    def _aliases(result):
        result.update(
            advancing=result["advancing_count"],
            declining=result["declining_count"],
            unchanged=result["unchanged_count"],
            unclassified=result["missing_previous_close_count"],
            eligible=result["eligible_count"],
            participation=result["participation_score"],
            momentum=result["breadth_momentum_score"],
            score=result["breadth_score"],
            direction=result["breadth_direction"],
        )
        result["moving_average_participation"] = {
            f"sma{w}_participation": result["sma_breadth"][str(w)]["percent_above"]
            for w in SMA_WINDOWS
        }
        result["new_highs"] = {
            "new_20d_highs": result["high_low_breadth"]["20d"]["new_highs"],
            "new_52w_highs": result["high_low_breadth"]["52w"]["new_highs"],
            "high_low_percent": result["high_low_breadth"]["20d"]["balance_percent"],
        }
        result["new_lows"] = {
            "new_20d_lows": result["high_low_breadth"]["20d"]["new_lows"],
            "new_52w_lows": result["high_low_breadth"]["52w"]["new_lows"],
        }


def supported_universes() -> list[dict[str, Any]]:
    return [
        {
            "code": value.code,
            "name": value.name,
            "exchange": value.exchange,
            "series": list(value.series),
            "category": value.category,
            "methodology": value.methodology,
        }
        for value in BREADTH_UNIVERSES
    ]
