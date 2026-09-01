from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.models.market_index_price import MarketIndexPrice
from app.repositories.market_index_price import MarketIndexPriceRepository

# ---------------------------------------------------------------------------
# Deterministic helpers (no look-ahead)
# ---------------------------------------------------------------------------


def _round(value: Decimal, places: int = 4) -> Decimal:
    """Round a Decimal using ROUND_HALF_UP (bankers rounding avoided)."""
    return value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)


def price_series_returns(
    prices: list[MarketIndexPrice], sessions: tuple[int, ...] = (1, 5, 20, 60)
) -> dict[int, Decimal | None]:
    """Compute deterministic session returns from an already bounded history."""
    closes = [Decimal(str(row.close)) for row in sorted(prices, key=lambda row: row.trade_date)]
    result: dict[int, Decimal | None] = {}
    for horizon in sessions:
        if horizon < 1:
            raise ValueError("return horizons must be positive")
        if len(closes) <= horizon or closes[-1 - horizon] == 0:
            result[horizon] = None
            continue
        result[horizon] = _round(
            (closes[-1] - closes[-1 - horizon]) / closes[-1 - horizon] * Decimal("100")
        )
    return result


def price_series_moving_averages(
    prices: list[MarketIndexPrice], windows: tuple[int, ...] = (20, 50, 100, 200)
) -> dict[str, Decimal | bool | None]:
    """Compute SMA position from an already bounded history using Decimal."""
    closes = [Decimal(str(row.close)) for row in sorted(prices, key=lambda row: row.trade_date)]
    result: dict[str, Decimal | bool | None] = {}
    for window in windows:
        if window < 1:
            raise ValueError("moving-average windows must be positive")
        if len(closes) < window:
            result[f"sma_{window}"] = None
            result[f"sma_{window}_above"] = None
            result[f"sma_{window}_distance_percent"] = None
            continue
        sma = sum(closes[-window:]) / Decimal(window)
        latest = closes[-1]
        result[f"sma_{window}"] = _round(sma)
        result[f"sma_{window}_above"] = latest > sma
        result[f"sma_{window}_distance_percent"] = (
            _round((latest - sma) / sma * Decimal("100")) if sma != 0 else None
        )
    return result


def price_series_range_position(
    prices: list[MarketIndexPrice], sessions: int = 20
) -> dict[str, float | None]:
    """Compute the latest close's position within the trailing N-session range.

    Used by sector intelligence for the 20-session high/low position. A flat
    window yields a neutral ``50`` position instead of a division by zero.
    """
    if sessions < 1:
        raise ValueError("sessions must be positive")
    closes = [
        Decimal(str(row.close))
        for row in sorted(prices, key=lambda row: row.trade_date)
    ]
    if len(closes) < sessions:
        return {
            "high": None,
            "low": None,
            "distance_from_high_percent": None,
            "distance_from_low_percent": None,
            "position_in_range_percent": None,
        }
    window = closes[-sessions:]
    highest = max(window)
    lowest = min(window)
    latest = closes[-1]
    if highest == lowest:
        position = Decimal("50")
    else:
        position = (latest - lowest) / (highest - lowest) * 100
    dist_high = None if highest == 0 else (latest - highest) / highest * 100
    dist_low = None if lowest == 0 else (latest - lowest) / lowest * 100
    return {
        "high": float(highest),
        "low": float(lowest),
        "distance_from_high_percent": (
            float(_round(-dist_high)) if dist_high is not None else None
        ),
        "distance_from_low_percent": (
            float(_round(dist_low)) if dist_low is not None else None
        ),
        "position_in_range_percent": float(_round(position)),
    }


def price_series_trend(
    prices: list[MarketIndexPrice], windows: tuple[int, int, int] = (20, 50, 200)
) -> str | None:
    """Classify the existing Task 18.2 trend from an already bounded history."""
    if not prices:
        return None
    if len(set(windows)) != 3 or any(window < 1 for window in windows):
        raise ValueError("trend windows must contain three distinct positive values")
    short_window, medium_window, long_window = windows
    mas = price_series_moving_averages(prices, windows)
    latest = Decimal(str(max(prices, key=lambda row: row.trade_date).close))
    short_sma = mas[f"sma_{short_window}"]
    medium_sma = mas[f"sma_{medium_window}"]
    long_sma = mas[f"sma_{long_window}"]
    if isinstance(long_sma, Decimal) and latest < long_sma:
        return "strong_bearish"
    if isinstance(long_sma, Decimal) and latest > long_sma:
        if (
            isinstance(short_sma, Decimal)
            and isinstance(medium_sma, Decimal)
            and short_sma > medium_sma
        ):
            return "strong_bullish"
        return "bullish"
    if not isinstance(short_sma, Decimal):
        return "neutral"
    if latest < short_sma:
        return "bearish"
    if latest > short_sma:
        return "bullish"
    return "neutral"


