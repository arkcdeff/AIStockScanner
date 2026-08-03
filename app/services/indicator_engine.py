from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
from loguru import logger
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.indicator_result import IndicatorResult
from app.models.stock import Stock


class IndicatorEngineError(Exception):
    pass


class IndicatorEngine:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def calculate_for_stock(self, symbol: str) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                __import__("sqlalchemy")
                .text("""
                    SELECT dp.trade_date, dp.open, dp.high, dp.low, dp.close, dp.volume,
                           dp.turnover, dp.trade_count, s.symbol
                    FROM daily_prices dp
                    JOIN stocks s ON s.id = dp.stock_id
                    WHERE s.symbol = :symbol
                    ORDER BY dp.trade_date ASC
                """)
                .bindparams(symbol=symbol.upper())
            )
        ).fetchall()

        if not rows:
            raise IndicatorEngineError(f"No price data found for symbol: {symbol}")

        df = pd.DataFrame(
            rows,
            columns=[
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "turnover",
                "trade_count",
                "symbol",
            ],
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"])

        results = self._calculate_indicators(df)
        await self._bulk_upsert(results)
        return results

    async def calculate_all(self, batch_size: int = 50) -> dict[str, int]:
        symbols = (await self.session.scalars(__import__("sqlalchemy").select(Stock.symbol))).all()
        total_calculated = 0
        total_skipped = 0

        for symbol in symbols:
            try:
                results = await self.calculate_for_stock(symbol)
                total_calculated += len(results)
            except IndicatorEngineError as exc:
                logger.warning("Skipping {}: {}", symbol, exc)
                total_skipped += 1

        return {"calculated": total_calculated, "skipped": total_skipped}

    def _calculate_indicators(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if len(df) < 200:
            logger.warning("Insufficient data for EMA-200: {} rows", len(df))

        # Trend
        df["ema_20"] = self._extract_series(df.ta.ema(length=20))
        df["ema_50"] = self._extract_series(df.ta.ema(length=50))
        df["ema_100"] = self._extract_series(df.ta.ema(length=100))
        df["ema_200"] = self._extract_series(df.ta.ema(length=200))
        df["sma"] = self._extract_series(df.ta.sma(length=20))

        # Momentum
        df["rsi_14"] = self._extract_series(df.ta.rsi(length=14))
        macd = df.ta.macd(fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            df["macd"] = self._extract_series(macd.iloc[:, 0])
            df["macd_signal"] = self._extract_series(macd.iloc[:, 1])
            df["macd_hist"] = self._extract_series(macd.iloc[:, 2])
        else:
            df["macd"] = None
            df["macd_signal"] = None
            df["macd_hist"] = None

        stoch = df.ta.stoch(k=14, d=3, smooth_k=3)
        if stoch is not None and not stoch.empty:
            df["stochastic_k"] = self._extract_series(stoch.iloc[:, 0])
            df["stochastic_d"] = self._extract_series(stoch.iloc[:, 1])
        else:
            df["stochastic_k"] = None
            df["stochastic_d"] = None

        # Volatility
        df["atr"] = self._extract_series(df.ta.atr(length=14))
        bb = df.ta.bbands(length=20, std=2)
        if bb is not None and not bb.empty:
            df["bollinger_upper"] = self._extract_series(bb.iloc[:, 0])
            df["bollinger_middle"] = self._extract_series(bb.iloc[:, 1])
            df["bollinger_lower"] = self._extract_series(bb.iloc[:, 2])
        else:
            df["bollinger_upper"] = None
            df["bollinger_middle"] = None
            df["bollinger_lower"] = None

        # Trend strength
        adx = df.ta.adx(length=14)
        if adx is not None and not adx.empty:
            df["adx"] = self._extract_series(adx.iloc[:, 0])
        else:
            df["adx"] = None

        st = df.ta.supertrend(length=7, multiplier=3)
        if st is not None and not st.empty:
            df["supertrend"] = self._extract_series(st.iloc[:, 0])
            direction_series = st.iloc[:, 1] if len(st.columns) > 1 else st.iloc[:, 0]
            df["supertrend_direction"] = (direction_series > 0).astype(int)
        else:
            df["supertrend"] = None
            df["supertrend_direction"] = None

        # Volume
        if "turnover" in df.columns and df["turnover"].notna().any():
            df["vwap"] = (df["turnover"].cumsum() / (df["volume"].cumsum() * df["close"])).replace(
                [float("inf"), -float("inf")], None
            )
        else:
            df["vwap"] = None
        df["obv"] = self._extract_series(df.ta.obv())

        results = []
        for _, row in df.iterrows():
            results.append(
                {
                    "symbol": row["symbol"],
                    "trade_date": row["trade_date"].date(),
                    "ema_20": self._to_decimal(row["ema_20"]),
                    "ema_50": self._to_decimal(row["ema_50"]),
                    "ema_100": self._to_decimal(row["ema_100"]),
                    "ema_200": self._to_decimal(row["ema_200"]),
                    "sma": self._to_decimal(row["sma"]),
                    "rsi_14": self._to_decimal(row["rsi_14"]),
                    "macd": self._to_decimal(row["macd"]),
                    "macd_signal": self._to_decimal(row["macd_signal"]),
                    "macd_hist": self._to_decimal(row["macd_hist"]),
                    "stochastic_k": self._to_decimal(row["stochastic_k"]),
                    "stochastic_d": self._to_decimal(row["stochastic_d"]),
                    "atr": self._to_decimal(row["atr"]),
                    "bollinger_upper": self._to_decimal(row["bollinger_upper"]),
                    "bollinger_middle": self._to_decimal(row["bollinger_middle"]),
                    "bollinger_lower": self._to_decimal(row["bollinger_lower"]),
                    "adx": self._to_decimal(row["adx"]),
                    "supertrend": self._to_decimal(row["supertrend"]),
                    "supertrend_direction": int(row["supertrend_direction"])
                    if pd.notna(row["supertrend_direction"])
                    else None,
                    "vwap": self._to_decimal(row["vwap"]),
                    "obv": self._to_decimal(row["obv"]),
                }
            )
        return results

    async def _bulk_upsert(self, results: list[dict[str, Any]]) -> None:
        if not results:
            return

        stock_ids = {r["symbol"]: None for r in results}
        existing = (
            await self.session.execute(
                __import__("sqlalchemy")
                .select(Stock.symbol, Stock.id)
                .where(Stock.symbol.in_(stock_ids.keys()))
            )
        ).fetchall()
        for symbol, stock_id in existing:
            stock_ids[symbol] = stock_id

        for result in results:
            result["stock_id"] = stock_ids[result.pop("symbol")]

        insert_stmt = insert(IndicatorResult).values(results)
        insert_stmt = insert_stmt.on_conflict_do_update(
            constraint="uq_indicator_result_stock_date",
            set_={
                "ema_20": insert_stmt.excluded.ema_20,
                "ema_50": insert_stmt.excluded.ema_50,
                "ema_100": insert_stmt.excluded.ema_100,
                "ema_200": insert_stmt.excluded.ema_200,
                "sma": insert_stmt.excluded.sma,
                "rsi_14": insert_stmt.excluded.rsi_14,
                "macd": insert_stmt.excluded.macd,
                "macd_signal": insert_stmt.excluded.macd_signal,
                "macd_hist": insert_stmt.excluded.macd_hist,
                "stochastic_k": insert_stmt.excluded.stochastic_k,
                "stochastic_d": insert_stmt.excluded.stochastic_d,
                "atr": insert_stmt.excluded.atr,
                "bollinger_upper": insert_stmt.excluded.bollinger_upper,
                "bollinger_middle": insert_stmt.excluded.bollinger_middle,
                "bollinger_lower": insert_stmt.excluded.bollinger_lower,
                "adx": insert_stmt.excluded.adx,
                "supertrend": insert_stmt.excluded.supertrend,
                "supertrend_direction": insert_stmt.excluded.supertrend_direction,
                "vwap": insert_stmt.excluded.vwap,
                "obv": insert_stmt.excluded.obv,
                "updated_at": datetime.now(UTC),
            },
        )
        await self.session.execute(insert_stmt)

    @staticmethod
    def _extract_series(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, pd.Series):
            return value
        if isinstance(value, pd.DataFrame):
            return value.iloc[:, 0]
        return value

    @staticmethod
    def _to_decimal(value: Any) -> Any:
        if value is None or (isinstance(value, float) and (value != value)):
            return None
        try:
            return __import__("decimal").Decimal(str(value))
        except Exception:
            return None
