from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.scan_result import ScanResult
from app.models.stock import Stock
from app.repositories.scan import ScanDefinitionRepository


@dataclass
class IndicatorRow:
    stock_id: int
    symbol: str
    trade_date: date
    close: float
    ema_20: float | None
    ema_50: float | None
    ema_100: float | None
    ema_200: float | None
    sma: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    stochastic_k: float | None
    stochastic_d: float | None
    atr: float | None
    bollinger_upper: float | None
    bollinger_middle: float | None
    bollinger_lower: float | None
    adx: float | None
    supertrend: float | None
    supertrend_direction: int | None
    vwap: float | None
    obv: float | None
    volume: int | None = None
    delivery_percentage: float | None = None
    high: float | None = None
    avg_volume_20d: float | None = None
    max_volume_20d: float | None = None
    avg_delivery_20d: float | None = None
    max_delivery_20d: float | None = None
    max_high_20d: float | None = None


@dataclass
class ScanResultDTO:
    scan_definition_id: int
    stock_id: int
    scan_date: date
    score: float
    metrics: dict[str, Any]


ScannerFunc = Callable[[IndicatorRow, IndicatorRow | None], tuple[bool, float, dict[str, Any]]]


class ScannerRegistry:
    scanners: dict[str, ScannerFunc] = {}

    @classmethod
    def register(cls, code: str) -> Callable[[ScannerFunc], ScannerFunc]:
        def decorator(func: ScannerFunc) -> ScannerFunc:
            cls.scanners[code] = func
            return func

        return decorator

    @classmethod
    def get(cls, code: str) -> ScannerFunc | None:
        return cls.scanners.get(code)

    @classmethod
    def all(cls) -> dict[str, ScannerFunc]:
        return dict(cls.scanners)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _compute_20d_aggregates(rows: list[Any]) -> dict[str, Any]:
    volumes: list[int] = []
    deliveries: list[float] = []
    highs: list[float] = []
    for row in rows:
        if getattr(row, "volume", None) is not None:
            volumes.append(int(row.volume))
        if getattr(row, "delivery_percentage", None) is not None:
            deliveries.append(float(row.delivery_percentage))
        if getattr(row, "high", None) is not None:
            highs.append(float(row.high))
    return {
        "avg_volume_20d": sum(volumes) / len(volumes) if volumes else None,
        "max_volume_20d": max(volumes) if volumes else None,
        "avg_delivery_20d": sum(deliveries) / len(deliveries) if deliveries else None,
        "max_delivery_20d": max(deliveries) if deliveries else None,
        "max_high_20d": max(highs) if highs else None,
    }