# ---------------------------------------------------------------------------
# Core calculation engine
# ---------------------------------------------------------------------------


class IndexPriceAction:
    """Deterministic Indian index price-action analysis.

    All features are calculated using *only* MarketIndexPrice rows with
    trade_date <= the requested date. No future data is ever used.
    """

    def __init__(self, index_code: str, trade_date: date) -> None:
        self.index_code = index_code
        self.trade_date = trade_date
        self._repo: MarketIndexPriceRepository | None = None

    # ------------------------------------------------------------------
    # Repository setup – injected by the caller so that tests can
    # provide a mock/stub without touching the real DB.
    # ------------------------------------------------------------------
    def set_repo(self, repo: MarketIndexPriceRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Internal: fetch price history bounded by trade_date (inclusive).
    # ------------------------------------------------------------------
    # A 52-week window is ~252 sessions; SMA200 needs 200 sessions. We
    # fetch a generous calendar lookback so all features can be computed
    # from data strictly on or before the requested date (no look-ahead).
    _LOOKBACK_DAYS = 400

    async def _fetch_history(self) -> list[MarketIndexPrice]:
        """Return price rows for this index with trade_date <= trade_date.

        The lookback window is bounded by ``self.trade_date`` (inclusive)
        so that no future data is ever used. The repository enforces the
        same bound at the SQL level.
        """
        if self._repo is None:
            raise RuntimeError("Price-action repo not set")
        start = self.trade_date - timedelta(days=self._LOOKBACK_DAYS)
        return await self._repo.get_date_range_by_index_code(
            self.index_code, start, self.trade_date
        )

    # ------------------------------------------------------------------
    # Public API – all methods are pure (no clock read inside calculation)
    # ------------------------------------------------------------------

    # -- A. BASIC PRICE ------------------------------------------------

    async def basic_price(self) -> dict[str, Any]:
        """Return open, high, low, close, previous_change, change_percent."""
        prices = await self._fetch_history()
        if not prices:
            return {
                "open": None,
                "high": None,
                "low": None,
                "close": None,
                "previous_close": None,
                "change_percent": None,
            }
        p = prices[0]  # the row for trade_date (latest <= date)
        change = Decimal(str(p.close)) - Decimal(str(p.previous_close or p.close))
        change_percent = (change / Decimal(str(p.previous_close or p.close))) * 100
        return {
            "open": float(p.open) if p.open is not None else None,
            "high": float(p.high) if p.high is not None else None,
            "low": float(p.low) if p.low is not None else None,
            "close": float(p.close) if p.close is not None else None,
            "previous_close": float(p.previous_close) if p.previous_close is not None else None,
            "change_percent": float(_round(change_percent)) if change_percent is not None else None,
        }

    # -- B. GAP --------------------------------------------------------

    async def gap(self) -> dict[str, Any]:
        """Return gap points, gap percent, and classification.

        gap_points = open - previous_close
        gap_percent = (open - previous_close) / previous_close * 100
        """
        prices = await self._fetch_history()
        if not prices or prices[0].previous_close is None:
            return {"gap_points": None, "gap_percent": None, "classification": "flat/no_gap"}
        p = prices[0]
        open_price = Decimal(str(p.open))
        prev_close = Decimal(str(p.previous_close))
        gap_points = open_price - prev_close
        if prev_close == 0:
            gap_percent = None
        else:
            gap_percent = (gap_points / prev_close) * 100
        # Classification
        if gap_points > 0:
            classification = "gap_up"
        elif gap_points < 0:
            classification = "gap_down"
        else:
            classification = "flat/no_gap"
        return {
            "gap_points": float(_round(gap_points)) if gap_points is not None else None,
            "gap_percent": float(_round(gap_percent)) if gap_percent is not None else None,
            "classification": classification,
        }

    # -- C. RETURNS --------------------------------------------------

    async def returns(self, sessions: tuple[int, ...] = (1, 5, 20, 60)) -> dict[int, Any]:
        """Return session return percentages for the requested horizons.

        A *session* is one trading day. Returns are computed as
        (close_t - close_{t-sessions}) / close_{t-sessions} * 100.
        Missing history for a horizon yields ``None`` for that horizon.

        Horizons are computed independently: a 1-session return is available
        with just 2 sessions even if 60-session data is missing.
        """
        result = price_series_returns(await self._fetch_history(), sessions)
        return {
            horizon: float(value) if value is not None else None
            for horizon, value in result.items()
        }

    # -- D. MOVING AVERAGES ----------------------------------------

    async def moving_averages(
        self, windows: tuple[int, ...] = (20, 50, 100, 200)
    ) -> dict[str, Any]:
        """Return SMA values and above/below + distance percent.

        Missing data for a window yields ``None`` for that window.
        """
        result = price_series_moving_averages(await self._fetch_history(), windows)
        return {
            key: float(value) if isinstance(value, Decimal) else value
            for key, value in result.items()
        }

    # -- E. TREND ----------------------------------------------------

    async def trend(
        self, windows: tuple[int, int, int] = (20, 50, 200)
    ) -> dict[str, str | None]:
        """Deterministic trend classification.

        States: strong_bullish, bullish, neutral, bearish, strong_bearish
        """
        history = await self._fetch_history()
        return {
            "trend": price_series_trend(history, windows) if history else None
        }

    # -- F. MOMENTUM -------------------------------------------------

    async def momentum(self, sessions: tuple[int, ...] = (5, 20, 60)) -> dict[int, Any]:
        """Return momentum return percentages for the requested session horizons."""
        return await self.returns(sessions=sessions)

    # -- G. 52-WEEK POSITION ---------------------------------------

    async def fifty_two_week_position(self) -> dict[str, Any]:
        """Return 52-week high, low, and derived positions.

        Uses up to ~252 trading sessions lookback.
        """
        prices = await self._fetch_history()
        if not prices or len(prices) < 252:
            return {
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
                "distance_from_52w_high_percent": None,
                "distance_from_52w_low_percent": None,
                "position_in_52w_range_percent": None,
            }

        prices_sorted = sorted(prices, key=lambda r: r.trade_date)
        close_series = [Decimal(str(p.close)) for p in prices_sorted]

        # Use last 252 sessions (or all if fewer)
        lookback = min(252, len(close_series))
        window = close_series[-lookback:]

        highest = max(window)
        lowest = min(window)
        latest = close_series[-1]

        dist_high = None if latest is None or highest == 0 else (latest - highest) / highest * 100
        dist_low = None if latest is None or lowest == 0 else (latest - lowest) / lowest * 100

        # Position in range: (latest - low) / (high - low) * 100
        if highest == lowest:
            position = Decimal("50") if latest is not None else Decimal("0")
        elif latest is not None:
            position = (latest - lowest) / (highest - lowest) * 100
        else:
            position = Decimal("0")

        return {
            "fifty_two_week_high": float(highest),
            "fifty_two_week_low": float(lowest),
            "distance_from_52w_high_percent": (
                float(_round(-dist_high)) if dist_high is not None else None
            ),
            "distance_from_52w_low_percent": (
                float(_round(dist_low)) if dist_low is not None else None
            ),
            "position_in_52w_range_percent": (
                float(_round(position)) if position is not None else None
            ),
        }

    # -- H. RANGE / VOLATILITY CONTEXT -----------------------------

    async def daily_range(self) -> dict[str, Any]:
        """Return today's day range percent.

        day_range_percent = (high - low) / open * 100
        """
        prices = await self._fetch_history()
        if not prices or prices[0].open is None or prices[0].high is None or prices[0].low is None:
            return {"day_range_percent": None, "high": None, "low": None, "open": None}
        p = prices[0]
        high = Decimal(str(p.high))
        low = Decimal(str(p.low))
        open_p = Decimal(str(p.open))
        if open_p == 0:
            return {
                "day_range_percent": None,
                "high": float(high),
                "low": float(low),
                "open": float(open_p),
            }
        drp = (high - low) / open_p * 100
        return {
            "day_range_percent": float(_round(drp)),
            "high": float(high),
            "low": float(low),
            "open": float(open_p),
        }

    async def twenty_session_avg_daily_move(self) -> float | None:
        """Return 20-session average of absolute daily moves.

        |close_t - close_{t-1}| averaged over the last 20 sessions.
        """
        prices = await self._fetch_history()
        if not prices or len(prices) < 21:
            return None
        prices_sorted = sorted(prices, key=lambda r: r.trade_date)
        close_series = [Decimal(str(p.close)) for p in prices_sorted]
        moves = []
        for i in range(1, len(close_series)):
            move = abs(close_series[i] - close_series[i - 1])
            moves.append(move)
        if len(moves) < 20:
            return None
        avg = sum(moves[-20:]) / 20
        return float(_round(avg))

    # -- I. PRICE-ACTION SCORE -------------------------------------

    async def price_action_score(self,
                                  weights: dict[str, float] | None = None) -> dict[str, Any]:
        """Deterministic price-action score in the range -100 .. +100.

        The score is a weighted composite of component signals.
        Components that cannot be computed (missing data) default to 0.

        Centralised default weights (all sum to 1.0 implicitly via
        normalisation; callers may override).

        Possible components (only those with data contribute):
          - trend        : +30 .. +30
          - momentum     : +20 .. +20
          - moving_average_position: +20 .. +20
          - fifty_two_week_position: +15 .. +15 (positive = near high, negative = near low)
          - gap          : +15 .. +15 (gap_up positive, gap_down negative)
        """
        prices = await self._fetch_history()
        if not prices:
            return {
                "price_action_score": None,
                "score_breakdown": {}
            }

        if weights is None:
            # Default weights that sum to 100 when all components contribute.
            weights = {
                "trend": 30.0,
                "momentum": 20.0,
                "moving_average_position": 20.0,
                "fifty_two_week_position": 15.0,
                "gap": 15.0,
            }

        score = Decimal("0")

        # 1. Trend component (max contribution = weights["trend"])
        trend_result = await self.trend()
        trend_str = trend_result.get("trend", "neutral")
        trend_weight = Decimal(str(weights["trend"]))
        trend_map = {
            "strong_bullish": trend_weight,
            "bullish": trend_weight * Decimal("2") / Decimal("3"),
            "neutral": Decimal("0"),
            "bearish": -trend_weight * Decimal("2") / Decimal("3"),
            "strong_bearish": -trend_weight,
        }
        score += trend_map.get(trend_str, Decimal("0"))

        # 2. Momentum component (max contribution = weights["momentum"])
        mom = await self.momentum()
        # Use 5-session momentum as proxy; sign determines direction
        mom5 = mom.get(5)
        if mom5 is not None:
            if mom5 > 0:
                score += Decimal(str(weights["momentum"]))
            elif mom5 < 0:
                score += Decimal(str(-weights["momentum"]))
            # else: neutral, add 0

        # 3. Moving-average position component (max = weights["moving_average_position"])
        mas = await self.moving_averages()
        sma20 = mas.get("sma_20")
        bp_close = (await self.basic_price()).get("close")
        latest_close_dec = (
            Decimal(str(bp_close)) if bp_close is not None else None
        )
        if sma20 is not None and latest_close_dec is not None:
            if latest_close_dec > sma20:
                score += Decimal(str(weights["moving_average_position"]))
            elif latest_close_dec < sma20:
                score += Decimal(str(-weights["moving_average_position"]))

        # 4. 52-week position component (max = weights["fifty_two_week_position"])
        fw = await self.fifty_two_week_position()
        pos = fw.get("position_in_52w_range_percent")
        if pos is not None:
            # Normalise to -1..+1: centre at 50, range width 50
            score += Decimal(
                str(round((pos - 50) / 50 * weights["fifty_two_week_position"], 1))
            )

        # 5. Gap component (max = weights["gap"])
        gap = await self.gap()
        gap_class = gap.get("classification")
        if gap_class == "gap_up":
            score += Decimal(str(weights["gap"]))
        elif gap_class == "gap_down":
            score += Decimal(str(-weights["gap"]))
        # else: flat, add 0

        # Clamp to -100 .. +100
        score = max(Decimal("-100"), min(Decimal("100"), score))

        return {
            "price_action_score": int(score),
            "score_breakdown": {
                "trend": trend_str,
                "momentum": float(mom5) if mom5 is not None else None,
                "moving_average_position": (
                    "above"
                    if latest_close_dec is not None
                    and sma20 is not None
                    and latest_close_dec > sma20
                    else "below"
                    if latest_close_dec is not None
                    and sma20 is not None
                    and latest_close_dec < sma20
                    else "neutral"
                ),
                "fifty_two_week_position": float(pos) if pos is not None else None,
                "gap": gap_class,
            },
        }