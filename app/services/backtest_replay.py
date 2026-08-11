"""Historical scanner replay engine with no-look-ahead guarantees.

Replays existing scanner logic against stored DailyPrice/Indicator data
without future-data leakage. For replay date D, only information available
on or before D is used.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock
from app.services.nse_historical import NSEHistoricalService
from app.services.scanner_engine import (
    IndicatorRow,
    ScannerRegistry,
    _compute_20d_aggregates,
    _to_float,
)

# Number of calendar days of lookback needed to compute EMA-200 and other
# long-window indicators from raw prices when historical IndicatorResult rows
# are missing.
LOOKBACK_DAYS = 400


@dataclass(frozen=True, slots=True)
class ReplaySignal:
    """A single historical scanner match for a stock on a trade date."""

    trade_date: date
    stock_id: int
    symbol: str
    scanner_code: str
    matched: bool
    score: float
    reason: str = ""
    close: float | None = None
    volume: int | None = None
    delivery_percent: float | None = None
    indicator_snapshot: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReplaySummary:
    """Summary of a replay run."""

    dates_processed: int = 0
    stocks_evaluated: int = 0
    scanners_evaluated: int = 0
    signals_generated: int = 0
    skipped_dates: int = 0
    insufficient_history: int = 0
    duration_ms: float = 0.0
    signals: list[ReplaySignal] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dates_processed": self.dates_processed,
            "stocks_evaluated": self.stocks_evaluated,
            "scanners_evaluated": self.scanners_evaluated,
            "signals_generated": self.signals_generated,
            "skipped_dates": self.skipped_dates,
            "insufficient_history": self.insufficient_history,
            "duration_ms": self.duration_ms,
        }


class BacktestReplayService:
    """Replay existing scanner rules against historical data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ data loading

    async def _load_stocks(self, symbols: list[str] | None) -> list[Stock]:
        statement = select(Stock).where(Stock.is_active.is_(True))
        if symbols:
            statement = statement.where(Stock.symbol.in_([s.upper() for s in symbols]))
        return list((await self.session.scalars(statement)).all())

    async def _load_prices(
        self, stock_ids: list[int], start_date: date, end_date: date
    ) -> dict[int, list[DailyPrice]]:
        """Bulk-load DailyPrice rows for the universe/date range, grouped by stock."""
        if not stock_ids:
            return {}
        rows = (
            await self.session.scalars(
                select(DailyPrice)
                .where(DailyPrice.stock_id.in_(stock_ids))
                .where(DailyPrice.trade_date >= start_date)
                .where(DailyPrice.trade_date <= end_date)
                .order_by(DailyPrice.stock_id, DailyPrice.trade_date)
            )
        ).all()
        grouped: dict[int, list[DailyPrice]] = {}
        for row in rows:
            grouped.setdefault(row.stock_id, []).append(row)
        return grouped

    async def _load_indicators(
        self, stock_ids: list[int], start_date: date, end_date: date
    ) -> dict[int, list[IndicatorResult]]:
        """Bulk-load IndicatorResult rows for the universe/date range, grouped by stock."""
        if not stock_ids:
            return {}
        rows = (
            await self.session.scalars(
                select(IndicatorResult)
                .where(IndicatorResult.stock_id.in_(stock_ids))
                .where(IndicatorResult.trade_date >= start_date)
                .where(IndicatorResult.trade_date <= end_date)
                .order_by(IndicatorResult.stock_id, IndicatorResult.trade_date)
            )
        ).all()
        grouped: dict[int, list[IndicatorResult]] = {}
        for row in rows:
            grouped.setdefault(row.stock_id, []).append(row)
        return grouped

    # ------------------------------------------------------------------ indicator construction

    def _build_indicator_row(
        self,
        *,
        stock_id: int,
        symbol: str,
        trade_date: date,
        price: DailyPrice | None,
        indicator: IndicatorResult | None,
    ) -> IndicatorRow:
        """Build an IndicatorRow from a price row and optional indicator row."""
        return IndicatorRow(
            stock_id=stock_id,
            symbol=symbol,
            trade_date=trade_date,
            close=float(price.close) if price and price.close is not None else 0.0,
            high=float(price.high) if price and price.high is not None else None,
            volume=int(price.volume) if price and price.volume is not None else None,
            delivery_percentage=(
                float(price.delivery_percentage)
                if price and price.delivery_percentage is not None
                else None
            ),
            ema_20=_to_float(indicator.ema_20) if indicator else None,
            ema_50=_to_float(indicator.ema_50) if indicator else None,
            ema_100=_to_float(indicator.ema_100) if indicator else None,
            ema_200=_to_float(indicator.ema_200) if indicator else None,
            sma=_to_float(indicator.sma) if indicator else None,
            rsi_14=_to_float(indicator.rsi_14) if indicator else None,
            macd=_to_float(indicator.macd) if indicator else None,
            macd_signal=_to_float(indicator.macd_signal) if indicator else None,
            macd_hist=_to_float(indicator.macd_hist) if indicator else None,
            stochastic_k=_to_float(indicator.stochastic_k) if indicator else None,
            stochastic_d=_to_float(indicator.stochastic_d) if indicator else None,
            atr=_to_float(indicator.atr) if indicator else None,
            bollinger_upper=_to_float(indicator.bollinger_upper) if indicator else None,
            bollinger_middle=_to_float(indicator.bollinger_middle) if indicator else None,
            bollinger_lower=_to_float(indicator.bollinger_lower) if indicator else None,
            adx=_to_float(indicator.adx) if indicator else None,
            supertrend=_to_float(indicator.supertrend) if indicator else None,
            supertrend_direction=indicator.supertrend_direction if indicator else None,
            vwap=_to_float(indicator.vwap) if indicator else None,
            obv=_to_float(indicator.obv) if indicator else None,
        )

    def _compute_20d_from_prices(
        self, prices: list[DailyPrice], replay_date: date
    ) -> dict[str, Any]:
        """Compute 20-day aggregates from prices strictly BEFORE replay_date.

        This preserves the live scanner semantics where the 20-day window
        excludes the current row.
        """
        prior = [p for p in prices if p.trade_date < replay_date]
        prior = prior[-20:]
        return _compute_20d_aggregates(prior)

    # ------------------------------------------------------------------ replay

    async def replay_scanner(
        self,
        scanner_code: str,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
    ) -> ReplaySummary:
        """Replay a single scanner over a date range."""
        scanner_func = ScannerRegistry.get(scanner_code)
        if scanner_func is None:
            raise ValueError(f"Unknown scanner: {scanner_code}")

        started = time.perf_counter()
        summary = ReplaySummary()
        summary.scanners_evaluated = 1

        stocks = await self._load_stocks(symbols)
        stock_ids = [s.id for s in stocks]
        stock_symbols = {s.id: s.symbol for s in stocks}

        # Load prices with lookback so 20-day aggregates and (if needed)
        # indicator calculation have enough history.
        price_start = start_date - timedelta(days=LOOKBACK_DAYS)
        prices_by_stock = await self._load_prices(stock_ids, price_start, end_date)
        indicators_by_stock = await self._load_indicators(stock_ids, price_start, end_date)

        # Build a set of trading dates present in the data.
        trading_dates = sorted(
            {p.trade_date for prices in prices_by_stock.values() for p in prices}
        )
        replay_dates = [d for d in trading_dates if start_date <= d <= end_date]

        for replay_date in replay_dates:
            summary.dates_processed += 1
            for stock in stocks:
                prices = prices_by_stock.get(stock.id, [])
                indicators = indicators_by_stock.get(stock.id, [])

                # Current price row for this date.
                current_price = next(
                    (p for p in prices if p.trade_date == replay_date), None
                )
                if current_price is None:
                    continue

                # Current indicator row for this date (if available).
                current_indicator = next(
                    (i for i in indicators if i.trade_date == replay_date), None
                )

                # Previous indicator row: the most recent indicator before replay_date.
                previous_indicator = None
                for i in indicators:
                    if i.trade_date < replay_date:
                        previous_indicator = i
                    else:
                        break

                # Previous price row: the most recent price before replay_date.
                previous_price = None
                for p in prices:
                    if p.trade_date < replay_date:
                        previous_price = p
                    else:
                        break

                current = self._build_indicator_row(
                    stock_id=stock.id,
                    symbol=stock_symbols[stock.id],
                    trade_date=replay_date,
                    price=current_price,
                    indicator=current_indicator,
                )
                previous = self._build_indicator_row(
                    stock_id=stock.id,
                    symbol=stock_symbols[stock.id],
                    trade_date=previous_price.trade_date if previous_price else replay_date,
                    price=previous_price,
                    indicator=previous_indicator,
                )

                # Compute 20-day aggregates from prices strictly before replay_date.
                aggs = self._compute_20d_from_prices(prices, replay_date)
                for key, value in aggs.items():
                    setattr(current, key, value)

                # Insufficient history: if the scanner needs 20-day aggregates
                # and we don't have enough prior rows, skip.
                if (
                    current.avg_volume_20d is None
                    and current.volume is not None
                    and scanner_code
                    in {
                        "volume_2x_average",
                        "volume_5x_average",
                        "highest_volume_20d",
                        "volume_breakout",
                        "high_volume_high_delivery",
                        "price_breakout_high_delivery",
                        "accumulation",
                    }
                ):
                    summary.insufficient_history += 1
                    continue

                try:
                    matched, score, metrics = scanner_func(current, previous)
                except Exception as exc:
                    logger.warning(
                        "Replay scanner {} failed for {} on {}: {}",
                        scanner_code,
                        stock.symbol,
                        replay_date,
                        exc,
                    )
                    continue

                summary.stocks_evaluated += 1
                if matched:
                    signal = ReplaySignal(
                        trade_date=replay_date,
                        stock_id=stock.id,
                        symbol=stock_symbols[stock.id],
                        scanner_code=scanner_code,
                        matched=True,
                        score=score,
                        reason=metrics.get("reason", ""),
                        close=current.close,
                        volume=current.volume,
                        delivery_percent=current.delivery_percentage,
                        indicator_snapshot={
                            "ema_20": current.ema_20,
                            "ema_50": current.ema_50,
                            "ema_200": current.ema_200,
                            "rsi_14": current.rsi_14,
                            "macd": current.macd,
                            "macd_signal": current.macd_signal,
                            "avg_volume_20d": current.avg_volume_20d,
                            "max_volume_20d": current.max_volume_20d,
                            "avg_delivery_20d": current.avg_delivery_20d,
                            "max_delivery_20d": current.max_delivery_20d,
                            "max_high_20d": current.max_high_20d,
                        },
                        metadata=metrics,
                    )
                    summary.signals.append(signal)
                    summary.signals_generated += 1

        summary.duration_ms = (time.perf_counter() - started) * 1000
        return summary

    async def replay_all(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
    ) -> ReplaySummary:
        """Replay all registered scanners over a date range."""
        started = time.perf_counter()
        summary = ReplaySummary()

        scanners = ScannerRegistry.all()
        summary.scanners_evaluated = len(scanners)

        for scanner_code in scanners:
            sub = await self.replay_scanner(
                scanner_code, start_date, end_date, symbols=symbols
            )
            summary.dates_processed = max(summary.dates_processed, sub.dates_processed)
            summary.stocks_evaluated += sub.stocks_evaluated
            summary.signals_generated += sub.signals_generated
            summary.signals.extend(sub.signals)
            summary.skipped_dates += sub.skipped_dates
            summary.insufficient_history += sub.insufficient_history

        summary.duration_ms = (time.perf_counter() - started) * 1000
        return summary

    # ------------------------------------------------------------------ data preparation

    async def ensure_historical_data(
        self,
        start_date: date,
        end_date: date,
        fetch_missing: bool = False,
    ) -> dict[str, Any]:
        """Ensure historical data exists for the range.

        If ``fetch_missing`` is False, only report missing dates.
        If True, attempt official NSE download/import for missing dates.
        Never fails the entire operation solely because NSE returns 403.
        """
        missing = await self.find_missing_dates(start_date, end_date)
        result: dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "missing_dates": [d.isoformat() for d in missing],
            "missing_count": len(missing),
            "fetch_attempted": fetch_missing,
            "fetch_results": [],
            "unresolved_gaps": [],
        }

        if not fetch_missing or not missing:
            result["unresolved_gaps"] = [d.isoformat() for d in missing]
            return result

        historical = NSEHistoricalService(self.session)
        for missing_date in missing:
            fetch_result = await historical.fetch_date(missing_date)
            result["fetch_results"].append(
                {
                    "trade_date": missing_date.isoformat(),
                    "status": fetch_result.status,
                    "message": fetch_result.message,
                }
            )
            if fetch_result.status not in {"imported", "already_present"}:
                result["unresolved_gaps"].append(missing_date.isoformat())

        return result

    async def find_missing_dates(
        self, start_date: date, end_date: date
    ) -> list[date]:
        """Return trading dates in the range with no DailyPrice data."""
        historical = NSEHistoricalService(self.session)
        return await historical.find_missing_dates(start_date, end_date)