@ScannerRegistry.register("ema_bullish_crossover")
def ema_bullish_crossover(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(
        current.ema_20
        and current.ema_50
        and previous.ema_20
        and previous.ema_50
        and current.ema_20 > current.ema_50
        and previous.ema_20 < previous.ema_50
    )
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_20": current.ema_20, "ema_50": current.ema_50}


@ScannerRegistry.register("ema_bearish_crossover")
def ema_bearish_crossover(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(
        current.ema_20
        and current.ema_50
        and previous.ema_20
        and previous.ema_50
        and current.ema_20 < current.ema_50
        and previous.ema_20 > previous.ema_50
    )
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_20": current.ema_20, "ema_50": current.ema_50}


@ScannerRegistry.register("golden_cross")
def golden_cross(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(
        current.ema_50
        and current.ema_200
        and previous.ema_50
        and previous.ema_200
        and current.ema_50 > current.ema_200
        and previous.ema_50 < previous.ema_200
    )
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_50": current.ema_50, "ema_200": current.ema_200}


@ScannerRegistry.register("death_cross")
def death_cross(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(
        current.ema_50
        and current.ema_200
        and previous.ema_50
        and previous.ema_200
        and current.ema_50 < current.ema_200
        and previous.ema_50 > previous.ema_200
    )
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_50": current.ema_50, "ema_200": current.ema_200}


@ScannerRegistry.register("price_above_ema20")
def price_above_ema20(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = bool(current.ema_20 and current.close > float(current.ema_20))
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "ema_20": current.ema_20}


@ScannerRegistry.register("price_below_ema20")
def price_below_ema20(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = bool(current.ema_20 and current.close < float(current.ema_20))
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "ema_20": current.ema_20}


@ScannerRegistry.register("supertrend_bullish")
def supertrend_bullish(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.supertrend_direction == 1
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {"supertrend": current.supertrend, "direction": current.supertrend_direction},
    )


@ScannerRegistry.register("supertrend_bearish")
def supertrend_bearish(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.supertrend_direction == -1
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {"supertrend": current.supertrend, "direction": current.supertrend_direction},
    )


@ScannerRegistry.register("rsi_oversold")
def rsi_oversold(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.rsi_14 is not None and float(current.rsi_14) < 30
    score = 1.0 if matched else 0.0
    return matched, score, {"rsi_14": current.rsi_14}


@ScannerRegistry.register("rsi_overbought")
def rsi_overbought(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.rsi_14 is not None and float(current.rsi_14) > 70
    score = 1.0 if matched else 0.0
    return matched, score, {"rsi_14": current.rsi_14}


@ScannerRegistry.register("macd_bullish")
def macd_bullish(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if previous is None or not all(
        [current.macd, current.macd_signal, previous.macd, previous.macd_signal]
    ):
        return False, 0.0, {}
    matched = float(current.macd) > float(current.macd_signal) and float(previous.macd) < float(
        previous.macd_signal
    )
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd, "macd_signal": current.macd_signal}


@ScannerRegistry.register("macd_bearish")
def macd_bearish(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if previous is None or not all(
        [current.macd, current.macd_signal, previous.macd, previous.macd_signal]
    ):
        return False, 0.0, {}
    matched = float(current.macd) < float(current.macd_signal) and float(previous.macd) > float(
        previous.macd_signal
    )
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd, "macd_signal": current.macd_signal}


@ScannerRegistry.register("stochastic_oversold")
def stochastic_oversold(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.stochastic_k is not None and float(current.stochastic_k) < 20
    score = 1.0 if matched else 0.0
    return matched, score, {"stochastic_k": current.stochastic_k}


@ScannerRegistry.register("stochastic_overbought")
def stochastic_overbought(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.stochastic_k is not None and float(current.stochastic_k) > 80
    score = 1.0 if matched else 0.0
    return matched, score, {"stochastic_k": current.stochastic_k}


@ScannerRegistry.register("macd_above_zero")
def macd_above_zero(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.macd is not None and float(current.macd) > 0
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd}


@ScannerRegistry.register("macd_below_zero")
def macd_below_zero(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.macd is not None and float(current.macd) < 0
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd}


@ScannerRegistry.register("bollinger_band_squeeze")
def bollinger_band_squeeze(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if not all([current.bollinger_upper, current.bollinger_lower, current.bollinger_middle]):
        return False, 0.0, {}
    bandwidth = (float(current.bollinger_upper) - float(current.bollinger_lower)) / float(
        current.bollinger_middle
    )
    matched = bandwidth < 0.05
    score = 1.0 if matched else 0.0
    return matched, score, {"bandwidth": bandwidth}


@ScannerRegistry.register("price_above_upper_bollinger")
def price_above_upper_bollinger(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.bollinger_upper is not None and current.close > float(current.bollinger_upper)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "bollinger_upper": current.bollinger_upper}


@ScannerRegistry.register("price_below_lower_bollinger")
def price_below_lower_bollinger(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.bollinger_lower is not None and current.close < float(current.bollinger_lower)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "bollinger_lower": current.bollinger_lower}


@ScannerRegistry.register("atr_high_volatility")
def atr_high_volatility(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.atr is None or current.close == 0:
        return False, 0.0, {}
    atr_pct = float(current.atr) / current.close * 100
    matched = atr_pct > 3.0
    score = 1.0 if matched else 0.0
    return matched, score, {"atr": current.atr, "atr_pct": atr_pct}


@ScannerRegistry.register("obv_rising")
def obv_rising(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if previous is None or current.obv is None or previous.obv is None:
        return False, 0.0, {}
    matched = float(current.obv) > float(previous.obv)
    score = 1.0 if matched else 0.0
    return matched, score, {"obv": current.obv, "prev_obv": previous.obv}


@ScannerRegistry.register("vwap_bullish")
def vwap_bullish(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.vwap is not None and current.close > float(current.vwap)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "vwap": current.vwap}


@ScannerRegistry.register("vwap_bearish")
def vwap_bearish(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.vwap is not None and current.close < float(current.vwap)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "vwap": current.vwap}


@ScannerRegistry.register("strong_trend_adx")
def strong_trend_adx(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.adx is not None and float(current.adx) > 25
    score = 1.0 if matched else 0.0
    return matched, score, {"adx": current.adx}


@ScannerRegistry.register("weak_trend_adx")
def weak_trend_adx(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    matched = current.adx is not None and float(current.adx) < 20
    score = 1.0 if matched else 0.0
    return matched, score, {"adx": current.adx}


@ScannerRegistry.register("volume_2x_average")
def volume_2x_average(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.volume is None or current.avg_volume_20d is None or current.avg_volume_20d == 0:
        return False, 0.0, {}
    multiple = current.volume / current.avg_volume_20d
    matched = multiple >= 2.0
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "volume": current.volume,
            "average_volume_20d": current.avg_volume_20d,
            "volume_multiple": multiple,
            "reason": f"Volume {multiple:.1f}x 20-day average",
        },
    )


@ScannerRegistry.register("volume_5x_average")
def volume_5x_average(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.volume is None or current.avg_volume_20d is None or current.avg_volume_20d == 0:
        return False, 0.0, {}
    multiple = current.volume / current.avg_volume_20d
    matched = multiple >= 5.0
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "volume": current.volume,
            "average_volume_20d": current.avg_volume_20d,
            "volume_multiple": multiple,
            "reason": f"Volume {multiple:.1f}x 20-day average",
        },
    )


@ScannerRegistry.register("highest_volume_20d")
def highest_volume_20d(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.volume is None or current.max_volume_20d is None:
        return False, 0.0, {}
    matched = current.volume >= current.max_volume_20d
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "volume": current.volume,
            "max_volume_20d": current.max_volume_20d,
            "reason": "Highest volume in last 20 sessions",
        },
    )


@ScannerRegistry.register("volume_breakout")
def volume_breakout(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.volume is None or current.max_volume_20d is None:
        return False, 0.0, {}
    matched = current.volume > current.max_volume_20d
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "volume": current.volume,
            "max_volume_20d": current.max_volume_20d,
            "breakout_level": current.max_volume_20d,
            "reason": f"Volume breakout above 20-day max {current.max_volume_20d}",
        },
    )


@ScannerRegistry.register("delivery_above_60")
def delivery_above_60(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.delivery_percentage is None:
        return False, 0.0, {}
    matched = float(current.delivery_percentage) >= 60.0
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "delivery_percent": current.delivery_percentage,
            "reason": f"Delivery {current.delivery_percentage:.1f}% >= 60%",
        },
    )


@ScannerRegistry.register("delivery_above_70")
def delivery_above_70(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.delivery_percentage is None:
        return False, 0.0, {}
    matched = float(current.delivery_percentage) >= 70.0
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "delivery_percent": current.delivery_percentage,
            "reason": f"Delivery {current.delivery_percentage:.1f}% >= 70%",
        },
    )


@ScannerRegistry.register("delivery_above_average")
def delivery_above_average(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.delivery_percentage is None or current.avg_delivery_20d is None:
        return False, 0.0, {}
    matched = float(current.delivery_percentage) > current.avg_delivery_20d
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "delivery_percent": current.delivery_percentage,
            "average_delivery_20d": current.avg_delivery_20d,
            "reason": (
                f"Delivery {current.delivery_percentage:.1f}% > "
                f"20-day avg {current.avg_delivery_20d:.1f}%"
            ),
        },
    )


@ScannerRegistry.register("delivery_breakout")
def delivery_breakout(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if current.delivery_percentage is None or current.max_delivery_20d is None:
        return False, 0.0, {}
    matched = float(current.delivery_percentage) > current.max_delivery_20d
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "delivery_percent": current.delivery_percentage,
            "max_delivery_20d": current.max_delivery_20d,
            "breakout_level": current.max_delivery_20d,
            "reason": f"Delivery breakout above 20-day max {current.max_delivery_20d:.1f}%",
        },
    )


@ScannerRegistry.register("high_volume_high_delivery")
def high_volume_high_delivery(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if (
        current.volume is None
        or current.avg_volume_20d is None
        or current.delivery_percentage is None
    ):
        return False, 0.0, {}
    volume_ok = current.volume >= 2.0 * current.avg_volume_20d
    delivery_ok = float(current.delivery_percentage) >= 60.0
    matched = volume_ok and delivery_ok
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "volume": current.volume,
            "average_volume_20d": current.avg_volume_20d,
            "delivery_percent": current.delivery_percentage,
            "reason": (
                f"Volume {current.volume} >= 2x avg {current.avg_volume_20d:.0f}, "
                f"Delivery {current.delivery_percentage:.1f}% >= 60%"
            ),
        },
    )


@ScannerRegistry.register("price_breakout_high_delivery")
def price_breakout_high_delivery(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if (
        current.close is None
        or current.high is None
        or current.max_high_20d is None
        or current.volume is None
        or current.avg_volume_20d is None
        or current.delivery_percentage is None
    ):
        return False, 0.0, {}
    price_ok = current.close > current.max_high_20d
    volume_ok = current.volume >= current.avg_volume_20d
    delivery_ok = float(current.delivery_percentage) >= 60.0
    matched = price_ok and volume_ok and delivery_ok
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "high": current.high,
            "max_high_20d": current.max_high_20d,
            "volume": current.volume,
            "average_volume_20d": current.avg_volume_20d,
            "delivery_percent": current.delivery_percentage,
            "breakout_level": current.max_high_20d,
            "reason": (
                f"Close {current.close} > 20d high {current.max_high_20d}, "
                f"Delivery {current.delivery_percentage:.1f}% >= 60%"
            ),
        },
    )


@ScannerRegistry.register("accumulation")
def accumulation(
    current: IndicatorRow, previous: IndicatorRow | None
) -> tuple[bool, float, dict[str, Any]]:
    if (
        previous is None
        or current.volume is None
        or current.avg_volume_20d is None
        or current.delivery_percentage is None
        or current.avg_delivery_20d is None
    ):
        return False, 0.0, {}
    price_up = current.close > previous.close
    volume_up = current.volume > current.avg_volume_20d
    delivery_up = float(current.delivery_percentage) > current.avg_delivery_20d
    matched = price_up and volume_up and delivery_up
    score = 1.0 if matched else 0.0
    return (
        matched,
        score,
        {
            "symbol": current.symbol,
            "close": current.close,
            "prev_close": previous.close,
            "volume": current.volume,
            "average_volume_20d": current.avg_volume_20d,
            "delivery_percent": current.delivery_percentage,
            "average_delivery_20d": current.avg_delivery_20d,
            "reason": (
                f"Close up, volume {current.volume} > avg {current.avg_volume_20d:.0f}, "
                f"delivery {current.delivery_percentage:.1f}% > avg {current.avg_delivery_20d:.1f}%"
            ),
        },
    )


class ScanRunner:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _fetch_indicator_rows(self, evaluation_date: date | None = None) -> list[tuple[IndicatorRow, IndicatorRow | None]]:
        today = evaluation_date or datetime.now(UTC).date()
        statement = (
            select(
                IndicatorResult,
                DailyPrice.close,
                DailyPrice.high,
                DailyPrice.volume,
                DailyPrice.delivery_percentage,
                Stock.symbol,
            )
            .join(Stock, IndicatorResult.stock_id == Stock.id)
            .join(
                DailyPrice,
                (DailyPrice.stock_id == IndicatorResult.stock_id)
                & (DailyPrice.trade_date == IndicatorResult.trade_date),
            )
            .where(IndicatorResult.trade_date == today)
            .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
        )
        rows = (await self.session.execute(statement)).fetchall()

        if not rows:
            statement = (
                select(
                    IndicatorResult,
                    DailyPrice.close,
                    DailyPrice.high,
                    DailyPrice.volume,
                    DailyPrice.delivery_percentage,
                    Stock.symbol,
                )
                .join(Stock, IndicatorResult.stock_id == Stock.id)
                .join(
                    DailyPrice,
                    (DailyPrice.stock_id == IndicatorResult.stock_id)
                    & (DailyPrice.trade_date == IndicatorResult.trade_date),
                )
                .where(Stock.symbol.not_in(SYNTHETIC_FIXTURE_SYMBOLS))
                .order_by(IndicatorResult.trade_date.desc())
                .limit(500)
            )
            rows = (await self.session.execute(statement)).fetchall()

        result_map: dict[int, IndicatorRow] = {}
        for ir, close, high, volume, delivery_pct, symbol in rows:
            result_map[ir.stock_id] = IndicatorRow(
                stock_id=ir.stock_id,
                symbol=symbol,
                trade_date=ir.trade_date,
                close=float(close) if close is not None else 0.0,
                high=float(high) if high is not None else None,
                volume=int(volume) if volume is not None else None,
                delivery_percentage=float(delivery_pct) if delivery_pct is not None else None,
                ema_20=_to_float(ir.ema_20),
                ema_50=_to_float(ir.ema_50),
                ema_100=_to_float(ir.ema_100),
                ema_200=_to_float(ir.ema_200),
                sma=_to_float(ir.sma),
                rsi_14=_to_float(ir.rsi_14),
                macd=_to_float(ir.macd),
                macd_signal=_to_float(ir.macd_signal),
                macd_hist=_to_float(ir.macd_hist),
                stochastic_k=_to_float(ir.stochastic_k),
                stochastic_d=_to_float(ir.stochastic_d),
                atr=_to_float(ir.atr),
                bollinger_upper=_to_float(ir.bollinger_upper),
                bollinger_middle=_to_float(ir.bollinger_middle),
                bollinger_lower=_to_float(ir.bollinger_lower),
                adx=_to_float(ir.adx),
                supertrend=_to_float(ir.supertrend),
                supertrend_direction=ir.supertrend_direction,
                vwap=_to_float(ir.vwap),
                obv=_to_float(ir.obv),
            )

        stock_ids = list(result_map.keys())
        if not stock_ids:
            return []

        prev_statement = (
            select(
                IndicatorResult,
                DailyPrice.close,
            )
            .join(
                DailyPrice,
                (DailyPrice.stock_id == IndicatorResult.stock_id)
                & (DailyPrice.trade_date == IndicatorResult.trade_date),
            )
            .where(IndicatorResult.stock_id.in_(stock_ids))
            .where(IndicatorResult.trade_date < today)
            .order_by(IndicatorResult.trade_date.desc())
        )
        prev_rows = (await self.session.execute(prev_statement)).fetchall()

        prev_map: dict[int, IndicatorRow] = {}
        for ir, close in prev_rows:
            if ir.stock_id not in prev_map:
                prev_map[ir.stock_id] = IndicatorRow(
                    stock_id=ir.stock_id,
                    symbol="",
                    trade_date=ir.trade_date,
                    close=float(close) if close is not None else 0.0,
                    ema_20=_to_float(ir.ema_20),
                    ema_50=_to_float(ir.ema_50),
                    ema_100=_to_float(ir.ema_100),
                    ema_200=_to_float(ir.ema_200),
                    sma=_to_float(ir.sma),
                    rsi_14=_to_float(ir.rsi_14),
                    macd=_to_float(ir.macd),
                    macd_signal=_to_float(ir.macd_signal),
                    macd_hist=_to_float(ir.macd_hist),
                    stochastic_k=_to_float(ir.stochastic_k),
                    stochastic_d=_to_float(ir.stochastic_d),
                    atr=_to_float(ir.atr),
                    bollinger_upper=_to_float(ir.bollinger_upper),
                    bollinger_middle=_to_float(ir.bollinger_middle),
                    bollinger_lower=_to_float(ir.bollinger_lower),
                    adx=_to_float(ir.adx),
                    supertrend=_to_float(ir.supertrend),
                    supertrend_direction=ir.supertrend_direction,
                    vwap=_to_float(ir.vwap),
                    obv=_to_float(ir.obv),
                )

        history_statement = (
            select(
                DailyPrice.stock_id,
                DailyPrice.volume,
                DailyPrice.delivery_percentage,
                DailyPrice.high,
            )
            .where(DailyPrice.stock_id.in_(stock_ids))
            .where(DailyPrice.trade_date < today)
            .order_by(DailyPrice.stock_id, DailyPrice.trade_date.desc())
        )
        history_rows = (await self.session.execute(history_statement)).fetchall()

        history_map: dict[int, list[Any]] = {}
        for stock_id, volume, delivery_pct, high in history_rows:
            if stock_id not in history_map:
                history_map[stock_id] = []
            history_map[stock_id].append((volume, delivery_pct, high))

        for stock_id in stock_ids:
            rows_history = history_map.get(stock_id, [])[:20]
            if len(rows_history) >= 20:
                dp_rows = [
                    type("DP", (), {"volume": v, "delivery_percentage": dp, "high": h})
                    for v, dp, h in rows_history
                ]
                aggs = _compute_20d_aggregates(dp_rows)
                if stock_id in result_map:
                    for key, value in aggs.items():
                        setattr(result_map[stock_id], key, value)

        output: list[tuple[IndicatorRow, IndicatorRow | None]] = []
        for stock_id in stock_ids:
            current = result_map.get(stock_id)
            previous = prev_map.get(stock_id)
            if current:
                output.append((current, previous))
        return output

    async def run_scanner(self, scan_code: str, evaluation_date: date | None = None) -> list[ScanResultDTO]:
        scanner_func = ScannerRegistry.get(scan_code)
        if scanner_func is None:
            raise ValueError(f"Unknown scanner: {scan_code}")

        scan_date = evaluation_date or datetime.now(UTC).date()
        rows = await self._fetch_indicator_rows(scan_date)
        results: list[ScanResultDTO] = []

        for current, previous in rows:
            try:
                matched, score, metrics = scanner_func(current, previous)
                if matched:
                    results.append(
                        ScanResultDTO(
                            scan_definition_id=0,
                            stock_id=current.stock_id,
                            scan_date=scan_date,
                            score=score,
                            metrics=metrics,
                        )
                    )
            except Exception as exc:
                logger.warning("Scanner {} failed for {}: {}", scan_code, current.symbol, exc)

        return results

    async def run_all(self, evaluation_date: date | None = None) -> dict[str, list[ScanResultDTO]]:
        scan_defs = await ScanDefinitionRepository(self.session).list_active()
        output: dict[str, list[ScanResultDTO]] = {}
        for scan_def in scan_defs:
            scanner_func = ScannerRegistry.get(scan_def.code)
            if scanner_func is None:
                continue
            scan_date = evaluation_date or datetime.now(UTC).date()
            rows = await self._fetch_indicator_rows(scan_date)
            results: list[ScanResultDTO] = []
            for current, previous in rows:
                try:
                    matched, score, metrics = scanner_func(current, previous)
                    if matched:
                        results.append(
                            ScanResultDTO(
                                scan_definition_id=scan_def.id,
                                stock_id=current.stock_id,
                                scan_date=scan_date,
                                score=score,
                                metrics=metrics,
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        "Scanner {} failed for {}: {}", scan_def.code, current.symbol, exc
                    )
            output[scan_def.code] = results
        return output


class ScannerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runner = ScanRunner(session)

    async def run_scanner(self, scan_code: str, evaluation_date: date | None = None) -> list[ScanResultDTO]:
        scan_def = await ScanDefinitionRepository(self.session).get_by_code(scan_code)
        if scan_def is None:
            raise ValueError(f"Scan definition not found: {scan_code}")
        return await self.runner.run_scanner(scan_code, evaluation_date)

    async def run_all(self, evaluation_date: date | None = None) -> dict[str, list[ScanResultDTO]]:
        return await self.runner.run_all(evaluation_date)

    async def persist_results(self, results: dict[str, list[ScanResultDTO]]) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        for scan_code, entries in results.items():
            scan_def = await ScanDefinitionRepository(self.session).get_by_code(scan_code)
            if scan_def is None:
                continue
            for entry in entries:
                statement = (
                    pg_insert(ScanResult)
                    .values(
                        scan_definition_id=scan_def.id,
                        stock_id=entry.stock_id,
                        scan_date=entry.scan_date,
                        score=entry.score,
                        metrics=entry.metrics,
                    )
                    .on_conflict_do_nothing(constraint="uq_scan_result_scan_stock_date")
                )
                try:
                    await self.session.execute(statement)
                except Exception:
                    await self.session.rollback()
                    raise
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
