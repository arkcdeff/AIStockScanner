from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.product_provenance import SYNTHETIC_FIXTURE_SYMBOLS
from app.models.daily_price import DailyPrice
from app.models.indicator_result import IndicatorResult
from app.services.indicator_engine import IndicatorEngine, IndicatorEngineError


@dataclass
class EligibleStockInfo:
    stock_id: int
    symbol: str
    session_count: int
    min_date: date
    max_date: date


@dataclass
class ExcludedStockInfo:
    stock_id: int
    symbol: str
    session_count: int
    reason: str


@dataclass
class BatchExecutionSummary:
    as_of_date: date
    dry_run: bool
    eligible_count: int
    excluded_count: int
    processed_count: int
    successful_count: int
    failed_count: int
    inserted_rows: int
    updated_rows: int
    unchanged_rows: int
    total_indicator_rows_produced: int
    total_daily_prices_read: int
    elapsed_seconds: float
    avg_seconds_per_stock: float
    failures: list[dict[str, Any]] = field(default_factory=list)


class IndicatorBatchService:
    """Service to safely execute broad-market indicator computation in bounded transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.engine = IndicatorEngine(session)

    async def resolve_evaluation_date(self, explicit_date: date | None = None) -> date:
        """Resolve evaluation date: explicit date or max official weekday date in DailyPrice."""
        if explicit_date is not None:
            return explicit_date

        max_date = await self.session.scalar(
            select(func.max(DailyPrice.trade_date)).where(
                text("EXTRACT(DOW FROM daily_prices.trade_date) NOT IN (0, 6)")
            )
        )
        if max_date is None:
            raise ValueError("No daily price data available to resolve evaluation date.")
        return max_date

    async def get_eligible_universe(
        self, as_of_date: date | None = None, min_sessions: int = 200
    ) -> list[EligibleStockInfo]:
        """Return non-fixture EQ stocks with >= min_sessions weekday price history up to as_of_date."""
        target_date = await self.resolve_evaluation_date(as_of_date)

        query = text("""
            SELECT s.id, s.symbol, count(dp.id) as session_count,
                   min(dp.trade_date) as min_date, max(dp.trade_date) as max_date
            FROM stocks s
            JOIN daily_prices dp ON dp.stock_id = s.id
                 AND EXTRACT(DOW FROM dp.trade_date) NOT IN (0, 6)
                 AND dp.trade_date <= :as_of_date
            WHERE s.series = 'EQ'
              AND s.symbol NOT IN :fixtures
            GROUP BY s.id, s.symbol
            HAVING count(dp.id) >= :min_sessions
            ORDER BY s.symbol ASC
        """).bindparams(
            __import__("sqlalchemy").bindparam("fixtures", expanding=True)
        )

        rows = (
            await self.session.execute(
                query,
                {
                    "as_of_date": target_date,
                    "fixtures": list(SYNTHETIC_FIXTURE_SYMBOLS),
                    "min_sessions": min_sessions,
                },
            )
        ).fetchall()

        return [
            EligibleStockInfo(
                stock_id=r[0],
                symbol=r[1],
                session_count=r[2],
                min_date=r[3],
                max_date=r[4],
            )
            for r in rows
        ]

    async def get_excluded_universe(
        self, as_of_date: date | None = None, min_sessions: int = 200
    ) -> list[ExcludedStockInfo]:
        """Return non-fixture EQ stocks with < min_sessions weekday price history up to as_of_date."""
        target_date = await self.resolve_evaluation_date(as_of_date)

        query = text("""
            SELECT s.id, s.symbol, count(dp.id) as session_count
            FROM stocks s
            LEFT JOIN daily_prices dp ON dp.stock_id = s.id
                 AND EXTRACT(DOW FROM dp.trade_date) NOT IN (0, 6)
                 AND dp.trade_date <= :as_of_date
            WHERE s.series = 'EQ'
              AND s.symbol NOT IN :fixtures
            GROUP BY s.id, s.symbol
            HAVING count(dp.id) < :min_sessions
            ORDER BY s.symbol ASC
        """).bindparams(
            __import__("sqlalchemy").bindparam("fixtures", expanding=True)
        )

        rows = (
            await self.session.execute(
                query,
                {
                    "as_of_date": target_date,
                    "fixtures": list(SYNTHETIC_FIXTURE_SYMBOLS),
                    "min_sessions": min_sessions,
                },
            )
        ).fetchall()

        return [
            ExcludedStockInfo(
                stock_id=r[0],
                symbol=r[1],
                session_count=r[2],
                reason=f"Insufficient weekday history ({r[2]} sessions < {min_sessions} required)",
            )
            for r in rows
        ]

    async def run_batch(
        self,
        as_of_date: date | None = None,
        dry_run: bool = False,
        min_sessions: int = 200,
        limit: int | None = None,
        symbols: list[str] | None = None,
        progress_callback: Any = None,
    ) -> BatchExecutionSummary:
        """Run broad-market indicator batch computation with per-stock transaction isolation."""
        target_date = await self.resolve_evaluation_date(as_of_date)
        eligible_universe = await self.get_eligible_universe(target_date, min_sessions=min_sessions)
        excluded_universe = await self.get_excluded_universe(target_date, min_sessions=min_sessions)

        if symbols is not None:
            symbols_set = {s.upper() for s in symbols}
            target_stocks = [s for s in eligible_universe if s.symbol in symbols_set]
        else:
            target_stocks = eligible_universe

        if limit is not None:
            target_stocks = target_stocks[:limit]

        expected_rows = sum(s.session_count for s in target_stocks)

        if dry_run:
            logger.info(
                "DRY RUN: {} eligible stocks, {} excluded stocks, ~{} expected rows for as_of_date={}",
                len(target_stocks),
                len(excluded_universe),
                expected_rows,
                target_date,
            )
            return BatchExecutionSummary(
                as_of_date=target_date,
                dry_run=True,
                eligible_count=len(eligible_universe),
                excluded_count=len(excluded_universe),
                processed_count=len(target_stocks),
                successful_count=len(target_stocks),
                failed_count=0,
                inserted_rows=0,
                updated_rows=0,
                unchanged_rows=0,
                total_indicator_rows_produced=expected_rows,
                total_daily_prices_read=expected_rows,
                elapsed_seconds=0.0,
                avg_seconds_per_stock=0.0,
                failures=[],
            )

        # Pre-identify stocks with existing indicator results for fast comparison
        stocks_with_indicators = set(
            (await self.session.scalars(select(IndicatorResult.stock_id).distinct())).all()
        )

        inserted_count = 0
        updated_count = 0
        unchanged_count = 0
        total_rows_produced = 0
        total_prices_read = 0
        successful = 0
        failures: list[dict[str, Any]] = []

        start_time = time.perf_counter()

        for idx, stock_info in enumerate(target_stocks, 1):
            sym = stock_info.symbol
            stk_id = stock_info.stock_id
            total_prices_read += stock_info.session_count

            try:
                # Existing indicator lookup for stocks with prior results
                existing_map: dict[date, tuple[Any, ...]] = {}
                if stk_id in stocks_with_indicators:
                    existing_rows = (
                        await self.session.execute(
                            text("""
                                SELECT trade_date, ema_20, ema_50, ema_200, rsi_14, macd
                                FROM indicator_results
                                WHERE stock_id = :stock_id AND trade_date <= :as_of_date
                            """),
                            {"stock_id": stk_id, "as_of_date": target_date},
                        )
                    ).fetchall()
                    existing_map = {row[0]: row for row in existing_rows}

                # Compute & upsert
                results = await self.engine.calculate_for_stock(sym, as_of_date=target_date)
                await self.session.commit()

                # Tally inserted / updated / unchanged
                stock_rows = len(results)
                total_rows_produced += stock_rows
                successful += 1

                if not existing_map:
                    inserted_count += stock_rows
                else:
                    for r in results:
                        td = r["trade_date"]
                        if td not in existing_map:
                            inserted_count += 1
                        else:
                            er = existing_map[td]
                            is_diff = False
                            for col_idx, col_name in [
                                (1, "ema_20"),
                                (2, "ema_50"),
                                (3, "ema_200"),
                                (4, "rsi_14"),
                                (5, "macd"),
                            ]:
                                v_old = er[col_idx]
                                v_new = r.get(col_name)
                                if v_old is None and v_new is None:
                                    continue
                                if v_old is not None and v_new is not None:
                                    if abs(float(v_old) - float(v_new)) > 1e-4:
                                        is_diff = True
                                        break
                                else:
                                    is_diff = True
                                    break
                            if is_diff:
                                updated_count += 1
                            else:
                                unchanged_count += 1

                if progress_callback is not None and idx % 100 == 0:
                    progress_callback(idx, len(target_stocks), sym)

            except IndicatorEngineError as exc:
                await self.session.rollback()
                logger.warning("IndicatorEngine error for {}: {}", sym, exc)
                failures.append(
                    {
                        "symbol": sym,
                        "stock_id": stk_id,
                        "reason": str(exc),
                        "stage": "calculation",
                        "status": "DATA_QUALITY_OR_HISTORY_ISSUE",
                    }
                )
            except Exception as exc:
                await self.session.rollback()
                logger.error("Unexpected error calculating indicators for {}: {}", sym, exc)
                failures.append(
                    {
                        "symbol": sym,
                        "stock_id": stk_id,
                        "reason": str(exc),
                        "stage": "execution",
                        "status": "EXECUTION_FAILURE",
                    }
                )

        elapsed = time.perf_counter() - start_time
        avg_time = elapsed / len(target_stocks) if target_stocks else 0.0

        return BatchExecutionSummary(
            as_of_date=target_date,
            dry_run=False,
            eligible_count=len(eligible_universe),
            excluded_count=len(excluded_universe),
            processed_count=len(target_stocks),
            successful_count=successful,
            failed_count=len(failures),
            inserted_rows=inserted_count,
            updated_rows=updated_count,
            unchanged_rows=unchanged_count,
            total_indicator_rows_produced=total_rows_produced,
            total_daily_prices_read=total_prices_read,
            elapsed_seconds=elapsed,
            avg_seconds_per_stock=avg_time,
            failures=failures,
        )
