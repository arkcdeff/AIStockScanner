from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


@ScannerRegistry.register("ema_bullish_crossover")
def ema_bullish_crossover(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(current.ema_20 and current.ema_50 and previous.ema_20 and previous.ema_50 and
                    current.ema_20 > current.ema_50 and previous.ema_20 < previous.ema_50)
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_20": current.ema_20, "ema_50": current.ema_50}


@ScannerRegistry.register("ema_bearish_crossover")
def ema_bearish_crossover(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(current.ema_20 and current.ema_50 and previous.ema_20 and previous.ema_50 and
                    current.ema_20 < current.ema_50 and previous.ema_20 > previous.ema_50)
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_20": current.ema_20, "ema_50": current.ema_50}


@ScannerRegistry.register("golden_cross")
def golden_cross(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(current.ema_50 and current.ema_200 and previous.ema_50 and previous.ema_200 and
                    current.ema_50 > current.ema_200 and previous.ema_50 < previous.ema_200)
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_50": current.ema_50, "ema_200": current.ema_200}


@ScannerRegistry.register("death_cross")
def death_cross(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if previous is None:
        return False, 0.0, {}
    matched = bool(current.ema_50 and current.ema_200 and previous.ema_50 and previous.ema_200 and
                    current.ema_50 < current.ema_200 and previous.ema_50 > previous.ema_200)
    score = 1.0 if matched else 0.0
    return matched, score, {"ema_50": current.ema_50, "ema_200": current.ema_200}


@ScannerRegistry.register("price_above_ema20")
def price_above_ema20(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = bool(current.ema_20 and current.close > float(current.ema_20))
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "ema_20": current.ema_20}


@ScannerRegistry.register("price_below_ema20")
def price_below_ema20(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = bool(current.ema_20 and current.close < float(current.ema_20))
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "ema_20": current.ema_20}


@ScannerRegistry.register("supertrend_bullish")
def supertrend_bullish(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.supertrend_direction == 1
    score = 1.0 if matched else 0.0
    return matched, score, {"supertrend": current.supertrend, "direction": current.supertrend_direction}


@ScannerRegistry.register("supertrend_bearish")
def supertrend_bearish(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.supertrend_direction == -1
    score = 1.0 if matched else 0.0
    return matched, score, {"supertrend": current.supertrend, "direction": current.supertrend_direction}


@ScannerRegistry.register("rsi_oversold")
def rsi_oversold(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.rsi_14 is not None and float(current.rsi_14) < 30
    score = 1.0 if matched else 0.0
    return matched, score, {"rsi_14": current.rsi_14}


@ScannerRegistry.register("rsi_overbought")
def rsi_overbought(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.rsi_14 is not None and float(current.rsi_14) > 70
    score = 1.0 if matched else 0.0
    return matched, score, {"rsi_14": current.rsi_14}


@ScannerRegistry.register("macd_bullish")
def macd_bullish(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if previous is None or not all([current.macd, current.macd_signal, previous.macd, previous.macd_signal]):
        return False, 0.0, {}
    matched = float(current.macd) > float(current.macd_signal) and float(previous.macd) < float(previous.macd_signal)
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd, "macd_signal": current.macd_signal}


@ScannerRegistry.register("macd_bearish")
def macd_bearish(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if previous is None or not all([current.macd, current.macd_signal, previous.macd, previous.macd_signal]):
        return False, 0.0, {}
    matched = float(current.macd) < float(current.macd_signal) and float(previous.macd) > float(previous.macd_signal)
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd, "macd_signal": current.macd_signal}


@ScannerRegistry.register("stochastic_oversold")
def stochastic_oversold(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.stochastic_k is not None and float(current.stochastic_k) < 20
    score = 1.0 if matched else 0.0
    return matched, score, {"stochastic_k": current.stochastic_k}


@ScannerRegistry.register("stochastic_overbought")
def stochastic_overbought(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.stochastic_k is not None and float(current.stochastic_k) > 80
    score = 1.0 if matched else 0.0
    return matched, score, {"stochastic_k": current.stochastic_k}


@ScannerRegistry.register("macd_above_zero")
def macd_above_zero(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.macd is not None and float(current.macd) > 0
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd}


@ScannerRegistry.register("macd_below_zero")
def macd_below_zero(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.macd is not None and float(current.macd) < 0
    score = 1.0 if matched else 0.0
    return matched, score, {"macd": current.macd}


@ScannerRegistry.register("bollinger_band_squeeze")
def bollinger_band_squeeze(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if not all([current.bollinger_upper, current.bollinger_lower, current.bollinger_middle]):
        return False, 0.0, {}
    bandwidth = (float(current.bollinger_upper) - float(current.bollinger_lower)) / float(current.bollinger_middle)
    matched = bandwidth < 0.05
    score = 1.0 if matched else 0.0
    return matched, score, {"bandwidth": bandwidth}


@ScannerRegistry.register("price_above_upper_bollinger")
def price_above_upper_bollinger(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.bollinger_upper is not None and current.close > float(current.bollinger_upper)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "bollinger_upper": current.bollinger_upper}


@ScannerRegistry.register("price_below_lower_bollinger")
def price_below_lower_bollinger(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.bollinger_lower is not None and current.close < float(current.bollinger_lower)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "bollinger_lower": current.bollinger_lower}


@ScannerRegistry.register("atr_high_volatility")
def atr_high_volatility(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if current.atr is None or current.close == 0:
        return False, 0.0, {}
    atr_pct = float(current.atr) / current.close * 100
    matched = atr_pct > 3.0
    score = 1.0 if matched else 0.0
    return matched, score, {"atr": current.atr, "atr_pct": atr_pct}


@ScannerRegistry.register("obv_rising")
def obv_rising(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    if previous is None or current.obv is None or previous.obv is None:
        return False, 0.0, {}
    matched = float(current.obv) > float(previous.obv)
    score = 1.0 if matched else 0.0
    return matched, score, {"obv": current.obv, "prev_obv": previous.obv}


@ScannerRegistry.register("vwap_bullish")
def vwap_bullish(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.vwap is not None and current.close > float(current.vwap)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "vwap": current.vwap}


@ScannerRegistry.register("vwap_bearish")
def vwap_bearish(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.vwap is not None and current.close < float(current.vwap)
    score = 1.0 if matched else 0.0
    return matched, score, {"close": current.close, "vwap": current.vwap}


@ScannerRegistry.register("strong_trend_adx")
def strong_trend_adx(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.adx is not None and float(current.adx) > 25
    score = 1.0 if matched else 0.0
    return matched, score, {"adx": current.adx}


@ScannerRegistry.register("weak_trend_adx")
def weak_trend_adx(current: IndicatorRow, previous: IndicatorRow | None) -> tuple[bool, float, dict[str, Any]]:
    matched = current.adx is not None and float(current.adx) < 20
    score = 1.0 if matched else 0.0
    return matched, score, {"adx": current.adx}


class ScanRunner:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _fetch_indicator_rows(self) -> list[tuple[IndicatorRow, IndicatorRow | None]]:
        today = datetime.now(UTC).date()
        statement = (
            select(
                IndicatorResult,
                DailyPrice.close,
                Stock.symbol,
            )
            .join(Stock, IndicatorResult.stock_id == Stock.id)
            .join(DailyPrice, (DailyPrice.stock_id == IndicatorResult.stock_id) & (DailyPrice.trade_date == IndicatorResult.trade_date))
            .where(IndicatorResult.trade_date == today)
        )
        rows = (await self.session.execute(statement)).fetchall()

        if not rows:
            statement = (
                select(
                    IndicatorResult,
                    DailyPrice.close,
                    Stock.symbol,
                )
                .join(Stock, IndicatorResult.stock_id == Stock.id)
                .join(DailyPrice, (DailyPrice.stock_id == IndicatorResult.stock_id) & (DailyPrice.trade_date == IndicatorResult.trade_date))
                .order_by(IndicatorResult.trade_date.desc())
                .limit(500)
            )
            rows = (await self.session.execute(statement)).fetchall()

        result_map: dict[int, IndicatorRow] = {}
        for ir, close, symbol in rows:
            result_map[ir.stock_id] = IndicatorRow(
                stock_id=ir.stock_id,
                symbol=symbol,
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

        stock_ids = list(result_map.keys())
        if not stock_ids:
            return []

        prev_statement = (
            select(
                IndicatorResult,
                DailyPrice.close,
            )
            .join(DailyPrice, (DailyPrice.stock_id == IndicatorResult.stock_id) & (DailyPrice.trade_date == IndicatorResult.trade_date))
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

        output: list[tuple[IndicatorRow, IndicatorRow | None]] = []
        for stock_id in stock_ids:
            current = result_map.get(stock_id)
            previous = prev_map.get(stock_id)
            if current:
                output.append((current, previous))
        return output

    async def run_scanner(self, scan_code: str) -> list[ScanResultDTO]:
        scanner_func = ScannerRegistry.get(scan_code)
        if scanner_func is None:
            raise ValueError(f"Unknown scanner: {scan_code}")

        scan_date = datetime.now(UTC).date()
        rows = await self._fetch_indicator_rows()
        results: list[ScanResultDTO] = []

        for current, previous in rows:
            try:
                matched, score, metrics = scanner_func(current, previous)
                if matched:
                    results.append(ScanResultDTO(
                        scan_definition_id=0,
                        stock_id=current.stock_id,
                        scan_date=scan_date,
                        score=score,
                        metrics=metrics,
                    ))
            except Exception as exc:
                logger.warning("Scanner {} failed for {}: {}", scan_code, current.symbol, exc)

        return results

    async def run_all(self) -> dict[str, list[ScanResultDTO]]:
        scan_defs = await ScanDefinitionRepository(self.session).list_active()
        output: dict[str, list[ScanResultDTO]] = {}
        for scan_def in scan_defs:
            scanner_func = ScannerRegistry.get(scan_def.code)
            if scanner_func is None:
                continue
            scan_date = datetime.now(UTC).date()
            rows = await self._fetch_indicator_rows()
            results: list[ScanResultDTO] = []
            for current, previous in rows:
                try:
                    matched, score, metrics = scanner_func(current, previous)
                    if matched:
                        results.append(ScanResultDTO(
                            scan_definition_id=scan_def.id,
                            stock_id=current.stock_id,
                            scan_date=scan_date,
                            score=score,
                            metrics=metrics,
                        ))
                except Exception as exc:
                    logger.warning("Scanner {} failed for {}: {}", scan_def.code, current.symbol, exc)
            output[scan_def.code] = results
        return output


class ScannerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runner = ScanRunner(session)

    async def run_scanner(self, scan_code: str) -> list[ScanResultDTO]:
        scan_def = await ScanDefinitionRepository(self.session).get_by_code(scan_code)
        if scan_def is None:
            raise ValueError(f"Scan definition not found: {scan_code}")
        return await self.runner.run_scanner(scan_code)

    async def run_all(self) -> dict[str, list[ScanResultDTO]]:
        return await self.runner.run_all()

    async def persist_results(self, results: dict[str, list[ScanResultDTO]]) -> None:
        for scan_code, entries in results.items():
            scan_def = await ScanDefinitionRepository(self.session).get_by_code(scan_code)
            if scan_def is None:
                continue
            for entry in entries:
                result = ScanResult(
                    scan_definition_id=scan_def.id,
                    stock_id=entry.stock_id,
                    scan_date=entry.scan_date,
                    score=entry.score,
                    metrics=entry.metrics,
                )
                self.session.add(result)
        await self.session.commit()
